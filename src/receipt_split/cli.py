"""Command-line interface for phase-one model extraction and evaluation."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time
import uuid
from typing import Any

from pydantic import ValidationError

from .extractor import DEFAULT_MODEL, PROMPT_VERSION, extract_expense
from .models import ExpenseDraft


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_json"):
        return json.loads(value.to_json())
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n")


def _artifact_dir(root: Path) -> Path:
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _publish_latest(root: Path, artifacts: Path) -> None:
    """Point `artifacts/latest` at the newest run without deleting run history."""
    latest = root / "latest"
    if latest.exists() and not latest.is_symlink():
        return
    if latest.is_symlink():
        latest.unlink()
    latest.symlink_to(artifacts.name, target_is_directory=True)


def _evaluate(actual: Any, expected: Any, path: str = "") -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{"field": path or "$", "expected": expected, "actual": actual}]
        for key, expected_value in expected.items():
            child_path = f"{path}.{key}" if path else key
            if key not in actual:
                differences.append({"field": child_path, "expected": expected_value, "actual": "<missing>"})
            else:
                differences.extend(_evaluate(actual[key], expected_value, child_path))
    elif isinstance(expected, list):
        if actual != expected:
            differences.append({"field": path, "expected": expected, "actual": actual})
    elif actual != expected:
        differences.append({"field": path, "expected": expected, "actual": actual})
    return differences


def _parse_members(raw: str) -> list[str]:
    members = [member.strip() for member in raw.split(",") if member.strip()]
    if not members:
        raise argparse.ArgumentTypeError("provide at least one member ID")
    return members


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract an editable receipt expense draft.")
    parser.add_argument("photo", type=Path, help="local JPG or PNG receipt")
    parser.add_argument("--note", required=True, help="split instruction shown with the receipt")
    parser.add_argument("--uploader", required=True, help="uploader/member ID used for 'I/me'")
    parser.add_argument("--members", required=True, type=_parse_members, help="comma-separated member IDs")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"vision model (default: {DEFAULT_MODEL})")
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts"), help="directory for each run")
    parser.add_argument("--expected", type=Path, help="optional expected draft JSON used only for evaluation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    artifacts = _artifact_dir(args.artifacts_dir)
    metadata = {
        "started_at": datetime.now(UTC).isoformat(),
        "photo": str(args.photo),
        "note": args.note,
        "uploader_id": args.uploader,
        "members": args.members,
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
    }
    _write_json(artifacts / "run.json", metadata)
    started = time.perf_counter()
    try:
        draft, raw_response = extract_expense(
            args.photo, args.note, args.uploader, args.members, model=args.model
        )
    except Exception as error:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        _write_json(artifacts / "error.json", {"error_type": type(error).__name__, "message": str(error), "elapsed_ms": elapsed_ms})
        _publish_latest(args.artifacts_dir, artifacts)
        print(f"Extraction failed; diagnostic artifact: {artifacts.resolve()}", file=sys.stderr)
        return 1

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    _write_json(artifacts / "raw_response.json", raw_response)
    _write_json(artifacts / "draft.json", draft.model_dump(mode="json"))
    _write_json(artifacts / "timing.json", {"elapsed_ms": elapsed_ms, "model": args.model, "prompt_version": PROMPT_VERSION})
    print(json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2))
    _publish_latest(args.artifacts_dir, artifacts)
    print(f"Artifacts: {artifacts.resolve()}", file=sys.stderr)

    if args.expected:
        try:
            expected = json.loads(args.expected.read_text())
            ExpenseDraft.model_validate(expected)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            print(f"Invalid expected-answer file: {error}", file=sys.stderr)
            return 2
        differences = _evaluate(draft.model_dump(mode="json"), expected)
        report = {"passed": not differences, "differences": differences}
        _write_json(artifacts / "evaluation.json", report)
        if differences:
            print("Evaluation failed; see evaluation.json", file=sys.stderr)
            return 3
        print("Evaluation passed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
