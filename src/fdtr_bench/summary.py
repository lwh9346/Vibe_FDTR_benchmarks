"""结果汇总：扫描 results/<session>/<tier>/<bench>，生成 markdown 表。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "results"


def _detect_bench_id(name: str) -> str:
    m = re.search(r'(L[13]-[A-Z]\d+)', name)
    return m.group(1) if m else name


def generate_summary(
    session_filter: str = "",
    bench_filter: list[str] | None = None,
) -> str:
    """生成汇总 markdown。session_filter 指定会话目录名，缺省读全部。"""

    rows: list[dict] = []
    if not RESULTS_DIR.is_dir():
        return "无结果。"
    sessions = sorted([d for d in RESULTS_DIR.iterdir() if d.is_dir()])
    if session_filter:
        sessions = [d for d in sessions if d.name == session_filter or d.name.startswith(session_filter)]

    for sess in sessions:
        for tier_dir in sorted(sess.iterdir()):
            if not tier_dir.is_dir() or tier_dir.name not in ("minimal", "code", "full"):
                continue
            tier = tier_dir.name
            for case_dir in sorted(tier_dir.iterdir()):
                if not case_dir.is_dir():
                    continue
                bench_id = _detect_bench_id(case_dir.name)
                if bench_filter and bench_id not in bench_filter:
                    continue
                mf = case_dir / "metrics.json"
                if not mf.exists():
                    continue
                m = json.loads(mf.read_text(encoding="utf-8"))
                rows.append({
                    "session": sess.name,
                    "bench_id": bench_id,
                    "tier": tier,
                    "run": case_dir.name,
                    "elapsed": m.get("elapsed_seconds", 0),
                    "timed_out": m.get("timed_out", False),
                    "gt_match": m.get("gt_match"),
                    "tokens": m.get("token_stats") or {},
                    "has_trace": (case_dir / "trace.md").exists(),
                })

    if not rows:
        return "无结果。"

    lines = [
        "# FDTR Benchmark 汇总",
        "",
        f"共 {len(rows)} 次运行，跨 {len(set(r['tier'] for r in rows))} 个层级、{len(set(r['session'] for r in rows))} 个会话",
        "",
        "| Session | ID | Tier | Elapsed | Status | Cost | Tok In | CacheR | Tok Out | Reason | GT |",
        "|---------|-----|------|---------|--------|------|--------|--------|---------|--------|----|",
    ]

    for r in sorted(rows, key=lambda x: (x["session"], x["bench_id"], x["tier"])):
        status = "TIMEOUT" if r["timed_out"] else "OK"
        if r["gt_match"] is True:
            status += "/PASS"
        elif r["gt_match"] is False:
            status += "/FAIL"
        cost = r["tokens"].get("cost_usd", 0)
        t_in = r["tokens"].get("tokens_input", 0)
        t_cr = r["tokens"].get("tokens_cache_read", 0)
        t_out = r["tokens"].get("tokens_output", 0)
        t_re = r["tokens"].get("tokens_reasoning", 0)
        gt_icon = "—" if r["gt_match"] is None else ("✅" if r["gt_match"] else "❌")
        lines.append(
            f"| {r['session']} | {r['bench_id']} | {r['tier']} | {r['elapsed']}s | {status} | "
            f"${cost:.3f} | {t_in} | {t_cr} | {t_out} | {t_re} | {gt_icon} |"
        )

    return "\n".join(lines)
