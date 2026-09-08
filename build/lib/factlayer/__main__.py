"""Command line: ingest PDFs and dump the knowledge layer.

Used to build the sample output committed to the repository, so the results can be
inspected without an API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import IngestError, ingest, rebuild_relations
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factlayer")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_command = commands.add_parser("ingest", help="add PDFs to the knowledge layer")
    ingest_command.add_argument("paths", nargs="+", type=Path)

    export_command = commands.add_parser("export", help="write the layer out as JSON")
    export_command.add_argument("--out", type=Path, default=Path("samples"))

    commands.add_parser("reconcile", help="recompare stored facts after a rule change")

    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _ingest(args.paths)
    if args.command == "reconcile":
        print(f"{rebuild_relations()} relationships")
        return 0
    return _export(args.out)


def _ingest(paths: list[Path]) -> int:
    failures = 0
    for path in paths:
        if not path.is_file():
            continue
        try:
            result = ingest(path.read_bytes(), path.name)
        except IngestError as exc:
            print(f"{path.name}: {exc}", file=sys.stderr)
            failures += 1
            continue
        print(
            f"{path.name}: {result.page_count} pages, {result.claims_extracted} facts, "
            f"{result.claims_quarantined} quarantined, "
            f"{result.relations_created} relationships ({result.status})"
        )
        if result.note:
            print(f"  note: {result.note}")
    return 1 if failures else 0


def _export(out: Path) -> int:
    store = Store()
    out.mkdir(parents=True, exist_ok=True)
    payloads = {
        "documents.json": store.documents(),
        "facts.json": [claim.as_dict() for claim in store.claims(status="active")],
        "quarantined.json": [claim.as_dict() for claim in store.claims(status="quarantined")],
        "relations.json": store.relations(),
        "summary.json": store.counts(),
    }
    for name, payload in payloads.items():
        (out / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"wrote {out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
