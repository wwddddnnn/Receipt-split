import json
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from receipt_split.ledger import (
    Ledger,
    LedgerValidationError,
    ReceiptPhoto,
    ReceiptProvenance,
    SubmissionConflictError,
)
from receipt_split.models import Clarification, ExpenseDraft, IndividualCharge


PHOTO_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-receipt"
SECOND_PHOTO_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-receipt-page-two"


def _case_two_draft() -> ExpenseDraft:
    return ExpenseDraft(
        receipt_date="2026-02-15",
        total_cents=6_000,
        subtotal_cents=5_310,
        payer_id="CindyTest",
        individual_charges=[IndividualCharge(member_id="AmyTest", amount_cents=1_200)],
        remainder_member_ids=["CindyTest", "AmyTest", "BenTest"],
    )


def _third_party_draft() -> ExpenseDraft:
    return ExpenseDraft(
        receipt_date="2026-02-16",
        total_cents=1_800,
        subtotal_cents=1_800,
        payer_id="BenTest",
        remainder_member_ids=["CindyTest", "AmyTest"],
    )


def _provenance(draft: ExpenseDraft, *, uploader_id: str = "CindyTest") -> ReceiptProvenance:
    return ReceiptProvenance(
        uploader_id=uploader_id,
        photos=[ReceiptPhoto(image_bytes=PHOTO_BYTES, mime_type="image/png")],
        raw_model_response={"id": "response-test", "output_text": "synthetic"},
        extractor_model="gpt-6-astra",
        prompt_version="test-v1",
    )


@pytest.fixture
def ledger(tmp_path: Path):
    with Ledger(tmp_path / "ledger.sqlite") as instance:
        instance.seed_members(["CindyTest", "AmyTest", "BenTest"])
        yield instance


def test_confirm_persists_public_and_original_receipt_data(ledger: Ledger):
    draft = _case_two_draft()
    confirmed = ledger.confirm_receipt(
        draft, note="AmyTest tea", submission_id="case-two", provenance=_provenance(draft)
    )

    assert confirmed.created is True
    assert confirmed.shares == {"AmyTest": 2_904, "BenTest": 1_548, "CindyTest": 1_548}
    assert confirmed.photo_paths == [f"objects/{sha256(PHOTO_BYTES).hexdigest()}.png"]
    row = ledger.connection.execute("SELECT * FROM receipts").fetchone()
    assert row["uploader_id"] == "CindyTest"
    assert json.loads(row["photo_paths_json"]) == confirmed.photo_paths
    assert "photo_bytes" not in row.keys()
    assert (ledger.database_path.parent / confirmed.photo_paths[0]).read_bytes() == PHOTO_BYTES
    assert json.loads(row["raw_model_response_json"])["id"] == "response-test"
    assert json.loads(row["confirmed_draft_json"]) == draft.model_dump(mode="json")
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipt_shares").fetchone()[0] == 3
    table_names = {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"members", "receipts", "receipt_shares"} <= table_names
    assert "expenses" not in table_names
    assert "expense_shares" not in table_names
    assert not {
        "photo_mime_types_json",
        "photo_sha256_json",
        "extracted_draft_json",
        "source_run_id",
    } & set(row.keys())


def test_same_submission_and_content_is_idempotent(ledger: Ledger):
    draft = _case_two_draft()
    first = ledger.confirm_receipt(
        draft, note="AmyTest tea", submission_id="retry", provenance=_provenance(draft)
    )
    retry = ledger.confirm_receipt(
        draft, note="AmyTest tea", submission_id="retry", provenance=_provenance(draft)
    )

    assert first.receipt_id == retry.receipt_id
    assert retry.created is False
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1


def test_receipt_can_store_ordered_paths_for_multiple_images(ledger: Ledger):
    draft = _case_two_draft()
    provenance = _provenance(draft)
    provenance.photos.append(ReceiptPhoto(image_bytes=SECOND_PHOTO_BYTES, mime_type="image/png"))

    receipt = ledger.confirm_receipt(
        draft, note="two-page receipt", submission_id="two-pages", provenance=provenance
    )

    expected_paths = [
        f"objects/{sha256(PHOTO_BYTES).hexdigest()}.png",
        f"objects/{sha256(SECOND_PHOTO_BYTES).hexdigest()}.png",
    ]
    assert receipt.photo_paths == expected_paths
    assert [
        (ledger.database_path.parent / path).read_bytes() for path in receipt.photo_paths
    ] == [PHOTO_BYTES, SECOND_PHOTO_BYTES]


def test_editing_date_moves_receipt_between_monthly_settlements(ledger: Ledger):
    original = ledger.confirm_receipt(
        _case_two_draft(), note="original", submission_id="move-month", provenance=_provenance(_case_two_draft())
    )
    assert ledger.monthly_settlement("2026-02").balances_cents["CindyTest"] == 4_452

    edited = Ledger.edit_draft(
        receipt_date="2026-03-01", total_cents=6_000, subtotal_cents=6_000, payer_id="AmyTest",
        individual_charges=[{"member_id": "AmyTest", "amount_cents": 1_000}],
        remainder_member_ids=["BenTest"],
    )
    ledger.update_receipt(original.receipt_id, version=original.version, draft=edited, note="moved", updated_by="CindyTest", confirm=True)

    assert ledger.monthly_settlement("2026-02").balances_cents == {"AmyTest": 0, "BenTest": 0, "CindyTest": 0}
    assert ledger.monthly_settlement("2026-03").balances_cents == {"AmyTest": 5_000, "BenTest": -5_000, "CindyTest": 0}


def test_legacy_blob_is_exported_and_replaced_with_path_list(tmp_path: Path):
    database_path = tmp_path / "legacy.sqlite"
    database = sqlite3.connect(database_path)
    database.executescript(
        """
        CREATE TABLE members (member_id TEXT PRIMARY KEY);
        CREATE TABLE receipts (
            receipt_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            receipt_date TEXT,
            total_cents INTEGER,
            subtotal_cents INTEGER,
            payer_id TEXT,
            uploader_id TEXT NOT NULL,
            note TEXT NOT NULL,
            submission_id TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            photo_bytes BLOB NOT NULL,
            photo_mime_type TEXT NOT NULL,
            photo_sha256 TEXT NOT NULL,
            raw_model_response_json TEXT NOT NULL,
            extracted_draft_json TEXT NOT NULL,
            confirmed_draft_json TEXT NOT NULL,
            extractor_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            source_run_id TEXT,
            created_at TEXT NOT NULL,
            confirmed_at TEXT
        );
        CREATE TABLE receipt_shares (
            receipt_id INTEGER NOT NULL,
            member_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            PRIMARY KEY (receipt_id, member_id)
        );
        """
    )
    draft = _case_two_draft().model_dump(mode="json")
    database.execute("INSERT INTO members(member_id) VALUES ('CindyTest')")
    database.execute(
        """
        INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1, "pending", None, 6_000, 5_310, "CindyTest", "CindyTest", "original note",
            "legacy", "old-hash", PHOTO_BYTES, "image/png", sha256(PHOTO_BYTES).hexdigest(),
            json.dumps({"id": "response"}), json.dumps(draft), json.dumps(draft), "test-model",
            "test-prompt", None, "2026-11-17T00:00:00+00:00", None,
        ),
    )
    database.commit()
    database.close()

    with Ledger(database_path) as ledger:
        row = ledger.connection.execute("SELECT * FROM receipts WHERE receipt_id = 1").fetchone()
        assert "photo_bytes" not in row.keys()
        assert json.loads(row["photo_paths_json"]) == [
            f"objects/{sha256(PHOTO_BYTES).hexdigest()}.png"
        ]
        assert (tmp_path / json.loads(row["photo_paths_json"])[0]).read_bytes() == PHOTO_BYTES


def test_same_submission_with_different_content_conflicts(ledger: Ledger):
    draft = _case_two_draft()
    ledger.confirm_receipt(
        draft, note="AmyTest tea", submission_id="retry", provenance=_provenance(draft)
    )
    with pytest.raises(SubmissionConflictError, match="different content"):
        ledger.confirm_receipt(
            draft, note="changed note", submission_id="retry", provenance=_provenance(draft)
        )


def test_unresolved_receipt_is_saved_pending_without_shares(ledger: Ledger):
    unresolved = _case_two_draft()
    unresolved.needs_clarification = [Clarification(field="receipt_date", reason="Date is unclear.")]
    pending = ledger.confirm_receipt(
        unresolved, note="AmyTest tea", submission_id="unresolved", provenance=_provenance(unresolved)
    )
    assert pending.status == "pending"
    assert pending.shares == {}
    row = ledger.connection.execute(
        "SELECT status, confirmed_at FROM receipts WHERE receipt_id = ?", (pending.receipt_id,)
    ).fetchone()
    assert dict(row) == {"status": "pending", "confirmed_at": None}
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipt_shares").fetchone()[0] == 0
    assert ledger.monthly_settlement("2026-02").balances_cents == {
        "AmyTest": 0,
        "BenTest": 0,
        "CindyTest": 0,
    }


def test_unknown_member_receipt_is_not_written(ledger: Ledger):
    unknown = _case_two_draft()
    with pytest.raises(LedgerValidationError, match="not in the ledger"):
        ledger.confirm_receipt(
            unknown, note="AmyTest tea", submission_id="unknown", provenance=_provenance(unknown, uploader_id="Unknown")
        )
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_monthly_balances_and_transfers_use_receipt_date(ledger: Ledger):
    case_two = _case_two_draft()
    ledger.confirm_receipt(
        case_two, note="February tea", submission_id="feb-one", provenance=_provenance(case_two)
    )
    third_party = _third_party_draft()
    ledger.confirm_receipt(
        third_party, note="February snack", submission_id="feb-two", provenance=_provenance(third_party, uploader_id="BenTest")
    )
    march = _third_party_draft()
    march.receipt_date = "2026-03-01"
    ledger.confirm_receipt(
        march, note="March snack", submission_id="mar-one", provenance=_provenance(march, uploader_id="BenTest")
    )

    settlement = ledger.monthly_settlement("2026-02")
    assert settlement.balances_cents == {"AmyTest": -3_804, "BenTest": 252, "CindyTest": 3_552}
    assert [transfer.model_dump() for transfer in settlement.transfers] == [
        {"from_member_id": "AmyTest", "to_member_id": "BenTest", "amount_cents": 252},
        {"from_member_id": "AmyTest", "to_member_id": "CindyTest", "amount_cents": 3_552},
    ]


def test_monthly_settlement_can_preserve_receipt_debt_links(ledger: Ledger):
    ben_owed_by_amy = ExpenseDraft(
        receipt_date="2026-04-01",
        total_cents=10_000,
        subtotal_cents=10_000,
        payer_id="BenTest",
        individual_charges=[IndividualCharge(member_id="AmyTest", amount_cents=10_000)],
    )
    cindy_owed_by_ben = ExpenseDraft(
        receipt_date="2026-04-02",
        total_cents=10_000,
        subtotal_cents=10_000,
        payer_id="CindyTest",
        individual_charges=[IndividualCharge(member_id="BenTest", amount_cents=10_000)],
    )
    ledger.confirm_receipt(
        ben_owed_by_amy,
        note="AmyTest owes BenTest",
        submission_id="amy-ben-chain",
        provenance=_provenance(ben_owed_by_amy, uploader_id="BenTest"),
    )
    ledger.confirm_receipt(
        cindy_owed_by_ben,
        note="BenTest owes CindyTest",
        submission_id="ben-cindy-chain",
        provenance=_provenance(cindy_owed_by_ben),
    )

    group_netting = ledger.monthly_settlement("2026-04", strategy="group_netting")
    receipt_links = ledger.monthly_settlement("2026-04", strategy="receipt_links")

    assert group_netting.balances_cents == receipt_links.balances_cents == {
        "AmyTest": -10_000,
        "BenTest": 0,
        "CindyTest": 10_000,
    }
    assert [transfer.model_dump() for transfer in group_netting.transfers] == [
        {"from_member_id": "AmyTest", "to_member_id": "CindyTest", "amount_cents": 10_000}
    ]
    assert [transfer.model_dump() for transfer in receipt_links.transfers] == [
        {"from_member_id": "AmyTest", "to_member_id": "BenTest", "amount_cents": 10_000},
        {"from_member_id": "BenTest", "to_member_id": "CindyTest", "amount_cents": 10_000},
    ]
