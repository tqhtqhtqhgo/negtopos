"""Load / append the target_capability preset table (capabilities.txt).

The file lives next to this module and is the single source of truth.
It is a tree: top-level bare snake_case lines are 大类 (categories); indented
lines starting with ├/└ tree chars are leaf capability tags.

target_capability may only be a leaf tag; 大类 names are used as
capability_category. New leaves emitted by the LLM (table_cap=false) are
appended into the matching 大类 section on success. 大类 are read dynamically
from the file so manual edits need no code change.
"""

from __future__ import annotations

import re
from pathlib import Path

CAPS_PATH: Path = Path(__file__).parent / "capabilities.txt"

_TAG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Characters used to draw tree leaf prefixes: ├ └ ─ and space.
_TREE_CHARS = "├└─ "
_LEAF_PREFIX_CHARS = frozenset("├└")


def _is_comment_or_blank(s: str) -> bool:
    return not s or s.startswith("#")


def _strip_leaf(s: str) -> str:
    """Given a stripped leaf line like '├── valid_syntax_in_toolcall', return the tag."""
    return s.lstrip(_TREE_CHARS).strip()


def load_categories(path: Path = CAPS_PATH) -> tuple[str, ...]:
    """Return the 大类 (top-level bare snake_case header) names in file order.

    ``other`` is always included (appended if missing) so it's a valid category
    even when the file has no ``other`` section.
    """
    cats: list[str] = []
    seen: set[str] = set()
    if not path.exists():
        return ("other",)
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if _is_comment_or_blank(s):
                continue
            if s[0] in _LEAF_PREFIX_CHARS:
                continue  # leaf line
            if _TAG_RE.match(s):
                if s not in seen:
                    cats.append(s)
                    seen.add(s)
    if "other" not in seen:
        cats.append("other")
    return tuple(cats)


def load_capabilities(path: Path = CAPS_PATH) -> dict[str, str]:
    """Return ``{leaf_tag: 大类}`` for every leaf tag in the file."""
    mapping: dict[str, str] = {}
    if not path.exists():
        return mapping
    current_cat = "other"
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if _is_comment_or_blank(s):
                continue
            if s[0] in _LEAF_PREFIX_CHARS:
                tag = _strip_leaf(s)
                if _TAG_RE.match(tag):
                    mapping[tag] = current_cat
            elif _TAG_RE.match(s):
                # 大类 header line
                current_cat = s
    return mapping


def preset_set(path: Path = CAPS_PATH) -> frozenset[str]:
    """Return the set of preset leaf tags (for table_cap membership checks).

    Only leaves count as preset target_capability values; 大类 header names are
    NOT in the preset set.
    """
    return frozenset(load_capabilities(path).keys())


def read_caps_text(path: Path = CAPS_PATH) -> str:
    """Return the raw file text for splicing into the prompt."""
    if not path.exists():
        return "(预设标签表为空)"
    return path.read_text(encoding="utf-8").strip()


def append_capability(path: Path, tag: str, category: str) -> bool:
    """Append ``tag`` as a leaf into the ``category`` 大类 section (tree format).

    - If ``tag`` already exists anywhere in the file (as a leaf or a 大类
      header) → skip (no-op).
    - If ``category`` matches a known 大类 and its section exists → insert the
      tag as a new ``└── {tag}`` leaf at the end of that section, flipping the
      previous last child's ``└──`` to ``├──`` for proper tree shape.
    - Otherwise → append under the ``other`` section (creating it if missing).

    Returns True if the file was modified, False if it was a no-op.
    """
    if not _TAG_RE.match(tag):
        return False

    categories = load_categories(path)
    if category not in categories:
        category = "other"

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            f"# target_capability 预设标签表\n{category}\n    └── {tag}\n",
            encoding="utf-8",
        )
        return True

    lines = path.read_text(encoding="utf-8").splitlines()

    # Existing names (leaves + 大类 headers) anywhere in the file.
    existing: set[str] = set()
    for raw in lines:
        s = raw.strip()
        if _is_comment_or_blank(s):
            continue
        if s[0] in _LEAF_PREFIX_CHARS:
            t = _strip_leaf(s)
            if _TAG_RE.match(t):
                existing.add(t)
        elif _TAG_RE.match(s):
            existing.add(s)
    if tag in existing:
        return False

    # Locate section boundaries by 大类 header lines.
    # sections: list of (category, header_index, end_index_exclusive)
    sections: list[tuple[str, int, int]] = []
    cur_cat: str | None = None
    cur_start = -1
    for i, raw in enumerate(lines):
        s = raw.strip()
        if _is_comment_or_blank(s):
            continue
        if s[0] in _LEAF_PREFIX_CHARS:
            continue
        if _TAG_RE.match(s):
            if cur_cat is not None:
                sections.append((cur_cat, cur_start, i))
            cur_cat = s
            cur_start = i
    if cur_cat is not None:
        sections.append((cur_cat, cur_start, len(lines)))

    new_lines = list(lines)

    def _insert_leaf(section_start: int, section_end: int) -> None:
        # Find the last child leaf line within (section_start, section_end).
        last_child_idx: int | None = None
        for i in range(section_start + 1, section_end):
            s = new_lines[i].strip()
            if _is_comment_or_blank(s):
                continue
            if s and s[0] in _LEAF_PREFIX_CHARS:
                last_child_idx = i
        if last_child_idx is None:
            # No children yet: insert right after the header line.
            new_lines.insert(section_start + 1, f"    └── {tag}")
        else:
            last = new_lines[last_child_idx]
            stripped = last.lstrip()
            indent = last[: len(last) - len(stripped)]
            if stripped.startswith("└"):
                # Flip previous last child └ → ├ for proper tree shape.
                new_lines[last_child_idx] = indent + "├" + stripped[1:]
            new_lines.insert(last_child_idx + 1, f"    └── {tag}")

    target = next(((c, s, e) for (c, s, e) in sections if c == category), None)
    if target is not None:
        _, s, e = target
        _insert_leaf(s, e)
    else:
        # category (only possible for 'other' here) has no section: append at EOF.
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append("other")
        new_lines.append(f"    └── {tag}")

    text = "\n".join(new_lines).rstrip("\n") + "\n"
    path.write_text(text, encoding="utf-8")
    return True
