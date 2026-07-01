"""Validation of LLM JSON output against the expected schema."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_TAG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class ValidationResult:
    ok: bool
    error: str
    parsed: dict | None

    @classmethod
    def success(cls, parsed: dict) -> "ValidationResult":
        return cls(ok=True, error="", parsed=parsed)

    @classmethod
    def failure(cls, error: str) -> "ValidationResult":
        return cls(ok=False, error=error, parsed=None)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # remove opening fence (with optional language tag)
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        # remove closing fence
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    return text


def _extract_json_object(text: str) -> str:
    """Return the substring of the outermost {...} block, if identifiable."""
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    # find the first { and the matching last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_llm_json(text: str) -> tuple[dict | None, str]:
    """Best-effort parse of the LLM text into a dict.

    Returns (parsed, error). On success error is "".
    """
    candidate = _strip_fences(text)
    candidate = _extract_json_object(candidate)
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败: {exc.msg} (pos {exc.pos})"

    # Accept a 1-element array and unwrap it (defensive: model may emit the old array shape).
    if isinstance(obj, list):
        if len(obj) == 1 and isinstance(obj[0], dict):
            obj = obj[0]
        else:
            return None, "顶层是数组但非单元素对象数组"
    if not isinstance(obj, dict):
        return None, f"顶层不是 JSON 对象: {type(obj).__name__}"
    return obj, ""


def validate(
    obj: dict,
    preset_set: frozenset[str] | None = None,
    categories: tuple[str, ...] | frozenset[str] | None = None,
) -> ValidationResult:
    """Schema-validate the parsed object.

    ``preset_set`` is the set of preset leaf tags (from capabilities.txt),
    used to cross-check each positive_condition's ``table_cap`` against its
    ``target_capability`` membership. ``categories`` is the tuple of valid
    大类 names (also from capabilities.txt), used to check
    ``capability_category``. Both default to empty (everything fails the
    membership checks) so callers should always pass them.
    """
    if preset_set is None:
        preset_set = frozenset()
    if categories is None:
        categories = ()
    # Required top-level keys
    for key in ("negative_chunks", "positive_conditions", "has_valid_condition"):
        if key not in obj:
            return ValidationResult.failure(f"缺少字段: {key}")

    neg = obj["negative_chunks"]
    pos = obj["positive_conditions"]
    has_valid = obj["has_valid_condition"]

    if not isinstance(neg, list):
        return ValidationResult.failure("negative_chunks 不是数组")
    if not isinstance(pos, list):
        return ValidationResult.failure("positive_conditions 不是数组")
    if not isinstance(has_valid, bool):
        return ValidationResult.failure("has_valid_condition 不是布尔值")

    # Validate negative_chunks items
    chunk_ids = []
    for i, item in enumerate(neg):
        if not isinstance(item, dict):
            return ValidationResult.failure(f"negative_chunks[{i}] 不是对象")
        cid = item.get("chunk_id")
        txt = item.get("text")
        if not isinstance(cid, int) or isinstance(cid, bool):
            return ValidationResult.failure(
                f"negative_chunks[{i}].chunk_id 必须是整数"
            )
        if not isinstance(txt, str) or not txt.strip():
            return ValidationResult.failure(
                f"negative_chunks[{i}].text 必须是非空字符串"
            )
        chunk_ids.append(cid)

    if len(set(chunk_ids)) != len(chunk_ids):
        return ValidationResult.failure("negative_chunks 中 chunk_id 有重复")

    # Validate positive_conditions items
    src_ids = []
    for j, item in enumerate(pos):
        if not isinstance(item, dict):
            return ValidationResult.failure(f"positive_conditions[{j}] 不是对象")
        sid = item.get("source_chunk_id")
        if not isinstance(sid, int) or isinstance(sid, bool):
            return ValidationResult.failure(
                f"positive_conditions[{j}].source_chunk_id 必须是整数"
            )
        tc = item.get("target_capability")
        if not isinstance(tc, str) or not tc.strip():
            return ValidationResult.failure(
                f"positive_conditions[{j}].target_capability 必须是非空字符串"
            )
        if not _TAG_RE.match(tc):
            return ValidationResult.failure(
                f"positive_conditions[{j}].target_capability 必须是英文 snake_case"
                f"（匹配 ^[a-z][a-z0-9_]*$）：{tc!r}"
            )
        tcap = item.get("table_cap")
        if not isinstance(tcap, bool):
            return ValidationResult.failure(
                f"positive_conditions[{j}].table_cap 必须是布尔值"
            )
        cat = item.get("capability_category")
        if not isinstance(cat, str) or not cat.strip():
            return ValidationResult.failure(
                f"positive_conditions[{j}].capability_category 必须是非空字符串"
            )
        if cat not in categories:
            return ValidationResult.failure(
                f"positive_conditions[{j}].capability_category 必须是预设表中的大类段名"
                f"（{list(categories)}）之一：{cat!r}"
            )
        # Strict table_cap consistency: membership must match the flag.
        in_preset = tc in preset_set
        if in_preset != tcap:
            return ValidationResult.failure(
                f"positive_conditions[{j}].table_cap={tcap} 与 target_capability "
                f"{tc!r} 是否在预设表中（{in_preset}）不一致"
            )
        src_ids.append(sid)

    # Length and mapping consistency
    if len(pos) != len(neg):
        return ValidationResult.failure(
            f"positive_conditions 长度({len(pos)}) != negative_chunks 长度({len(neg)})"
        )

    chunk_id_set = set(chunk_ids)
    for sid in src_ids:
        if sid not in chunk_id_set:
            return ValidationResult.failure(
                f"source_chunk_id {sid} 没有对应的 negative_chunks.chunk_id"
            )

    # has_valid_condition consistency
    expected_valid = len(pos) > 0 and all(
        isinstance(item.get("target_capability"), str)
        and item.get("target_capability", "").strip()
        for item in pos
    )
    if has_valid != expected_valid:
        return ValidationResult.failure(
            f"has_valid_condition={has_valid} 与实际条件数不一致(期望 {expected_valid})"
        )

    return ValidationResult.success(obj)
