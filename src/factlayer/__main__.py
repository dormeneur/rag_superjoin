"""Command line: ingest PDFs and dump the knowledge layer.

Used to build the sample output committed to the repository, so the results can be
inspected without an API key.
"""

from __future__ import annotations

import argparse
import hashlib
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
    ingest_command.add_argument(
        "--sweeps", type=int, default=2,
        help="extra passes over documents a busy provider left unfinished (default 2)",
    )

    export_command = commands.add_parser("export", help="write the layer out as JSON")
    export_command.add_argument("--out", type=Path, default=Path("samples"))

    commands.add_parser("reconcile", help="recompare stored facts after a rule change")

    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _ingest(args.paths, args.sweeps)
    if args.command == "reconcile":
        print(f"{rebuild_relations()} relationships")
        return 0
    return _export(args.out)


def _ingest(paths: list[Path], sweeps: int = 2) -> int:
    """Read each PDF, then go back for whatever a busy provider would not answer.

    A free tier refusing one page out of a hundred is routine, and resuming is cheap
    because every page already read stays read. Doing that here means the documented
    one-line command actually finishes, instead of leaving a document partial and
    expecting the operator to notice.
    """
    files = [path for path in paths if path.is_file()]
    failures = 0

    for path in files:
        try:
            _report(path.name, ingest(path.read_bytes(), path.name))
        except IngestError as exc:
            print(f"{path.name}: {exc}", file=sys.stderr)
            failures += 1

    for attempt in range(max(0, sweeps)):
        unfinished = {
            document["sha256"]
            for document in Store().documents()
            if document["status"] != "complete"
        }
        if not unfinished:
            break
        print(f"sweep {attempt + 1}: {len(unfinished)} document(s) left unfinished")
        for path in files:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() in unfinished:
                try:
                    _report(path.name, ingest(data, path.name))
                except IngestError as exc:
                    print(f"{path.name}: {exc}", file=sys.stderr)

    return 1 if failures else 0


def _report(name: str, result) -> None:
    print(
        f"{name}: {result.page_count} pages, {result.claims_extracted} facts, "
        f"{result.claims_quarantined} quarantined, "
        f"{result.relations_created} relationships ({result.status})"
    )
    if result.note:
        print(f"  note: {result.note}")


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
