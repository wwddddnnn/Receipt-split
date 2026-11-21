import json
from pathlib import Path

from receipt_split.cli import _evaluate, main
from receipt_split.models import ExpenseDraft
from receipt_split.splitting import calculate_shares


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_expected_answer_can_be_partial_for_targeted_evaluation():
    actual = {"total_cents": 5650, "payer_id": "CindyTest"}
    assert _evaluate(actual, {"total_cents": 5650}) == []
    assert _evaluate(actual, {"payer_id": "BenTest"}) == [
        {"field": "payer_id", "expected": "BenTest", "actual": "CindyTest"}
    ]


def test_missing_key_is_reported():
    assert _evaluate({}, {"receipt_date": "2026-01-01"}) == [
        {"field": "receipt_date", "expected": "2026-01-01", "actual": "<missing>"}
    ]


def test_assignment_lists_are_evaluated_without_regard_to_order():
    actual = {
        "individual_charges": [
            {"member_id": "CindyTest", "amount_cents": 5_996},
            {"member_id": "AmyTest", "amount_cents": 1_249},
            {"member_id": "BenTest", "amount_cents": 1_149},
        ],
        "remainder_member_ids": ["CindyTest", "AmyTest", "BenTest"],
    }
    expected = {
        "individual_charges": [
            {"member_id": "AmyTest", "amount_cents": 1_249},
            {"member_id": "BenTest", "amount_cents": 1_149},
            {"member_id": "CindyTest", "amount_cents": 5_996},
        ],
        "remainder_member_ids": ["AmyTest", "BenTest", "CindyTest"],
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
        str(tmp_path / "missing.jpg"), "--note", "split equally", "--uploader", "CindyTest",
        "--members", "CindyTest,AmyTest,BenTest", "--runs-dir", str(artifact_root),
    ])
    assert status == 1
    errors = list(artifact_root.glob("*/error.json"))
    assert len(errors) == 1
    assert json.loads(errors[0].read_text())["error_type"] == "FileNotFoundError"


def test_repeated_photo_option_passes_ordered_images_to_extractor(monkeypatch, tmp_path: Path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    seen: dict[str, object] = {}

    def extractor(photos, *_args, **_kwargs):
        seen["photos"] = photos
        return ExpenseDraft(
            receipt_date="2026-11-17",
            total_cents=100,
            subtotal_cents=100,
            payer_id="CindyTest",
            remainder_member_ids=["CindyTest"],
        ), {"response_id": "test"}

    monkeypatch.setattr("receipt_split.cli.extract_expense", extractor)
    artifact_root = tmp_path / "artifacts"
    status = main([
        "--photo", str(first), "--photo", str(second), "--note", "split equally",
        "--uploader", "CindyTest", "--members", "CindyTest", "--runs-dir", str(artifact_root),
    ])

    assert status == 0
    assert seen["photos"] == [first, second]
    run = next(artifact_root.glob("*/run.json"))
    assert json.loads(run.read_text())["photos"] == [str(first), str(second)]


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
        ("AmyTest", 1_249),
        ("BenTest", 1_149),
        ("CindyTest", 5_996),
    ]
    assert draft.remainder_member_ids == []
    assert calculate_shares(draft) == {"AmyTest": 1_685, "BenTest": 1_551, "CindyTest": 8_091}
