"""negtopos CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from .capabilities import CAPS_PATH, preset_set
from .config import load_config
from .pipeline import process_issue


def _read_jsonl(path: str) -> list[dict]:
    items: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[warn] 跳过第 {lineno} 行（非法 JSON）: {exc}", file=sys.stderr)
                continue
            if isinstance(obj, list):
                # tolerate a file that is one big JSON array
                for o in obj:
                    if isinstance(o, dict):
                        items.append(o)
            elif isinstance(obj, dict):
                items.append(obj)
    return items


def _write_record(out_f, record: dict) -> None:
    out_f.write(json.dumps(record, ensure_ascii=False))
    out_f.write("\n")
    out_f.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="negtopos",
        description="将负面反馈 issue 转换为正向轨迹筛选条件。",
    )
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--input", default=None, help="覆盖输入 jsonl 路径")
    parser.add_argument("--output", default=None, help="覆盖输出 jsonl 路径")
    parser.add_argument(
        "--max-retries", type=int, default=None, help="覆盖最大重试次数"
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.input:
        config.io.input_path = args.input
    if args.output:
        config.io.output_path = args.output
    if args.max_retries is not None:
        config.processing.max_retries = args.max_retries

    in_path = config.io.input_path
    out_path = config.io.output_path

    if not Path(in_path).exists():
        print(f"[error] 输入文件不存在: {in_path}", file=sys.stderr)
        return 1

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    issues = _read_jsonl(in_path)
    caps_count = len(preset_set(CAPS_PATH))
    print(
        f"[info] 读取 {len(issues)} 条 issue；模型={config.api.model} "
        f"重试={config.processing.max_retries} 预设标签={caps_count} "
        f"输入={in_path} 输出={out_path}",
        file=sys.stderr,
    )

    ok_count = 0
    filtered_count = 0
    fail_count = 0

    with httpx.Client(timeout=config.api.timeout) as client, Path(out_path).open(
        "w", encoding="utf-8"
    ) as out_f:
        for idx, issue in enumerate(issues, 1):
            record_id = f"fb_{idx:06d}"
            record = process_issue(client, config, issue, record_id)
            _write_record(out_f, record)

            rid = record["record_id"]
            if not record["is_valid_feedback"]:
                filtered_count += 1
                print(
                    f"[{rid}] filtered (category not supported): "
                    f"{record.get('invalid_reason')}",
                    file=sys.stderr,
                )
            elif record["has_valid_condition"]:
                ok_count += 1
                n_chunks = len(record["negative_chunks"])
                print(
                    f"[{rid}] ok: {n_chunks} chunk(s) -> conditions",
                    file=sys.stderr,
                )
            else:
                fail_count += 1
                err = record.get("_last_error", "unknown")
                attempts = record.get("_attempts", "?")
                print(
                    f"[{rid}] FAILED after {attempts} attempt(s): {err}",
                    file=sys.stderr,
                )

    print(
        f"[done] total={len(issues)} ok={ok_count} filtered={filtered_count} failed={fail_count}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
