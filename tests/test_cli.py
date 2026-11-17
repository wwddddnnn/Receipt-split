import json
from pathlib import Path

from receipt_split.cli import _evaluate, main
from receipt_split.models import ExpenseDraft
from receipt_split.splitting import calculate_shares


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_expected_answer_can_be_partial_for_targeted_evaluation():
    actual = {"total_cents": 5650, "payer_id": "Doris"}
    assert _evaluate(actual, {"total_cents": 5650}) == []
    assert _evaluate(actual, {"payer_id": "Ben"}) == [
        {"field": "payer_id", "expected": "Ben", "actual": "Doris"}
    ]


def test_missing_key_is_reported():
    assert _evaluate({}, {"receipt_date": "2026-01-01"}) == [
        {"field": "receipt_date", "expected": "2026-01-01", "actual": "<missing>"}
    ]


def test_assignment_lists_are_evaluated_without_regard_to_order():
    actual = {
        "individual_charges": [
            {"member_id": "Doris", "amount_cents": 5_996},
            {"member_id": "Amy", "amount_cents": 1_249},
            {"member_id": "Ben", "amount_cents": 1_149},
        ],
        "remainder_member_ids": ["Doris", "Amy", "Ben"],
    }
    expected = {
        "individual_charges": [
            {"member_id": "Amy", "amount_cents": 1_249},
            {"member_id": "Ben", "amount_cents": 1_149},
            {"member_id": "Doris", "amount_cents": 5_996},
        ],
        "remainder_member_ids": ["Amy", "Ben", "Doris"],
    }

    assert _evaluate(actual, expected) == []


def test_clarification_reason_text_does_not_affect_evaluation():
    expected = {
        "needs_clarification": [
            {"field": "receipt_date", "reason": "The receipt date is not visible."}
        ]
    }
    actual = {
        "needs_clarification": [
            {
                "field": "receipt_date",
                "reason": "The receipt date is not visible in the image. Please provide the date.",
            }
        ]
    }

    assert _evaluate(actual, expected) == []
    assert _evaluate({"needs_clarification": []}, expected) == [
        {
            "field": "needs_clarification",
            "expected": ["receipt_date"],
            "actual": [],
        }
    ]


def test_invalid_photo_creates_diagnostic_artifact(tmp_path: Path):
    artifact_root = tmp_path / "artifacts"
    status = main([
        str(tmp_path / "missing.jpg"), "--note", "split equally", "--uploader", "Doris",
        "--members", "Doris,Amy,Ben", "--runs-dir", str(artifact_root),
    ])
    assert status == 1
    errors = list(artifact_root.glob("*/error.json"))
    assert len(errors) == 1
    assert json.loads(errors[0].read_text())["error_type"] == "FileNotFoundError"


def test_expense_draft_rejects_non_iso_date():
    for invalid_date in ("01/01/2026", "20260101"):
        try:
            ExpenseDraft(receipt_date=invalid_date)
        except ValueError:
            pass
        else:
            raise AssertionError("expected validation error")


def test_case_four_expected_draft_matches_the_menu_assignment():
    expected = json.loads((PROJECT_ROOT / "examples" / "expected-case-04.json").read_text())
    draft = ExpenseDraft.model_validate(expected)

    assert draft.total_cents == 11_327
    assert draft.subtotal_cents == 8_394
    assert [(charge.member_id, charge.amount_cents) for charge in draft.individual_charges] == [
        ("Amy", 1_249),
        ("Ben", 1_149),
        ("Doris", 5_996),
    ]
    assert draft.remainder_member_ids == []
    assert calculate_shares(draft) == {"Amy": 1_685, "Ben": 1_551, "Doris": 8_091}


