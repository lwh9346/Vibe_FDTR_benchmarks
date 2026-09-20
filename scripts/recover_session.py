"""离线恢复某个 session 目录下缺失的 trace.md / metrics.json。

背景：结果目录在 NFS 上，opencode.db 是 WAL 模式，runner 后处理阶段
用普通 sqlite 连接打开会触发 WAL 恢复 + NFS 锁，报 "disk I/O error" /
"locking protocol"，导致 run 本身成功但 metrics.json 未生成。

恢复方法：把 opencode.db(+-wal/-shm) 复制到本地磁盘（/tmp），
在本地副本上正常打开（sqlite 自动做 WAL recovery），再调用
fdtr_bench.trace / fdtr_bench.metrics 的现有函数完成打分。

用法：
    uv run python scripts/recover_session.py results/20260908_200725_ds-v4-flash
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fdtr_bench.config import load_benchmark
from fdtr_bench.metrics import build_metrics
from fdtr_bench.trace import (
    aggregate_errors,
    aggregate_tool_calls,
    read_session_stats,
    write_trace_md,
)

# DeepSeek 官网现行价（2026-09，$/1M tokens），off-peak / peak
OFFICIAL_PRICE = {
    "off_peak": {"input": 0.22, "cache_read": 0.007, "output": 0.66},
    "peak": {"input": 0.44, "cache_read": 0.014, "output": 1.32},
}


def _official_costs(ts: dict) -> dict:
    out = {}
    for k, p in OFFICIAL_PRICE.items():
        cost = (
            (ts.get("tokens_input", 0) + ts.get("tokens_cache_write", 0)) * p["input"]
            + ts.get("tokens_cache_read", 0) * p["cache_read"]
            + (ts.get("tokens_output", 0) + ts.get("tokens_reasoning", 0)) * p["output"]
        ) / 1_000_000
        out[f"cost_usd_official_{k}"] = round(cost, 4)
    return out


def _elapsed_estimate(db_path: Path) -> int | None:
    """用 message 表的 time_created 跨度估算 agent 活跃时长（秒）。"""
    try:
        db = sqlite3.connect(str(db_path))
        row = db.execute("SELECT MIN(time_created), MAX(time_created) FROM message").fetchone()
        db.close()
        if row and row[0] and row[1]:
            return int((row[1] - row[0]) / 1000)
    except Exception:
        pass
    return None


def recover_case(case_dir: Path) -> str:
    """恢复单个 case 目录，返回状态行。"""
    m = re.match(r"(.+)-run(\d+)$", case_dir.name)
    bench_id, run_idx = m.group(1), int(m.group(2))
    tier = case_dir.parent.name
    bench = load_benchmark(bench_id)

    db_src = case_dir / "opencode.db"
    metrics: dict = {
        "benchmark_id": bench_id,
        "tier": tier,
        "run_index": run_idx,
        "recovered": True,
        "db_recovered": db_src.exists(),
    }

    if db_src.exists():
        with tempfile.TemporaryDirectory(prefix="fdtr-recover-") as td:
            local_db = Path(td) / "opencode.db"
            for suffix in ("", "-wal", "-shm"):
                src = case_dir / f"opencode.db{suffix}"
                if src.exists():
                    shutil.copy2(src, Path(td) / f"opencode.db{suffix}")

            # 本地副本上先做 WAL checkpoint：部分 run 的 opencode 未优雅退出，
            # 数据还在 WAL 里；checkpoint 后主库文件才是完整的
            #（trace 模块统一用 immutable 只读打开，不会读 WAL）
            wal_db = sqlite3.connect(str(local_db))
            wal_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            wal_db.close()

            write_trace_md(local_db, case_dir / "trace.md")
            ts = read_session_stats(local_db)
            metrics["tool_calls"] = aggregate_tool_calls(local_db)
            metrics["errors_detected"] = aggregate_errors(local_db)
            elapsed = _elapsed_estimate(local_db)

        if ts:
            ts.update(_official_costs(ts))
            metrics["token_stats"] = ts

    metrics["elapsed_seconds"] = elapsed if db_src.exists() else None
    timed_out = bool(elapsed and bench.timeout and elapsed >= bench.timeout)
    metrics["timed_out"] = timed_out
    metrics["timed_out_note"] = "estimated from db message time span" if db_src.exists() else "no db"

    if not timed_out:
        metrics.update(build_metrics(case_dir, bench.outputs))
    else:
        metrics["fitted_values"] = {}
        metrics["gt_match"] = False

    (case_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))

    status = "TIMEOUT?" if timed_out else "OK"
    gm = metrics.get("gt_match")
    status += "/PASS" if gm is True else ("/FAIL" if gm is False else "")
    return f"{bench_id} [{tier}] r{run_idx:02d} {status}"


def main() -> None:
    session = Path(sys.argv[1])
    case_dirs = sorted(
        d for d in session.glob("*/*") if d.is_dir() and not (d / "metrics.json").exists()
    )
    print(f"session: {session}, 待恢复: {len(case_dirs)} 个 case")

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        for case_dir, result in zip(case_dirs, ex.map(_safe_recover, case_dirs)):
            if result.startswith("ERROR"):
                fail += 1
                print(f"  {case_dir.name}: {result}")
            else:
                ok += 1
    print(f"完成: 恢复 {ok} 个, 失败 {fail} 个")


def _safe_recover(case_dir: Path) -> str:
    try:
        return recover_case(case_dir)
    except Exception as e:
        return f"ERROR: {e}"


if __name__ == "__main__":
    main()
