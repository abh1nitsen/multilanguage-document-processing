import argparse
import sys

from .store import Store, dumps
from .model import DEFAULT_MODEL
from .judge import DEFAULT_JUDGE_MODEL


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Read Hindi notices, review fields, and persist confirmed records.")
    parser.add_argument("--db", default="data/notices.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("ingest"); p.add_argument("path")
    p.add_argument("--language", choices=["hi", "kn", "ur", "ar"], default="hi")
    p.add_argument("--provider", choices=["baseline", "groq"], default="baseline")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto")
    for name in ["show", "retry", "recover", "confirm", "correct", "reopen", "judge", "reprocess"]:
        p = sub.add_parser(name); p.add_argument("id")
        if name in {"confirm", "correct", "reopen", "reprocess"}:
            p.add_argument("--revision", type=int, required=True)
        if name in {"confirm", "correct"}:
            p.add_argument("--reviewer", required=True)
        if name == "correct":
            p.add_argument("--field", required=True)
            value = p.add_mutually_exclusive_group(required=True)
            value.add_argument("--value")
            value.add_argument("--clear", action="store_true")
            p.add_argument("--reason", required=True)
        if name == "recover":
            p.add_argument("--worker-stopped", action="store_true", required=True,
                           help="Assert that the original processing worker has stopped.")
        if name == "judge":
            p.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
        if name == "reprocess":
            p.add_argument("--refresh-reading", action="store_true")
    p = sub.add_parser("export"); p.add_argument("path")
    p.add_argument("--profile", choices=["original", "ascii-experimental"], default="original")
    args = parser.parse_args(argv)
    store = Store(args.db)
    try:
        if args.command == "list": result = store.list()
        elif args.command == "ingest": result = store.ingest(args.path, args.language, args.provider, args.model, args.ocr)
        elif args.command == "show": result = store.get(args.id)
        elif args.command == "retry": result = store.process(args.id)
        elif args.command == "recover": result = store.recover(args.id)
        elif args.command == "confirm": result = store.confirm(args.id, args.reviewer, args.revision)
        elif args.command == "reopen": result = store.reopen(args.id, args.revision)
        elif args.command == "judge": result = store.judge(args.id, args.model)
        elif args.command == "reprocess": result = store.reprocess(args.id, args.revision, args.refresh_reading)
        elif args.command == "correct":
            result = store.correct(args.id, args.field, None if args.clear else args.value,
                                   args.reason, args.reviewer, args.revision)
        else: result = store.export(args.path, args.profile)
        print(dumps(result))
        doc = result.get("document", result) if isinstance(result, dict) else {}
        return 2 if doc.get("state") in {"failed", "rejected"} else 0
    except (ValueError, OSError) as exc:
        print(dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
