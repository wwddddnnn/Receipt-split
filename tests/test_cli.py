import json
from pathlib import Path

from receipt_split.cli import _evaluate, main
from receipt_split.models import ExpenseDraft


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


def test_invalid_photo_creates_diagnostic_artifact(tmp_path: Path):
    artifact_root = tmp_path / "artifacts"
    status = main([
        str(tmp_path / "missing.jpg"), "--note", "split equally", "--uploader", "Doris",
        "--members", "Doris,Amy,Ben", "--artifacts-dir", str(artifact_root),
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
    assert [(charge.member_id, charge.amount_cents) for charge in draft.individual_charges] == [
        ("Amy", 1_249),
        ("Ben", 1_149),
    ]
    assert draft.remainder_member_ids == ["Doris"]
    assert sum(charge.amount_cents for charge in draft.individual_charges) == 2_398


