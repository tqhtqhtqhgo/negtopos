"""Per-issue pipeline: Step a (filter), Step b (topic), Step c (LLM + retry)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx

from .capabilities import (
    CAPS_PATH,
    append_capability,
    load_categories,
    preset_set,
    read_caps_text,
)
from .config import Config
from .llm import LLMError, LLMResponseError, LLMResult, chat_complete
from .prompt import build_prompt
from .validate import ValidationResult, parse_llm_json, validate

SUPPORTED_CATEGORIES = {
    "逻辑与算法实现",
    "任务执行流程",
    "指令与偏好遵循",
    "安全与合规",
    "服务端工具集成",
    "记忆与上下文管理",
}


@dataclass
class StepCResult:
    negative_chunks: list
    positive_conditions: list
    has_valid_condition: bool
    llm_response: str
    attempts: int
    last_error: str


def step_a_filter(issue: dict) -> tuple[bool, str | None]:
    """Step a: is this issue in a supported category?"""
    category = issue.get("issue_category")
    if category in SUPPORTED_CATEGORIES:
        return True, None
    return False, f"issue_category '{category}' 不在支持的6类问题中"


def _slugify(text: str) -> str:
    """Derive a snake_case slug from text (placeholder Step-b normalization)."""
    if not text:
        return "unknown_issue"
    # keep cjk and alphanumeric, map separators to underscore
    s = text.strip().lower()
    s = re.sub(r"[\s,;:：，；、()/\\]+", "_", s)
    s = re.sub(r"[^a-z0-9_\u4e00-\u9fff]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "unknown_issue"
    return s[:60]


def step_b_topic(issue: dict) -> tuple[str, str | None]:
    """Step b: derive topic slug; cluster_id is a placeholder (null)."""
    desc = issue.get("issue_description") or ""
    topic = _slugify(desc)
    return topic, None


def _build_retry_hint(error: str) -> str:
    return (
        f"上一次输出无效：{error}。请严格按输出格式只输出一个 JSON 对象，"
        "包含 negative_chunks、positive_conditions、has_valid_condition 三个字段，"
        "且 positive_conditions 长度必须等于 negative_chunks 长度，"
        "每个 source_chunk_id 必须对应一个 chunk_id，"
        "每项必须含 target_capability(预设表中的叶子标签，英文 snake_case，"
        "不能填大类名) / table_cap(布尔) / capability_category(预设表中的大类段名之一)，"
        "且 table_cap 必须与 target_capability 是否为预设叶子一致。不要输出任何其它内容。"
    )


def step_c(
    client: httpx.Client,
    config: Config,
    issue: dict,
) -> StepCResult:
    """Step c: build prompt, call LLM, validate, retry on failure."""
    issue_json = json.dumps(issue, ensure_ascii=False)
    # Load the preset table fresh for this issue so tags added by previous
    # issues in the same run are visible.
    caps_text = read_caps_text(CAPS_PATH)
    caps_set = preset_set(CAPS_PATH)
    cats = load_categories(CAPS_PATH)
    prompt = build_prompt(issue_json, caps_text)

    max_retries = config.processing.max_retries
    backoff_base = config.processing.backoff_base

    last_error = ""
    last_text = ""
    attempts = 0

    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        attempts = attempt
        retry_hint = _build_retry_hint(last_error) if attempt > 1 else None

        try:
            result: LLMResult = chat_complete(
                client, config.api, prompt, retry_hint=retry_hint
            )
        except (LLMError, LLMResponseError) as exc:
            last_error = f"[attempt {attempt}] 调用失败: {exc}"
            last_text = ""
            # If json_mode caused a 400 (unsupported response_format),
            # disable it and retry immediately.
            if (
                config.api.json_mode
                and isinstance(exc, LLMResponseError)
                and exc.status_code == 400
            ):
                config.api.json_mode = False
                last_error += "（已关闭 json_mode 并将在下次重试）"
            _sleep_backoff(backoff_base, attempt)
            continue

        last_text = result.text
        parsed, parse_err = parse_llm_json(result.text)
        if parse_err:
            last_error = f"[attempt {attempt}] {parse_err}"
            _sleep_backoff(backoff_base, attempt)
            continue

        vr: ValidationResult = validate(parsed, caps_set, cats)
        if not vr.ok:
            last_error = f"[attempt {attempt}] {vr.error}"
            _sleep_backoff(backoff_base, attempt)
            continue

        # Success
        neg = parsed.get("negative_chunks", [])
        pos = parsed.get("positive_conditions", [])
        has_valid = bool(parsed.get("has_valid_condition", False))

        # Persist any self-invented tags (table_cap=false) into the preset
        # table under their declared capability_category.
        if has_valid:
            for item in pos:
                if not isinstance(item, dict):
                    continue
                if item.get("table_cap") is False:
                    tc = item.get("target_capability")
                    cat = item.get("capability_category", "other")
                    if isinstance(tc, str) and tc:
                        try:
                            append_capability(CAPS_PATH, tc, cat)
                        except OSError:
                            pass

        return StepCResult(
            negative_chunks=neg,
            positive_conditions=pos,
            has_valid_condition=has_valid,
            llm_response=result.text,
            attempts=attempts,
            last_error="",
        )

    # All retries exhausted
    return StepCResult(
        negative_chunks=[],
        positive_conditions=[],
        has_valid_condition=False,
        llm_response=last_text,
        attempts=attempts,
        last_error=last_error,
    )


def _sleep_backoff(base: float, attempt: int) -> None:
    if base <= 0:
        return
    time.sleep(min(base * (2 ** (attempt - 1)), 30.0))


def process_issue(
    client: httpx.Client,
    config: Config,
    issue: dict,
    record_id: str,
) -> dict:
    """Run the full pipeline for one issue and return the record dict."""
    raw_feedback = issue.get("issue_description") or ""
    is_valid, invalid_reason = step_a_filter(issue)
    topic, cluster_id = step_b_topic(issue)

    record = {
        "record_id": record_id,
        "raw_feedback": raw_feedback,
        "is_valid_feedback": is_valid,
        "invalid_reason": invalid_reason,
        "topic": topic if is_valid else _slugify(raw_feedback),
        "cluster_id": cluster_id,
        "llm_prompt": None,
        "llm_response": None,
        "negative_chunks": [],
        "positive_conditions": [],
        "has_valid_condition": False,
    }

    if not is_valid:
        # Filtered out at Step a; no LLM call.
        return record

    issue_json = json.dumps(issue, ensure_ascii=False)
    caps_text = read_caps_text(CAPS_PATH)
    record["llm_prompt"] = build_prompt(issue_json, caps_text)

    sc = step_c(client, config, issue)
    record["llm_response"] = sc.llm_response
    record["negative_chunks"] = sc.negative_chunks
    record["positive_conditions"] = sc.positive_conditions
    record["has_valid_condition"] = sc.has_valid_condition

    if sc.last_error:
        # Keep a trace of the failure inside the record for debugging.
        record["_last_error"] = sc.last_error
        record["_attempts"] = sc.attempts

    return record
