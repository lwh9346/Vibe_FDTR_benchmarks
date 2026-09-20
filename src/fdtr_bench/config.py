"""任务定义加载器：扫描 tasks/*.yaml 组装 BenchmarkCase 列表。"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "tasks"
INPUTS_DIR = ROOT / "inputs"


def _csv_header(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return next(csv.reader(f), [])


@dataclass
class OutputSpec:
    """单个回收文件的输出契约。

    - compare="keyvalue": 读 YAML，按 groundtruth 的 keys 对比标量值（tolerance 必填）
    - compare="curve":    读 CSV，与 groundtruth_csv 做频率插值逐点对比（tolerance 必填）
    - compare="artifact": 仅回收文件，不评分（description 必填，用于拼装 prompt）
    """
    file: str
    compare: str = "keyvalue"               # keyvalue | curve | artifact
    tolerance: float | None = None          # keyvalue/curve 必填
    groundtruth: dict = field(default_factory=dict)
    groundtruth_csv: str = ""               # curve 用，指向 inputs/ 下 gt csv
    description: str = ""                    # artifact 必填，描述该文件用途
    units: dict[str, str] = field(default_factory=dict)  # keyvalue 必填，每个 gt key 的单位


@dataclass
class BenchmarkCase:
    id: str
    description: str = ""
    data_file: str = ""
    data_dirs: list[str] = field(default_factory=list)
    prompt: str = ""
    timeout: int | None = None
    prompt_prefix: dict[str, str] = field(default_factory=dict)
    prompt_suffix: dict[str, str] = field(default_factory=dict)
    outputs: list[OutputSpec] = field(default_factory=list)

    @property
    def has_groundtruth(self) -> bool:
        return any(o.groundtruth or o.groundtruth_csv for o in self.outputs)


def _load_outputs(raw: list | None, task_name: str) -> list[OutputSpec]:
    specs: list[OutputSpec] = []
    for item in raw or []:
        file = item.get("file", "?")
        compare = item.get("compare", "keyvalue")
        if compare == "artifact":
            if not item.get("description"):
                raise ValueError(
                    f"任务 {task_name} 的 artifact output '{file}' 缺少必填的 description 字段"
                )
            tolerance = None
        else:
            if "tolerance" not in item:
                raise ValueError(
                    f"任务 {task_name} 的 output '{file}' 缺少必填的 tolerance 字段"
                )
            tolerance = float(item["tolerance"])
        groundtruth = dict(item.get("groundtruth") or {})
        units = dict(item.get("units") or {})
        if compare == "keyvalue" and groundtruth:
            missing = [k for k in groundtruth if k not in units]
            if missing:
                raise ValueError(
                    f"任务 {task_name} 的 output '{file}' 缺少这些 groundtruth key 的单位(units): {missing}"
                )
        elif compare == "curve":
            if not units:
                raise ValueError(
                    f"任务 {task_name} 的 curve output '{file}' 缺少必填的 units 字段（每列一个单位）"
                )
            cols = _csv_header(INPUTS_DIR / item.get("groundtruth_csv", ""))
            missing = [c for c in cols if c not in units]
            if missing:
                raise ValueError(
                    f"任务 {task_name} 的 curve output '{file}' 缺少这些列的单位(units): {missing}"
                )
        specs.append(OutputSpec(
            file=item["file"],
            compare=compare,
            tolerance=tolerance,
            groundtruth=groundtruth,
            groundtruth_csv=item.get("groundtruth_csv", ""),
            description=item.get("description", ""),
            units=units,
        ))
    return specs


def _load_one(path: Path) -> BenchmarkCase:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    bench_id = raw.get("id") or path.stem
    timeout = raw.get("timeout")
    if not timeout:
        raise ValueError(f"任务 {path.name} 缺少必填的 timeout 字段")
    return BenchmarkCase(
        id=str(bench_id),
        description=raw.get("description", ""),
        data_file=raw.get("data_file", ""),
        data_dirs=list(raw.get("data_dirs") or []),
        prompt=raw.get("prompt", ""),
        timeout=int(timeout),
        prompt_prefix=dict(raw.get("prompt_prefix") or {}),
        prompt_suffix=dict(raw.get("prompt_suffix") or {}),
        outputs=_load_outputs(raw.get("outputs"), path.name),
    )


def load_benchmarks() -> list[BenchmarkCase]:
    """加载 tasks/ 下全部任务，按 id 排序。"""
    if not TASKS_DIR.is_dir():
        raise FileNotFoundError(f"任务目录不存在: {TASKS_DIR}")
    cases = [_load_one(p) for p in sorted(TASKS_DIR.glob("*.yaml"))]
    return cases


def load_benchmark(bench_id: str) -> BenchmarkCase:
    """按 id 加载单个任务。"""
    path = TASKS_DIR / f"{bench_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"任务文件不存在: {path}")
    return _load_one(path)


BENCHMARKS = load_benchmarks()
BENCHMARK_IDS = [b.id for b in BENCHMARKS]
BENCHMARK_BY_ID = {b.id: b for b in BENCHMARKS}
