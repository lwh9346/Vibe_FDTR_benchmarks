"""结果回收与 groundtruth 对比，由 task.yaml 的 outputs 契约驱动。

- keyvalue: 读 artifacts/<file> 的 YAML（平铺 key: value），按 groundtruth keys 对比
- curve:    读 artifacts/<file> 的 CSV，与 groundtruth_csv 做频率插值逐点对比
"""

from __future__ import annotations

from pathlib import Path

import yaml

from fdtr_bench.config import INPUTS_DIR, OutputSpec


def _load_yaml_flat(path: Path) -> dict[str, float]:
    """读 YAML 文件，提取平铺的 {key: float}。非数值键跳过。"""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    flat: dict[str, float] = {}
    for k, v in data.items():
        try:
            flat[str(k)] = float(v)
        except (ValueError, TypeError):
            continue
    return flat


def compare_with_groundtruth(
    fitted: dict[str, float],
    groundtruth: dict[str, float],
    tolerance: float = 0.01,
) -> tuple[bool, dict[str, float]]:
    """对比 fitted 与 groundtruth，返回 (全通过, {param: 误差率})。"""
    if not groundtruth:
        return True, {}

    errors: dict[str, float] = {}
    all_passed = True
    for key, gt_val in groundtruth.items():
        if key not in fitted:
            errors[key] = 1.0
            all_passed = False
            continue
        fv = fitted[key]
        if gt_val == 0:
            err = abs(fv)
        else:
            err = abs(fv - gt_val) / abs(gt_val)
        errors[key] = err
        if err > tolerance:
            all_passed = False
    return all_passed, errors


def compare_sensitivity_curves(
    actual_csv: Path | None,
    gt_path: Path,
    tolerance: float = 0.05,
    zero_threshold: float = 0.01,
) -> tuple[bool, float]:
    """对比 sensitivity CSV 与 groundtruth 曲线，返回 (是否通过, 最大相对误差)。"""
    import numpy as np

    if actual_csv is None or not actual_csv.exists() or not gt_path.exists():
        return False, float("inf")

    try:
        actual_data = np.loadtxt(str(actual_csv), delimiter=",", skiprows=1)
        gt_data = np.loadtxt(str(gt_path), delimiter=",", skiprows=1)
    except Exception:
        return False, float("inf")

    if actual_data.ndim != 2 or gt_data.ndim != 2:
        return False, float("inf")
    if actual_data.shape[1] != gt_data.shape[1]:
        return False, float("inf")

    gt_freq = gt_data[:, 0]
    actual_freq = actual_data[:, 0]
    n_params = gt_data.shape[1] - 1

    max_err = 0.0
    for j in range(n_params):
        gt_curve = gt_data[:, j + 1]
        actual_curve = np.interp(gt_freq, actual_freq, actual_data[:, j + 1])
        for i in range(len(gt_freq)):
            gtv = abs(gt_curve[i])
            if gtv < zero_threshold:
                continue
            err = abs(actual_curve[i] - gt_curve[i]) / gtv
            if err > max_err:
                max_err = err

    return max_err <= tolerance, max_err


def build_metrics(case_dir: Path, outputs: list[OutputSpec]) -> dict:
    """遍历 outputs 契约，回收文件并对比 groundtruth。

    返回 dict 含：
      - fitted_values: 合并所有 keyvalue 输出
      - outputs: [{file, compare, match, errors/max_err}]
      - gt_match: 所有有 gt 的 output 是否全通过（无 gt 时为 None）
    """
    artifacts = case_dir / "artifacts"
    fitted_all: dict[str, float] = {}
    output_results: list[dict] = []
    all_match = True
    has_gt = False

    for spec in outputs:
        fpath = artifacts / spec.file
        entry: dict = {"file": spec.file, "compare": spec.compare}

        if spec.compare == "artifact":
            entry["match"] = None
            entry["collected"] = fpath.exists()
        elif spec.compare == "curve":
            has_gt = True
            gt_path = INPUTS_DIR / spec.groundtruth_csv
            passed, max_err = compare_sensitivity_curves(fpath, gt_path, spec.tolerance)
            entry["match"] = bool(passed)
            entry["max_err"] = round(float(max_err), 6) if max_err != float("inf") else None
            if not passed:
                all_match = False
        else:
            fitted = _load_yaml_flat(fpath)
            fitted_all.update(fitted)
            if spec.groundtruth:
                has_gt = True
                passed, errors = compare_with_groundtruth(fitted, spec.groundtruth, spec.tolerance)
                entry["match"] = passed
                entry["errors"] = {k: round(v, 6) for k, v in errors.items()}
                if not passed:
                    all_match = False
            else:
                entry["match"] = None

        output_results.append(entry)

    return {
        "fitted_values": fitted_all,
        "outputs": output_results,
        "gt_match": all_match if has_gt else None,
    }
