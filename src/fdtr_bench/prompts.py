"""prompt 组装：全局层级前后缀 + 按 outputs 契约自动追加结果提交说明。

优先级：任务级 prompt_prefix/prompt_suffix 覆盖 configs/prompts.yaml 的全局 tier 默认。
outputs 契约后缀根据 task.yaml 的 outputs 自动生成，告诉模型把结果写到指定文件。
"""

from __future__ import annotations

import csv
from pathlib import Path

import yaml

from fdtr_bench.config import BenchmarkCase, OutputSpec, INPUTS_DIR

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_PATH = ROOT / "configs" / "prompts.yaml"

_raw = yaml.safe_load(PROMPTS_PATH.read_text(encoding="utf-8")) or {}
TIER_PREFIX: dict[str, str] = dict(_raw.get("tier_prefix") or {})
TIER_SUFFIX: dict[str, str] = dict(_raw.get("tier_suffix") or {})


def _csv_header(gt_csv: str) -> list[str]:
    """读 inputs/<gt_csv> 的表头列名。"""
    path = INPUTS_DIR / gt_csv
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration:
            return []


def _output_contract(spec: OutputSpec) -> str:
    """为单个 output 生成提交说明。"""
    if spec.compare == "artifact":
        return (
            f"After completing the task, save {spec.description} to /app/{spec.file}. "
            f"This file will be collected as an artifact."
        )

    if spec.compare == "curve":
        cols = _csv_header(spec.groundtruth_csv)
        if cols:
            def _cu(c: str) -> str:
                return f"{c} (in {spec.units.get(c, '')})"
            freq_col = _cu(cols[0])
            value_cols = ", ".join(_cu(c) for c in cols[1:])
            return (
                f"After completing the task, save the resulting curves to /app/{spec.file} "
                f"as a CSV file with a header row. The first column must be the frequency "
                f'axis "{freq_col}", and the remaining columns must be exactly: {value_cols}. '
                f"This file will be read automatically for grading."
            )
        return (
            f"After completing the task, save the resulting curves to /app/{spec.file} "
            f"as a CSV file with a header row (frequency in the first column). "
            f"This file will be read automatically for grading."
        )

    # keyvalue
    keys = ", ".join(f"{k} (in {spec.units.get(k, '')})" for k in spec.groundtruth)
    return (
        f"After completing the task, write your final results to /app/{spec.file} "
        f"as a YAML file mapping each of the following keys to its numeric value, "
        f"using exactly the specified unit for each: {keys}. "
        f"This file will be read automatically for grading."
    )


def assemble_prompt(bench: BenchmarkCase, tier: str) -> str:
    """组装最终 prompt：prefix + bench.prompt + tier_suffix + outputs 契约。"""
    prefix = bench.prompt_prefix.get(tier, TIER_PREFIX.get(tier, ""))
    suffix = bench.prompt_suffix.get(tier, TIER_SUFFIX.get(tier, ""))

    contracts = [_output_contract(o) for o in bench.outputs]
    contract_block = ("\n\n" + "\n".join(contracts)) if contracts else ""

    return f"{prefix}{bench.prompt}{suffix}{contract_block}"
