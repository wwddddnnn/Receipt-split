import json
from hashlib import sha256
from pathlib import Path

import pytest

from receipt_split.ledger import (
    Ledger,
    LedgerValidationError,
    ReceiptProvenance,
    SubmissionConflictError,
)
from receipt_split.models import Clarification, ExpenseDraft, IndividualCharge


PHOTO_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-receipt"


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
        photo_bytes=PHOTO_BYTES,
        photo_mime_type="image/png",
        raw_model_response={"id": "response-test", "output_text": "synthetic"},
        extracted_draft=draft,
        extractor_model="gpt-6-astra",
        prompt_version="test-v1",
        source_run_id="20261117T000000Z-test",
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
    assert confirmed.photo_sha256 == sha256(PHOTO_BYTES).hexdigest()
    row = ledger.connection.execute("SELECT * FROM receipts").fetchone()
    assert row["uploader_id"] == "CindyTest"
    assert row["photo_bytes"] == PHOTO_BYTES
    assert row["photo_mime_type"] == "image/png"
    assert json.loads(row["raw_model_response_json"])["id"] == "response-test"
    assert json.loads(row["extracted_draft_json"])["total_cents"] == 6_000
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
