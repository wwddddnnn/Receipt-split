from pathlib import Path

import pytest

from receipt_split.ledger import Ledger, LedgerValidationError, SubmissionConflictError
from receipt_split.models import Clarification, ExpenseDraft, IndividualCharge


def _case_two_draft() -> ExpenseDraft:
    return ExpenseDraft(
        receipt_date="2026-02-15",
        total_cents=6_000,
        subtotal_cents=5_310,
        payer_id="Doris",
        individual_charges=[IndividualCharge(member_id="Amy", amount_cents=1_200)],
        remainder_member_ids=["Doris", "Amy", "Ben"],
    )


def _third_party_draft() -> ExpenseDraft:
    return ExpenseDraft(
        receipt_date="2026-02-16",
        total_cents=1_800,
        subtotal_cents=1_800,
        payer_id="Ben",
        remainder_member_ids=["Doris", "Amy"],
    )


@pytest.fixture
def ledger(tmp_path: Path):
    with Ledger(tmp_path / "ledger.sqlite") as instance:
        instance.seed_members(["Doris", "Amy", "Ben"])
        yield instance


def test_confirm_persists_calculated_shares_in_one_expense(ledger: Ledger):
    confirmed = ledger.confirm_expense(
        _case_two_draft(), note="Amy tea", submission_id="case-two"
    )

    assert confirmed.created is True
    assert confirmed.shares == {"Amy": 2_904, "Ben": 1_548, "Doris": 1_548}
    assert sum(confirmed.shares.values()) == 6_000
    assert ledger.connection.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 1
    assert ledger.connection.execute("SELECT COUNT(*) FROM expense_shares").fetchone()[0] == 3


def test_same_submission_and_content_is_idempotent(ledger: Ledger):
    first = ledger.confirm_expense(_case_two_draft(), note="Amy tea", submission_id="retry")
    retry = ledger.confirm_expense(_case_two_draft(), note="Amy tea", submission_id="retry")

    assert first.expense_id == retry.expense_id
    assert retry.created is False
    assert ledger.connection.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 1


def test_same_submission_with_different_content_conflicts(ledger: Ledger):
    ledger.confirm_expense(_case_two_draft(), note="Amy tea", submission_id="retry")

    with pytest.raises(SubmissionConflictError, match="different content"):
        ledger.confirm_expense(_case_two_draft(), note="changed note", submission_id="retry")


def test_unresolved_or_unknown_member_draft_is_not_written(ledger: Ledger):
    unresolved = _case_two_draft()
    unresolved.needs_clarification = [
        Clarification(field="receipt_date", reason="Date is unclear.")
    ]
    with pytest.raises(LedgerValidationError, match="resolve"):
        ledger.confirm_expense(unresolved, note="Amy tea", submission_id="unresolved")

    unknown = _case_two_draft()
    unknown.payer_id = "Unknown"
    with pytest.raises(LedgerValidationError, match="not in the ledger"):
        ledger.confirm_expense(unknown, note="Amy tea", submission_id="unknown")
    assert ledger.connection.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 0


def test_monthly_balances_and_transfers_use_expense_date(ledger: Ledger):
    ledger.confirm_expense(_case_two_draft(), note="February tea", submission_id="feb-one")
    ledger.confirm_expense(_third_party_draft(), note="February snack", submission_id="feb-two")
    march = _third_party_draft()
    march.receipt_date = "2026-03-01"
    ledger.confirm_expense(march, note="March snack", submission_id="mar-one")

    settlement = ledger.monthly_settlement("2026-02")
    assert settlement.balances_cents == {"Amy": -3_804, "Ben": 252, "Doris": 3_552}
    assert [transfer.model_dump() for transfer in settlement.transfers] == [
        {"from_member_id": "Amy", "to_member_id": "Ben", "amount_cents": 252},
        {"from_member_id": "Amy", "to_member_id": "Doris", "amount_cents": 3_552},
    ]
