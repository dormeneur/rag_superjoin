"""Validate the Space's YAML header before anything is uploaded.

The Hub checks this server side and rejects the push, which costs a full upload of
the corpus to learn that a description was one character too long.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

COLOURS = {"red", "yellow", "green", "blue", "indigo", "purple", "pink", "gray"}
SDKS = {"gradio", "streamlit", "docker", "static"}
LIMITS = {"short_description": 60, "title": 100}


def main(path: str) -> int:
    text = Path(path).read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not match:
        print(f"{path}: no YAML header", file=sys.stderr)
        return 1

    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()

    problems = []
    for key, limit in LIMITS.items():
        value = fields.get(key, "")
        if len(value) > limit:
            problems.append(f"{key} is {len(value)} characters, the limit is {limit}")
    for key in ("colorFrom", "colorTo"):
        if fields.get(key) and fields[key] not in COLOURS:
            problems.append(f"{key} is {fields[key]!r}; allowed: {', '.join(sorted(COLOURS))}")
    if fields.get("sdk") not in SDKS:
        problems.append(f"sdk is {fields.get('sdk')!r}; allowed: {', '.join(sorted(SDKS))}")
    for key in ("title", "emoji", "sdk"):
        if not fields.get(key):
            problems.append(f"{key} is missing")

    for problem in problems:
        print(f"{path}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
