from __future__ import annotations

from pathlib import Path

from receipt_split.admin_cli import main
from receipt_split.auth import AuthStore
from receipt_split.ledger import Ledger


def test_provision_uses_web_database_environment(monkeypatch, tmp_path: Path):
    database_path = tmp_path / "data" / "ledger.sqlite"
    database_path.parent.mkdir()
    with Ledger(database_path) as ledger:
        ledger.seed_members(["CindyTest"])
    monkeypatch.setenv("RECEIPT_SPLIT_DB_PATH", str(database_path))
    monkeypatch.setattr("receipt_split.admin_cli.getpass", lambda _: "a-safe-password")

    assert main(["provision", "--member-id", "CindyTest", "--display-name", "Cindy"]) == 0

    with AuthStore(database_path) as store:
        assert store.authenticate("CindyTest", "a-safe-password") is not None
