"""异步 Docker 容器编排：运行单个 benchmark 任务。

特性：
- asyncio + asyncio.create_subprocess_exec，Semaphore 控并发
- 超时发 SIGINT 优雅退出 opencode，回收 opencode.db
- 固定容器名 fdtr-bench-<hash>-<bench>-<tier>-r<NN>
- 余额异常时通过 asyncio.Event 中止后续任务
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fdtr_bench.config import BenchmarkCase, INPUTS_DIR
from fdtr_bench.metrics import build_metrics
from fdtr_bench.models import ModelConfig, ModelProfile, PricingConfig, render_opencode_json
from fdtr_bench.prompts import assemble_prompt
from fdtr_bench.trace import aggregate_errors, aggregate_tool_calls, extract_session, read_session_stats, write_trace_md

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "results"

TIERS = ("minimal", "code", "full")

_ABORT_PATTERNS = [
    "insufficient_quota", "Insufficient Balance", "insufficient balance",
    "You exceeded your current quota", "402 Insufficient Balance",
    "余额不足", "quota exceeded",
]


def _check_balance_error(text: str) -> bool:
    return any(p in text for p in _ABORT_PATTERNS)


def _container_name(session_hash: str, bench_id: str, tier: str, run_idx: int) -> str:
    bid = bench_id.lower()
    return f"fdtr-bench-{session_hash}-{bid}-{tier}-r{run_idx:02d}"


def _session_hash(session_name: str) -> str:
    return hashlib.md5(session_name.encode()).hexdigest()[:8]


def _make_case_dir(session_dir: Path, tier: str, bench_id: str, run_idx: int, repeat: int) -> Path:
    if repeat > 1 and run_idx > 0:
        return session_dir / tier / f"{bench_id}-run{run_idx:02d}"
    return session_dir / tier / bench_id


# ============================================================
# 异步 Docker 辅助
# ============================================================

async def _run_cmd(
    args: list[str], timeout: float | None = None,
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout, stderr
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, b"", b"timeout"


async def _docker_cp_text(text: str, dest: str, work_dir: Path) -> None:
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False, dir=str(work_dir), prefix="fdtr_cp_") as f:
        f.write(text.encode("utf-8"))
        tmp = f.name
    try:
        await _run_cmd(["docker", "cp", tmp, dest])
    finally:
        os.unlink(tmp)


async def _docker_cp_file(src: str, dest: str) -> None:
    await _run_cmd(["docker", "cp", src, dest])


async def _docker_cp_dir(src: str, dest: str) -> None:
    await _run_cmd(["docker", "cp", src + "/.", dest])


async def _docker_cp_out(cid: str, src: str, dest: Path) -> bool:
    if dest.exists():
        return True
    rc, _, _ = await _run_cmd(["docker", "cp", f"{cid}:{src}", str(dest)])
    return rc == 0


async def _docker_cp_dir_out(cid: str, src: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    await _run_cmd(["docker", "cp", f"{cid}:{src}/.", str(dest)])


# ============================================================
# 运行单个任务
# ============================================================

@dataclass
class RunResult:
    bench_id: str
    tier: str
    run_idx: int
    status: str          # OK / TIMEOUT / ERROR / SKIPPED
    line: str
    elapsed: int = 0


async def run_one(
    bench: BenchmarkCase,
    tier: str,
    model_config: ModelConfig,
    session_dir: Path,
    session_hash: str,
    run_idx: int,
    repeat: int,
    abort_event: asyncio.Event,
    print_lock: asyncio.Lock,
    preserve: bool = False,
) -> RunResult:
    """运行单个 bench+tier 任务。"""

    if abort_event.is_set():
        return RunResult(bench.id, tier, run_idx, "SKIPPED",
                         f"{bench.id} [{tier}] r{run_idx:02d} SKIPPED (balance abort)")

    profile = model_config.get_profile()
    image = model_config.get_image(tier)
    cname = _container_name(session_hash, bench.id, tier, run_idx)
    prompt = assemble_prompt(bench, tier)
    timeout = bench.timeout or 600
    local_work = Path(tempfile.mkdtemp(prefix="fdtr_bench_"))

    case_dir = _make_case_dir(session_dir, tier, bench.id, run_idx, repeat)
    case_dir.mkdir(parents=True, exist_ok=True)

    elapsed = 0
    timed_out = False
    db_recovered = False

    try:
        # 1. 创建容器（固定名字，先清理同名残留）
        await _run_cmd(["docker", "rm", "-f", cname])
        env_args: list[str] = []
        for k, v in profile.opencode_env.items():
            env_args += ["-e", f"{k}={v}"]
        if profile.api_key_env and profile.api_key:
            env_args += ["-e", f"{profile.api_key_env}={profile.api_key}"]
        env_args += ["-e", "TERM=dumb"]

        create_cmd = ["docker", "create", "--name", cname] + env_args + [image, "tail", "-f", "/dev/null"]
        rc, out, err = await _run_cmd(create_cmd)
        if rc != 0:
            return RunResult(bench.id, tier, run_idx, "ERROR",
                             f"{bench.id} [{tier}] r{run_idx:02d} ERROR: docker create: {err.decode()[:200]}")

        # 2. 启动
        await _run_cmd(["docker", "start", cname])

        # 3. 准备目录
        await _run_cmd(["docker", "exec", cname, "mkdir", "-p",
                        "/app/data", "/app/tasks", "/app/artifacts"])

        # 4. 注入 opencode.json（openai-compatible provider）
        if profile.provider == "openai-compatible":
            oc_json = render_opencode_json(profile)
            await _docker_cp_text(oc_json, f"{cname}:/app/opencode.json", local_work)

        # 5. cp 输入文件
        await _docker_cp_text(prompt, f"{cname}:/app/prompt.txt", local_work)
        sh = _build_run_script(profile.variant, bench.outputs)
        await _docker_cp_text(sh, f"{cname}:/app/run.sh", local_work)

        if bench.data_file:
            src = str(INPUTS_DIR / bench.data_file)
            if Path(src).exists():
                await _docker_cp_file(src, f"{cname}:/app/data/{bench.data_file}")

        for d in bench.data_dirs:
            src_dir = str(INPUTS_DIR / d)
            if Path(src_dir).is_dir():
                await _docker_cp_dir(src_dir, f"{cname}:/app/data/{d}")

        # 6. 运行 opencode（带超时 + SIGINT 优雅退出）
        t0 = time.time()
        rc, stdout, stderr = await _run_docker_exec_with_sigint(
            cname, ["bash", "/app/run.sh"], timeout=timeout, abort_event=abort_event,
        )
        elapsed = int(time.time() - t0)
        timed_out = rc == -1

        # 扫描输出检测余额错误
        out_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        if _check_balance_error(out_text):
            abort_event.set()
            async with print_lock:
                print(f"\n  *** 余额错误 {bench.id} [{tier}] r{run_idx:02d} — 中止后续任务\n")

        # 保存 docker exec stdout 用于调试
        (case_dir / "debug.log").write_bytes(stdout or b"")

        # 7. 回收 opencode.db（即使超时也尝试，SIGINT 已让 opencode 优雅退出）
        db_src = "/root/.local/share/opencode/opencode.db"
        db_dest = case_dir / "opencode.db"
        # 先在容器内做 WAL checkpoint：超时强 kill 时 WAL 未 checkpoint，
        # 数据只在 -wal 里，而后处理用 immutable 只读打开（不读 WAL）
        await _run_cmd(["docker", "exec", cname, "python3", "-c",
                        "import sqlite3; "
                        f"sqlite3.connect('{db_src}').execute('PRAGMA wal_checkpoint(TRUNCATE)')"])
        db_recovered = await _docker_cp_out(cname, db_src, db_dest)
        for suffix in ("-wal", "-shm"):
            await _docker_cp_out(cname, f"{db_src}{suffix}", case_dir / f"opencode.db{suffix}")

        # 8. 回收 artifacts
        await _docker_cp_dir_out(cname, "/app/artifacts", case_dir / "artifacts")

    except Exception as e:
        async with print_lock:
            print(f"  {bench.id} [{tier}] r{run_idx:02d} EXCEPTION: {e}")
    finally:
        # 9. 清理容器（--preserve 时保留）
        if preserve:
            async with print_lock:
                print(f"  [保留容器] {cname}")
        else:
            await _run_cmd(["docker", "rm", "-f", cname])
        shutil.rmtree(local_work, ignore_errors=True)

    # 10. 后处理：trace.md / metrics.json / prompt.txt / groundtruth.json
    (case_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    gt_dump = [
        {"file": o.file, "compare": o.compare,
         "groundtruth": o.groundtruth, "groundtruth_csv": o.groundtruth_csv,
         "tolerance": o.tolerance}
        for o in bench.outputs
    ]
    (case_dir / "groundtruth.json").write_text(json.dumps(gt_dump, indent=2))

    db_path = case_dir / "opencode.db"
    write_trace_md(db_path, case_dir / "trace.md")

    metrics: dict = {
        "benchmark_id": bench.id,
        "tier": tier,
        "run_index": run_idx,
        "repeat_count": repeat,
        "elapsed_seconds": elapsed,
        "timed_out": timed_out,
        "db_recovered": db_recovered,
    }

    if db_path.exists():
        ts = read_session_stats(db_path)
        metrics["tool_calls"] = aggregate_tool_calls(db_path)
        metrics["errors_detected"] = aggregate_errors(db_path)

        if profile.pricing and profile.pricing != PricingConfig():
            p = profile.pricing
            total_out = ts["tokens_output"] + ts["tokens_reasoning"]
            cost = (ts["tokens_input"] * p.input
                  + ts["tokens_cache_read"] * p.cache_read
                  + total_out * p.output) / 1_000_000
            ts["cost_usd"] = round(cost, 4)
        metrics["token_stats"] = ts

    if not timed_out:
        metrics.update(build_metrics(case_dir, bench.outputs))
    else:
        metrics["fitted_values"] = {}
        metrics["gt_match"] = False

    (case_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))

    # 状态行
    status = "TIMEOUT" if timed_out else "OK"
    if not timed_out:
        if metrics.get("gt_match") is True:
            status += "/PASS"
        elif metrics.get("gt_match") is False:
            status += "/FAIL"

    tokens = metrics.get("token_stats") or {}
    tok_str = ""
    if tokens:
        tok_str = (f" in={tokens.get('tokens_input',0)} cr={tokens.get('tokens_cache_read',0)}"
                   f" out={tokens.get('tokens_output',0)} re={tokens.get('tokens_reasoning',0)}"
                   f" cost=${tokens.get('cost_usd',0)}")
    line = f"{bench.id} [{tier}] r{run_idx:02d} {status} {elapsed}s{tok_str}".strip()

    return RunResult(bench.id, tier, run_idx, status, line, elapsed)


def _build_run_script(variant: str, outputs: list) -> str:
    """生成容器内 run.sh：跑 opencode + 按 outputs 契约收集回收文件。"""
    cp_lines = "".join(
        f"cp /app/{o.file} /app/artifacts/ 2>/dev/null || true\n" for o in outputs
    )
    return (
        "#!/bin/bash\n"
        "PROMPT=$(cat /app/prompt.txt)\n"
        f'opencode run --variant {variant} "$PROMPT" 2>&1\n'
        "mkdir -p /app/artifacts\n"
        + cp_lines
        + "sync\n"
    )


async def _run_docker_exec_with_sigint(
    cname: str,
    cmd: list[str],
    timeout: int,
    abort_event: asyncio.Event,
) -> tuple[int, bytes, bytes]:
    """运行 docker exec，超时发 SIGINT 优雅退出 opencode。

    流程：
      1. docker exec 跑 run.sh，带 timeout
      2. 超时：docker exec 发 SIGINT 给 opencode run 进程
      3. 再等 60s 让 opencode 优雅退出
      4. 仍超时则强 kill，返回 -1
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", cname, *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout, stderr
    except asyncio.TimeoutError:
        # 超时：发 SIGINT 让 opencode 优雅退出
        await _run_cmd(["docker", "exec", cname, "bash", "-c",
                        "pkill -SIGINT -f 'opencode run' 2>/dev/null || true"])
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            return proc.returncode or 0, stdout, stderr
        except asyncio.TimeoutError:
            # 仍不退出，强 kill
            proc.kill()
            await proc.wait()
            return -1, b"", b"timeout-sigint"


# ============================================================
# 会话编排
# ============================================================

async def run_session(
    benches: list[BenchmarkCase],
    tiers: list[str],
    model_config: ModelConfig,
    bench_filter: list[str] | None,
    repeat: int,
    max_workers: int,
    tag: str = "",
    preserve: bool = False,
) -> None:
    """编排一个会话的所有任务。"""

    from datetime import datetime
    session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    if tag:
        session_name += f"_{tag}"
    session_dir = RESULTS_DIR / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    sh = _session_hash(session_name)

    # 过滤任务
    filtered = []
    for b in benches:
        if bench_filter and b.id not in bench_filter:
            continue
        # 数据文件校验
        if b.data_file and not (INPUTS_DIR / b.data_file).exists():
            print(f"[SKIP] {b.id}: 数据文件不存在 {b.data_file}")
            continue
        ok = True
        for d in b.data_dirs:
            if not (INPUTS_DIR / d).is_dir():
                print(f"[SKIP] {b.id}: 数据目录不存在 {d}")
                ok = False
                break
        if not ok:
            continue
        filtered.append(b)

    # 生成任务列表
    tasks = []
    for b in filtered:
        for tier in tiers:
            for r in range(1, repeat + 1):
                tasks.append((b, tier, r))

    if not tasks:
        print("无可运行任务。")
        return

    # 写 meta.json
    (session_dir / "meta.json").write_text(json.dumps({
        "session": session_name,
        "profile": model_config.default,
        "tiers": tiers,
        "bench_filter": bench_filter or [],
        "repeat": repeat,
        "max_workers": max_workers,
        "image_tags": model_config.image_tags,
        "time": datetime.now().isoformat(),
    }, indent=2), encoding="utf-8")

    print(f"会话: {session_name}")
    print(f"任务数: {len(tasks)}（{len(filtered)} benchmarks × {len(tiers)} tiers × {repeat} reps）"
          f" 并发: {max_workers}")
    print(f"目录: {session_dir}")
    print()

    abort_event = asyncio.Event()
    print_lock = asyncio.Lock()
    sem = asyncio.Semaphore(max_workers)

    # 错峰启动：第 i 个任务延迟 i 秒再去抢 semaphore，
    # 保证每秒最多新启动一个容器，避免 Docker daemon 被并发 create 冲击。
    LAUNCH_INTERVAL = 1.0

    async def _bounded(idx: int, b, tier, r):
        await asyncio.sleep(idx * LAUNCH_INTERVAL)
        if abort_event.is_set():
            return RunResult(b.id, tier, r, "SKIPPED",
                             f"{b.id} [{tier}] r{r:02d} SKIPPED (balance abort)")
        async with sem:
            return await run_one(b, tier, model_config, session_dir, sh, r, repeat,
                                  abort_event, print_lock, preserve)

    coros = [_bounded(i, b, tier, r) for i, (b, tier, r) in enumerate(tasks)]

    results = await asyncio.gather(*coros, return_exceptions=True)

    # 打印结果
    completed = 0
    for r in results:
        if isinstance(r, Exception):
            completed += 1
            print(f"  [{completed}/{len(tasks)}] ERROR: {r}")
            continue
        completed += 1
        async with print_lock:
            print(f"  [{completed}/{len(tasks)}] {r.line}")

    print(f"\n完成。结果: {session_dir}")
