"""Optional live-model tests, one parametrized test per example case."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from receipt_split.cli import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = PROJECT_ROOT / "examples"
CASES = json.loads((EXAMPLES_ROOT / "cases.json").read_text())


@pytest.mark.model_eval
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_model_evaluation_case(case: dict[str, object], pytestconfig: pytest.Config) -> None:
    """Run one selected fixture and retain exactly that fixture's API artifacts."""
    root: Path = pytestconfig._pytest_run_root  # type: ignore[attr-defined]
    case_root = root / "cases" / str(case["id"])
    photo_values = case.get("photos")
    if photo_values is None:
        photo_values = [case["photo"]]
    assert isinstance(photo_values, list)
    photo_arguments = [
        value
        for photo in photo_values
        for value in ("--photo", str(EXAMPLES_ROOT / str(photo)))
    ]
    status = main(
        [
            *photo_arguments,
            "--note",
            str(case["note"]),
            "--uploader",
            str(case["uploader_id"]),
            "--members",
            ",".join(case["members"]),  # type: ignore[arg-type]
            "--expected",
            str(EXAMPLES_ROOT / str(case["expected"])),
            "--output-dir",
            str(case_root),
        ]
    )
    pytestconfig._model_eval_artifacts.append(  # type: ignore[attr-defined]
        {"case_id": str(case["id"]), "artifact_dir": f"cases/{case['id']}"}
    )
    assert status == 0
