"""Deterministic share calculations for an editable expense draft."""

from __future__ import annotations

from collections import defaultdict

from .models import ExpenseDraft


def _split_evenly(total_cents: int, member_ids: list[str]) -> dict[str, int]:
    if not member_ids:
        raise ValueError("a positive unassigned subtotal needs remainder_member_ids")
    if len(set(member_ids)) != len(member_ids):
        raise ValueError("remainder_member_ids must not contain duplicates")
    quotient, remainder = divmod(total_cents, len(member_ids))
    return {
        member_id: quotient + int(index < remainder)
        for index, member_id in enumerate(sorted(member_ids))
    }


def _split_proportionally(total_cents: int, bases: dict[str, int]) -> dict[str, int]:
    """Allocate cents by base amount, using largest remainder then member ID."""
    total_base = sum(bases.values())
    if total_cents == 0:
        return {member_id: 0 for member_id in bases}
    if total_base <= 0:
        raise ValueError("positive fees require a positive item subtotal")

    shares = {
        member_id: total_cents * base_cents // total_base
        for member_id, base_cents in bases.items()
    }
    leftover = total_cents - sum(shares.values())
    priority = sorted(
        bases,
        key=lambda member_id: (
            -(total_cents * bases[member_id] % total_base),
            member_id,
        ),
    )
    for member_id in priority[:leftover]:
        shares[member_id] += 1
    return shares


def calculate_shares(draft: ExpenseDraft) -> dict[str, int]:
    """Return final shares after distributing all fees proportionally to item bases.

    `individual_charges` are direct pre-fee items. Any unassigned portion of
    `subtotal_cents` is first shared evenly among `remainder_member_ids`. The
    difference between `total_cents` and `subtotal_cents` (tax, tip, service,
    delivery, and similar adjustments) is then distributed in proportion to
    those item bases. The result always sums exactly to `total_cents`.
    """
    if draft.total_cents is None or draft.subtotal_cents is None:
        raise ValueError("total_cents and subtotal_cents must be confirmed")
    if draft.subtotal_cents > draft.total_cents:
        raise ValueError("subtotal_cents cannot exceed total_cents")

    # A receipt with no individually assigned item is a true equal split. Split
    # the paid total directly so subtotal rounding cannot be amplified when fees
    # are later prorated.
    if not draft.individual_charges and draft.remainder_member_ids:
        return dict(sorted(_split_evenly(draft.total_cents, draft.remainder_member_ids).items()))

    bases: dict[str, int] = defaultdict(int)
    for charge in draft.individual_charges:
        bases[charge.member_id] += charge.amount_cents
    assigned_subtotal = sum(bases.values())
    if assigned_subtotal > draft.subtotal_cents:
        raise ValueError("individual charges cannot exceed subtotal_cents")

    unassigned_subtotal = draft.subtotal_cents - assigned_subtotal
    if unassigned_subtotal:
        for member_id, amount_cents in _split_evenly(
            unassigned_subtotal, draft.remainder_member_ids
        ).items():
            bases[member_id] += amount_cents
    elif draft.remainder_member_ids:
        raise ValueError("remainder_member_ids must be empty when all subtotal is assigned")

    fees = draft.total_cents - draft.subtotal_cents
    fee_shares = _split_proportionally(fees, dict(bases))
    final_shares = {
        member_id: bases[member_id] + fee_shares[member_id]
        for member_id in bases
    }
    if sum(final_shares.values()) != draft.total_cents:
        raise AssertionError("share calculation did not preserve the total")
    return dict(sorted(final_shares.items()))
