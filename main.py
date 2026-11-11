"""Command line for the Orbit Air baggage desk."""

import argparse
import csv
import sqlite3
import sys
from datetime import datetime

import claims
import db


def _timestamp(text):
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO timestamp: {text!r}") from None
    if value.tzinfo is not None:
        raise argparse.ArgumentTypeError(f"timezone not supported, use a plain timestamp: {text!r}")
    return value


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py")
    parser.add_argument("--now", type=_timestamp, help="ISO timestamp to use as the current time")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, *positionals, by=False):
        p = sub.add_parser(name)
        for arg in positionals:
            p.add_argument(arg)
        if by:
            p.add_argument("--by", default="system")
        return p

    p = add("file", "passenger", "flight", "type", "tier", by=True)
    p.add_argument("--note", default="")
    p = add("list")
    p.add_argument("--status")
    p.add_argument("--tier")
    add("assign", "id", "agent", by=True)
    add("resolve", "id", by=True)
    add("close", "id", by=True)
    add("escalate", by=True)
    add("history", "id")
    add("report")
    add("seed", "path")
    return parser


def _claim_id(text):
    try:
        return int(text)
    except ValueError:
        raise claims.ClaimError(f"invalid claim id: {text!r}") from None


def _read_seed(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise claims.ClaimError(f"cannot read {path}: {exc}") from exc


def _run(args, conn, now):
    cmd = args.command
    if cmd == "file":
        claim = claims.file_claim(
            conn, args.passenger, args.flight, args.type, args.tier, args.note, now, args.by
        )
        print(claim["id"])
    elif cmd == "list":
        for c in claims.list_claims(conn, args.status, args.tier):
            print(
                f"{c['id']}  {c['status']}  {c['tier']}  {c['type']}  "
                f"{c['passenger']}  {c['flight']}  {c['deadline']}"
            )
    elif cmd == "assign":
        claims.assign(conn, _claim_id(args.id), args.agent, args.by, now)
    elif cmd == "resolve":
        claims.resolve(conn, _claim_id(args.id), args.by, now)
    elif cmd == "close":
        claims.close(conn, _claim_id(args.id), args.by, now)
    elif cmd == "escalate":
        ids = claims.escalate_overdue(conn, now, args.by)
        if ids:
            print("Escalated claims: " + ", ".join(str(i) for i in ids))
        else:
            print("No overdue claims to escalate.")
    elif cmd == "report":
        for tier, counts in claims.report(conn, now).items():
            print(f"{tier}  open {counts['open']}  overdue {counts['overdue']}")
    elif cmd == "history":
        for h in claims.get_history(conn, _claim_id(args.id)):
            print(
                f"{h['old_status'] or '-'} -> {h['new_status']}  by {h['changed_by']}  at {h['changed_at']}"
            )
    elif cmd == "seed":
        count = claims.file_claims(conn, _read_seed(args.path), now)
        print(f"Loaded {count} claims.")


def main(argv=None, db_path="orbit.db", clock=datetime.now):
    args = build_parser().parse_args(argv)
    now = args.now if args.now is not None else clock()
    try:
        conn = db.connect(db_path)
    except sqlite3.Error as exc:
        print(f"Error: cannot open database {db_path}: {exc}", file=sys.stderr)
        return 1
    try:
        _run(args, conn, now)
    except (claims.ClaimError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
