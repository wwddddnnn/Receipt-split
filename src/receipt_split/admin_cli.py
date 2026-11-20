"""Maintainer-only commands for member login credentials."""

from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path

from .auth import AuthStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain receipt-split member credentials.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision = subparsers.add_parser("provision", help="create or update a member login")
    provision.add_argument("--database", type=Path, default=Path("data/ledger.sqlite"))
    provision.add_argument("--member-id", required=True)
    provision.add_argument("--display-name", required=True)
    reset = subparsers.add_parser("reset-password", help="reset an existing member password")
    reset.add_argument("--database", type=Path, default=Path("data/ledger.sqlite"))
    reset.add_argument("--member-id", required=True)
    args = parser.parse_args(argv)
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        parser.error("passwords do not match")
    try:
        with AuthStore(args.database) as store:
            if args.command == "provision":
                store.provision_member(args.member_id, args.display_name, password)
            else:
                store.reset_password(args.member_id, password)
    except ValueError as error:
        parser.error(str(error))
    print(f"Updated credentials for {args.member_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
