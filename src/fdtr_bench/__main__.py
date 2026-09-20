"""CLI 入口：uv run fdtr-bench [run|summary|clean]"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from fdtr_bench.config import BENCHMARKS, BENCHMARK_BY_ID
from fdtr_bench.runner import run_session
from fdtr_bench.models import load_model_config


def _run(args: argparse.Namespace) -> None:
    mc = load_model_config()
    if args.profile:
        mc.default = args.profile
    benches = list(BENCHMARKS)
    # 单个任务
    if args.bench:
        benches = [BENCHMARK_BY_ID[b] for b in args.bench if b in BENCHMARK_BY_ID]
    if not benches:
        print("无匹配任务。")
        sys.exit(1)
    asyncio.run(run_session(
        benches=benches,
        tiers=args.tier or ["minimal", "code", "full"],
        model_config=mc,
        bench_filter=args.bench,
        repeat=args.repeat,
        max_workers=args.jobs,
        tag=args.tag or "",
        preserve=args.preserve,
    ))


def _summary(args: argparse.Namespace) -> None:
    from fdtr_bench.summary import generate_summary
    md = generate_summary(args.session, args.bench)
    if args.write:
        Path(args.write).write_text(md, encoding="utf-8")
        print(f"已写入 {args.write}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(md)


def _clean(args: argparse.Namespace) -> None:
    import subprocess
    res = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=fdtr-bench-", "--format", "{{.Names}}\t{{.Status}}"],
        capture_output=True, text=True,
    )
    lines = [l for l in res.stdout.strip().splitlines() if l.strip()]
    if not lines:
        print("无 fdtr-bench-* 残留容器。")
        return
    print(f"找到 {len(lines)} 个残留容器：")
    for l in lines:
        print(f"  {l}")
    if args.dry_run:
        print("\n(--dry-run，未实际删除)")
        return
    for l in lines:
        name = l.split("\t")[0]
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    print(f"已清理 {len(lines)} 个容器。")


def main() -> None:
    parser = argparse.ArgumentParser("fdtr-bench", description="FDTR AI Agent Benchmark")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="运行 benchmark 任务")
    p_run.add_argument("-p", "--profile", default=None, help="运行模型 profile（缺省读 models.yaml default）")
    p_run.add_argument("-s", "--tier", choices=["minimal", "code", "full"], action="append",
                       help="配置层级（可重复，默认全部）")
    p_run.add_argument("-b", "--bench", action="append", help="benchmark ID 过滤（可重复）")
    p_run.add_argument("-j", "--jobs", type=int, default=1, help="并发数（默认 1 顺序）")
    p_run.add_argument("--repeat", type=int, default=1, help="每任务重复次数")
    p_run.add_argument("--tag", default="", help="会话标签（附加到会话目录名）")
    p_run.add_argument("--preserve", action="store_true", help="保留容器不删除（调试用）")
    p_run.set_defaults(func=_run)

    # summary
    p_sum = sub.add_parser("summary", help="生成结果汇总表")
    p_sum.add_argument("session", nargs="?", default="", help="会话目录名（缺省读全部）")
    p_sum.add_argument("-b", "--bench", action="append", help="benchmark ID 过滤")
    p_sum.add_argument("--write", default="", help="写入文件（缺省打印到 stdout）")
    p_sum.set_defaults(func=_summary)

    # clean
    p_clean = sub.add_parser("clean", help="清理本项目残留的 docker 容器")
    p_clean.add_argument("--dry-run", action="store_true", help="仅预览，不实际删除")
    p_clean.set_defaults(func=_clean)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
