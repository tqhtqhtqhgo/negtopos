"""Load / append the target_capability preset table (capabilities.txt).

The file lives next to this module and is the single source of truth for
preset capability tags. It is organized into sections keyed by 大类 header
lines like ``# 代码与实现类``. New tags emitted by the LLM (table_cap=false)
are appended into the matching 大类 section on success.
"""

from __future__ import annotations

import re
from pathlib import Path

CAPS_PATH: Path = Path(__file__).parent / "capabilities.txt"

VALID_CATEGORIES: tuple[str, ...] = (
    "代码与实现类",
    "任务执行流程类",
    "指令与偏好遵循类",
    "安全与合规类",
    "other",
)

_TAG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def load_capabilities(path: Path = CAPS_PATH) -> dict[str, str]:
    """Return ``{tag: 大类}`` for every tag line in the file.

    Section headers are lines starting with ``#`` that match a known 大类.
    Other comment lines and blank lines are ignored.
    """
    mapping: dict[str, str] = {}
    if not path.exists():
        return mapping
    current_cat = "other"
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                header = line[1:].strip()
                if header in VALID_CATEGORIES:
                    current_cat = header
                continue
            if _TAG_RE.match(line):
                mapping[line] = current_cat
    return mapping


def preset_set(path: Path = CAPS_PATH) -> frozenset[str]:
    """Return the set of preset tags (for table_cap membership checks)."""
    return frozenset(load_capabilities(path).keys())


def read_caps_text(path: Path = CAPS_PATH) -> str:
    """Return the raw file text for splicing into the prompt."""
    if not path.exists():
        return "(预设标签表为空)"
    return path.read_text(encoding="utf-8").strip()


def append_capability(path: Path, tag: str, category: str) -> bool:
    """Append ``tag`` into the ``# <category>`` section if not already present.

    - If ``tag`` already exists anywhere in the file → skip (no-op).
    - If ``category`` matches a known 大类 and its section exists → insert at
      the end of that section (before the next ``#`` header or EOF).
    - Otherwise → append under the ``# other`` section (creating it if needed).

    Returns True if the file was modified, False if it was a no-op.
    """
    if not _TAG_RE.match(tag):
        return False

    # Normalize category; unknown → other.
    if category not in VALID_CATEGORIES:
        category = "other"

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Bootstrap: create the file with the requested section + tag.
        path.write_text(
            f"# target_capability 预设标签表\n# {category}\n{tag}\n",
            encoding="utf-8",
        )
        return True

    lines = path.read_text(encoding="utf-8").splitlines()
    # Existing tags anywhere in the file.
    existing = {
        ln.strip()
        for ln in lines
        if ln.strip() and not ln.strip().startswith("#") and _TAG_RE.match(ln.strip())
    }
    if tag in existing:
        return False

    # Locate section boundaries: list of (category, start_index_of_header, end_index_exclusive)
    sections: list[tuple[str, int, int]] = []
    cur_cat: str | None = None
    cur_start = -1
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("#"):
            header = s[1:].strip()
            if header in VALID_CATEGORIES:
                if cur_cat is not None:
                    sections.append((cur_cat, cur_start, i))
                cur_cat = header
                cur_start = i
        # non-header lines are part of current section; nothing to do
    if cur_cat is not None:
        sections.append((cur_cat, cur_start, len(lines)))

    # Find target section.
    target = next(((c, s, e) for (c, s, e) in sections if c == category), None)

    new_lines = list(lines)
    if target is not None:
        _, _s, e = target
        # Insert at end of section: position e (before next header / EOF).
        new_lines.insert(e, tag)
    else:
        # Need an `# other` section. Prefer existing `# other` header if present
        # but empty (handled above if header exists). If no other section at all:
        other_target = next(((c, s, e) for (c, s, e) in sections if c == "other"), None)
        if other_target is not None:
            _, _s, e = other_target
            new_lines.insert(e, tag)
        else:
            # No other section anywhere: append one at EOF.
            if new_lines and new_lines[-1].strip() != "":
                new_lines.append("")
            new_lines.append("# other")
            new_lines.append(tag)

    # Ensure trailing newline.
    text = "\n".join(new_lines).rstrip("\n") + "\n"
    path.write_text(text, encoding="utf-8")
    return True
