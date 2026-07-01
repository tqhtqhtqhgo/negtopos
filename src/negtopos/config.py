"""Configuration loading from config.toml (stdlib tomllib)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ApiConfig:
    url: str
    key: str
    model: str
    json_mode: bool = True
    temperature: float = 0.2
    timeout: float = 120.0


@dataclass
class ProcessingConfig:
    max_retries: int = 5
    backoff_base: float = 2.0


@dataclass
class IoConfig:
    input_path: str = "input/issues.jsonl"
    output_path: str = "output/results.jsonl"


@dataclass
class Config:
    api: ApiConfig
    processing: ProcessingConfig
    io: IoConfig


def _get(data: dict, section: str, key: str, default, cast=None):
    val = data.get(section, {}).get(key, default)
    if cast is not None and val is not None:
        try:
            val = cast(val)
        except (TypeError, ValueError):
            val = default
    return val


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)

    api = ApiConfig(
        url=_get(data, "api", "url", "http://localhost:3000"),
        key=_get(data, "api", "key", ""),
        model=_get(data, "api", "model", "glm5.2"),
        json_mode=_get(data, "api", "json_mode", True, bool),
        temperature=float(_get(data, "api", "temperature", 0.2, float)),
        timeout=float(_get(data, "api", "timeout", 120.0, float)),
    )
    processing = ProcessingConfig(
        max_retries=int(_get(data, "processing", "max_retries", 5, int)),
        backoff_base=float(_get(data, "processing", "backoff_base", 2.0, float)),
    )
    io = IoConfig(
        input_path=_get(data, "io", "input_path", "input/issues.jsonl"),
        output_path=_get(data, "io", "output_path", "output/results.jsonl"),
    )
    return Config(api=api, processing=processing, io=io)
