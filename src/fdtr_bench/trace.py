"""从 opencode.db 提取结构化 trace，并生成人读 markdown。

opencode.db 是唯一真相源（不再从 stdout tee trace.log）。
- extract_session: 读 session + part 表，返回结构化 dict
- render_trace_md: 转为 markdown（完整工具输出，<details> 折叠）
- read_session_stats / count_tool_calls / detect_errors: 供 metrics 复用
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def _connect(db_path: Path) -> sqlite3.Connection:
    # 只读 + immutable 打开：opencode.db 是 WAL 模式且结果目录在 NFS 上，
    # 普通连接会触发 WAL recovery + NFS 文件锁，报 "disk I/O error" /
    # "locking protocol"。此处所有查询均为只读，immutable 跳过锁与 recovery；
    # db 回收前 opencode 已优雅退出（SIGINT），WAL 已 checkpoint，数据完整。
    db = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _ms_to_str(ms: int | None) -> str:
    if not ms:
        return ""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _dur_str(start_ms: int | None, end_ms: int | None) -> str:
    if not start_ms or not end_ms:
        return ""
    d = (end_ms - start_ms) / 1000
    if d < 60:
        return f"{d:.1f}s"
    return f"{int(d // 60)}m{int(d % 60)}s"


def extract_session(db_path: Path) -> dict:
    """读取 opencode.db，返回结构化 session dict。

    返回:
        {
          "session": {id, title, model, cost, tokens, time_created, time_updated, duration_s},
          "messages": [{role, time, parts: [...]}],
        }
    part 结构按 type 不同：
        text:     {type, text}
        reasoning:{type, text, duration_s}
        tool:     {type, tool, status, input, output, error, duration_s}
        step-start/step-finish: {type}
    """
    db = _connect(db_path)
    try:
        srow = db.execute(
            "SELECT id, title, model, cost, tokens_input, tokens_output, "
            "tokens_reasoning, tokens_cache_read, tokens_cache_write, "
            "time_created, time_updated FROM session "
            "ORDER BY time_created DESC LIMIT 1"
        ).fetchone()
        if not srow:
            return {"session": {}, "messages": []}

        model_raw = srow["model"]
        if model_raw:
            try:
                model_info = json.loads(model_raw)
                model_name = f"{model_info.get('providerID','')}/{model_info.get('id') or model_info.get('modelID','')}"
            except Exception:
                model_name = str(model_raw)
        else:
            model_name = ""

        session = {
            "id": srow["id"],
            "title": srow["title"] or "",
            "model": model_name,
            "cost": round(srow["cost"] or 0, 4),
            "tokens_input": srow["tokens_input"] or 0,
            "tokens_output": srow["tokens_output"] or 0,
            "tokens_reasoning": srow["tokens_reasoning"] or 0,
            "tokens_cache_read": srow["tokens_cache_read"] or 0,
            "tokens_cache_write": srow["tokens_cache_write"] or 0,
            "time_created": srow["time_created"],
            "time_updated": srow["time_updated"],
            "duration_s": round(((srow["time_updated"] or 0) - (srow["time_created"] or 0)) / 1000, 1),
        }

        messages: list[dict] = []
        for mrow in db.execute(
            "SELECT id, time_created, data FROM message ORDER BY time_created ASC"
        ):
            try:
                mdata = json.loads(mrow["data"])
            except Exception:
                mdata = {}
            role = mdata.get("role", "unknown")

            parts: list[dict] = []
            for prow in db.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created ASC",
                (mrow["id"],),
            ):
                try:
                    pdata = json.loads(prow["data"])
                except Exception:
                    continue
                parts.append(_normalize_part(pdata))

            messages.append({
                "role": role,
                "time": mrow["time_created"],
                "parts": parts,
            })

        return {"session": session, "messages": messages}
    finally:
        db.close()


def _normalize_part(pdata: dict) -> dict:
    """把 part.data 规范化为统一结构。"""
    ptype = pdata.get("type", "")
    out: dict = {"type": ptype}

    if ptype == "text":
        out["text"] = pdata.get("text", "")

    elif ptype == "reasoning":
        out["text"] = pdata.get("text", "")
        t = pdata.get("time") or {}
        out["duration_s"] = _dur_str(t.get("start"), t.get("end"))

    elif ptype == "tool":
        out["tool"] = pdata.get("tool", "")
        state = pdata.get("state") or {}
        out["status"] = state.get("status", "")
        out["input"] = state.get("input", {})
        out["output"] = state.get("output", "")
        out["error"] = state.get("error", "")
        t = state.get("time") or {}
        out["duration_s"] = _dur_str(t.get("start"), t.get("end"))

    return out


def render_trace_md(session_data: dict) -> str:
    """把结构化 session 渲染为人读 markdown。完整工具输出，<details> 折叠。"""
    s = session_data.get("session", {})
    messages = session_data.get("messages", [])

    lines: list[str] = []
    lines.append(f"# {s.get('title') or 'Session'}")
    lines.append("")
    lines.append(f"- **Model:** `{s.get('model','')}`")
    lines.append(f"- **Cost:** ${s.get('cost', 0)}")
    lines.append(
        f"- **Tokens:** in={s.get('tokens_input',0)} out={s.get('tokens_output',0)} "
        f"reason={s.get('tokens_reasoning',0)} "
        f"cache_read={s.get('tokens_cache_read',0)}"
    )
    lines.append(f"- **Duration:** {s.get('duration_s',0)}s")
    lines.append("")
    lines.append("---")
    lines.append("")

    step = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        parts = msg.get("parts", [])

        is_user = role == "user"
        if not is_user:
            step += 1
            lines.append(f"## Step {step}")

        for p in parts:
            ptype = p.get("type", "")

            if ptype == "step-start":
                continue

            if ptype == "text":
                if is_user:
                    lines.append("### User")
                else:
                    lines.append("### Text")
                lines.append("")
                lines.append(p.get("text", "").strip() or "_(empty)_")
                lines.append("")

            elif ptype == "reasoning":
                dur = p.get("duration_s", "")
                lines.append(f"### Reasoning{f' ({dur})' if dur else ''}")
                lines.append("")
                txt = p.get("text", "").strip()
                if txt:
                    lines.append("<details><summary>reasoning</summary>")
                    lines.append("")
                    lines.append(txt)
                    lines.append("")
                    lines.append("</details>")
                else:
                    lines.append("_(empty)_")
                lines.append("")

            elif ptype == "tool":
                _render_tool_md(p, lines)

            elif ptype == "step-finish":
                lines.append("---")
                lines.append("")

        if is_user:
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def _render_tool_md(p: dict, lines: list[str]) -> None:
    tool = p.get("tool", "?")
    status = p.get("status", "")
    dur = p.get("duration_s", "")
    inp = p.get("input", {})
    out = p.get("output", "")
    err = p.get("error", "")

    header = f"### Tool: {tool}"
    if status:
        header += f" [{status}]"
    if dur:
        header += f" ({dur})"
    lines.append(header)
    lines.append("")

    if inp:
        lines.append("**Input:**")
        lines.append("")
        if isinstance(inp, dict):
            for k, v in inp.items():
                vs = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                lines.append(f"- `{k}`: {vs}")
        else:
            lines.append(f"`{inp}`")
        lines.append("")

    if out:
        lines.append("<details><summary>Output</summary>")
        lines.append("")
        lines.append("```")
        lines.append(out.rstrip())
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if err:
        lines.append(f"**Error:** {err}")
        lines.append("")


def write_trace_md(db_path: Path, out_path: Path) -> bool:
    """从 db 提取并写入 trace.md。返回是否成功。"""
    if not db_path.exists():
        return False
    try:
        session_data = extract_session(db_path)
        if not session_data.get("session"):
            return False
        out_path.write_text(render_trace_md(session_data), encoding="utf-8")
        return True
    except Exception as e:
        out_path.write_text(f"提取 trace 失败: {e}", encoding="utf-8")
        return False


def read_session_stats(db_path: Path) -> dict:
    """汇总 opencode.db 中**所有** session（含子 agent）的 token/cost 统计。

    与旧版不同：不再 LIMIT 1，而是遍历全部 session 行做 SUM，
    解决子 agent（task 工具派生的 session）成本遗漏问题。
    """
    if not db_path.exists():
        return {}
    db = _connect(db_path)
    try:
        rows = db.execute(
            "SELECT model, cost, tokens_input, tokens_output, tokens_reasoning, "
            "tokens_cache_read, tokens_cache_write, parent_id FROM session"
        ).fetchall()
        if not rows:
            return {}

        total_cost = total_input = total_output = 0
        total_reasoning = total_cache_read = total_cache_write = 0
        main_model = ""
        session_count = 0
        subagent_count = 0

        for row in rows:
            total_cost += row["cost"] or 0
            total_input += row["tokens_input"] or 0
            total_output += row["tokens_output"] or 0
            total_reasoning += row["tokens_reasoning"] or 0
            total_cache_read += row["tokens_cache_read"] or 0
            total_cache_write += row["tokens_cache_write"] or 0
            session_count += 1
            if row["parent_id"]:
                subagent_count += 1
            else:
                main_model = row["model"] or main_model

        model_name = ""
        if main_model:
            try:
                mi = json.loads(main_model)
                model_name = f"{mi.get('providerID','')}/{mi.get('id') or mi.get('modelID','')}"
            except Exception:
                model_name = str(main_model)

        return {
            "model": model_name,
            "cost_usd": round(total_cost, 4),
            "tokens_input": total_input,
            "tokens_output": total_output,
            "tokens_reasoning": total_reasoning,
            "tokens_cache_read": total_cache_read,
            "tokens_cache_write": total_cache_write,
            "session_count": session_count,
            "subagent_sessions": subagent_count,
        }
    finally:
        db.close()


def count_tool_calls(session_data: dict) -> dict:
    """从结构化 session 统计工具调用次数。"""
    counts: dict[str, int] = {}
    total = 0
    for msg in session_data.get("messages", []):
        for p in msg.get("parts", []):
            if p.get("type") == "tool":
                tool = p.get("tool", "unknown")
                counts[tool] = counts.get(tool, 0) + 1
                total += 1
    counts["total"] = total
    return counts


_ERROR_PATTERNS = [
    "ValueError", "Traceback", "Error:", "ModuleNotFoundError",
    "ImportError", "RuntimeError", "cannot execute", "FileNotFoundError",
]


def detect_errors(session_data: dict) -> list[str]:
    """从结构化 session 检测错误模式。"""
    errors: list[str] = []
    counts: dict[str, int] = {}
    for msg in session_data.get("messages", []):
        for p in msg.get("parts", []):
            if p.get("type") != "tool":
                continue
            text = ""
            if p.get("error"):
                text += p["error"]
            if p.get("output"):
                text += "\n" + p["output"]
            for pat in _ERROR_PATTERNS:
                n = len(re.findall(pat, text))
                if n:
                    counts[pat] = counts.get(pat, 0) + n
    for pat, n in counts.items():
        errors.append(f"{pat}: {n}")
    return errors


def aggregate_tool_calls(db_path: Path) -> dict[str, int]:
    """遍历 opencode.db **所有** session 的 tool part，汇总工具调用次数。

    替代旧的 count_tool_calls()（后者只遍历 extract_session 的 LIMIT 1 消息）。
    """
    if not db_path.exists():
        return {"total": 0}
    db = _connect(db_path)
    try:
        counts: dict[str, int] = {}
        for srow in db.execute("SELECT id FROM session"):
            sid = srow["id"]
            for prow in db.execute(
                "SELECT data FROM part WHERE session_id = ? AND json_extract(data, '$.type') = 'tool'",
                (sid,),
            ):
                try:
                    pdata = json.loads(prow["data"])
                except Exception:
                    continue
                tool = pdata.get("tool", "unknown")
                counts[tool] = counts.get(tool, 0) + 1
        counts["total"] = sum(counts.values())
        return counts
    finally:
        db.close()


def aggregate_errors(db_path: Path) -> list[str]:
    """遍历 opencode.db **所有** session 的 tool part，汇总错误模式。

    替代旧的 detect_errors()（后者只遍历 extract_session 的 LIMIT 1 消息）。
    """
    if not db_path.exists():
        return []
    db = _connect(db_path)
    try:
        counts: dict[str, int] = {}
        for srow in db.execute("SELECT id FROM session"):
            sid = srow["id"]
            for prow in db.execute(
                "SELECT data FROM part WHERE session_id = ? AND json_extract(data, '$.type') = 'tool'",
                (sid,),
            ):
                try:
                    pdata = json.loads(prow["data"])
                except Exception:
                    continue
                text = ""
                state = pdata.get("state") or {}
                if state.get("error"):
                    text += state["error"]
                if state.get("output"):
                    text += "\n" + state["output"]
                for pat in _ERROR_PATTERNS:
                    n = len(re.findall(pat, text))
                    if n:
                        counts[pat] = counts.get(pat, 0) + n
        errors: list[str] = []
        for pat, n in counts.items():
            errors.append(f"{pat}: {n}")
        return errors
    finally:
        db.close()
