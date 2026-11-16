import pytest

from receipt_split.models import ExpenseDraft, IndividualCharge
from receipt_split.splitting import calculate_shares


def test_fees_are_distributed_in_proportion_to_pre_fee_items():
    draft = ExpenseDraft(
        total_cents=11_327,
        subtotal_cents=8_394,
        individual_charges=[
            IndividualCharge(member_id="Amy", amount_cents=1_249),
            IndividualCharge(member_id="Ben", amount_cents=1_149),
            IndividualCharge(member_id="Doris", amount_cents=5_996),
        ],
    )

    assert calculate_shares(draft) == {"Amy": 1_685, "Ben": 1_551, "Doris": 8_091}


def test_fee_rounding_uses_largest_remainder_then_member_id():
    draft = ExpenseDraft(
        total_cents=103,
        subtotal_cents=100,
        individual_charges=[
            IndividualCharge(member_id="Amy", amount_cents=50),
            IndividualCharge(member_id="Ben", amount_cents=50),
        ],
    )

    assert calculate_shares(draft) == {"Amy": 52, "Ben": 51}


def test_unassigned_subtotal_is_evenly_shared_before_fee_proration():
    draft = ExpenseDraft(
        total_cents=6_000,
        subtotal_cents=5_310,
        individual_charges=[IndividualCharge(member_id="Amy", amount_cents=1_200)],
        remainder_member_ids=["Doris", "Amy", "Ben"],
    )

    assert calculate_shares(draft) == {"Amy": 2_904, "Ben": 1_548, "Doris": 1_548}


def test_missing_subtotal_is_not_calculated():
    with pytest.raises(ValueError, match="subtotal_cents"):
        calculate_shares(ExpenseDraft(total_cents=100, remainder_member_ids=["Amy"]))
