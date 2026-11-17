"""CLI commands for initializing and confirming the SQLite ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ledger import Ledger
from .models import ExpenseDraft


def _members(raw: str) -> list[str]:
    return [member.strip() for member in raw.split(",") if member.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the receipt-split SQLite ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="create the ledger and seed members")
    init.add_argument("--database", type=Path, default=Path("ledger.sqlite"))
    init.add_argument("--members", required=True, type=_members)
    confirm = subparsers.add_parser("confirm", help="confirm a reviewed draft into the ledger")
    confirm.add_argument("draft", type=Path)
    confirm.add_argument("--database", type=Path, default=Path("ledger.sqlite"))
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--submission-id", required=True)
    monthly = subparsers.add_parser("monthly", help="show balances and transfer suggestions")
    monthly.add_argument("month", help="YYYY-MM")
    monthly.add_argument("--database", type=Path, default=Path("ledger.sqlite"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with Ledger(args.database) as ledger:
        if args.command == "init":
            ledger.seed_members(args.members)
            print(json.dumps({"database": str(args.database), "members": args.members}))
        elif args.command == "confirm":
            draft = ExpenseDraft.model_validate_json(args.draft.read_text())
            result = ledger.confirm_expense(
                draft, note=args.note, submission_id=args.submission_id
            )
            print(result.model_dump_json(indent=2))
        else:
            print(ledger.monthly_settlement(args.month).model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
