"""Pytest controls and run summaries for optional live model evaluations."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import uuid

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-model-evals",
        action="store_true",
        default=False,
        help="call the live vision model for the selected examples/cases.json cases",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "model_eval: live model evaluation; skipped unless --run-model-evals is supplied",
    )
    config._model_eval_artifacts: list[dict[str, str]] = []  # type: ignore[attr-defined]
    config._pytest_reports: list[dict[str, str]] = []  # type: ignore[attr-defined]
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    root = Path.cwd() / "runs" / run_id
    root.mkdir(parents=True, exist_ok=False)
    config._pytest_run_root = root  # type: ignore[attr-defined]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-model-evals"):
        return
    skip_live_eval = pytest.mark.skip(reason="pass --run-model-evals to call the live model")
    for item in items:
        if "model_eval" in item.keywords:
            item.add_marker(skip_live_eval)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and report.skipped):
        item.config._pytest_reports.append(  # type: ignore[attr-defined]
            {"nodeid": report.nodeid, "outcome": report.outcome}
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    root: Path = session.config._pytest_run_root  # type: ignore[attr-defined]
    summary = {
        "finished_at": datetime.now(UTC).isoformat(),
        "exit_status": exitstatus,
        "selected_case_artifacts": session.config._model_eval_artifacts,  # type: ignore[attr-defined]
        "pytest_results": session.config._pytest_reports,  # type: ignore[attr-defined]
    }
    (root / "pytest-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
