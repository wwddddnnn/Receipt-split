from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from receipt_split.models import Clarification, ExpenseDraft
from receipt_split.web import create_app


PNG_ONE = b"\x89PNG\r\n\x1a\npage-one"
PNG_TWO = b"\x89PNG\r\n\x1a\npage-two"
SUBMISSION_ID = "0c7d96d9-e86d-4dfb-9e9b-39b470baf7dd"


def _complete_draft() -> ExpenseDraft:
    return ExpenseDraft(
        receipt_date="2026-11-17",
        total_cents=6_000,
        subtotal_cents=6_000,
        payer_id="CindyTest",
        remainder_member_ids=["AmyTest", "BenTest"],
    )


def _client(monkeypatch, tmp_path: Path, extractor):
    monkeypatch.setenv("RECEIPT_SPLIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RECEIPT_SPLIT_DB_PATH", str(tmp_path / "data" / "ledger.sqlite"))
    monkeypatch.setenv("RECEIPT_SPLIT_MEMBERS", "AmyTest,BenTest,CindyTest,DanTest")
    monkeypatch.setattr("receipt_split.web.extract_expense", extractor)
    return TestClient(create_app())


def _submission(client: TestClient):
    return client.post(
        "/receipts",
        data={"uploader_id": "CindyTest", "note": "AmyTest and BenTest split this."},
        files=[
            ("photos", ("page-1.png", PNG_ONE, "image/png")),
            ("photos", ("page-2.png", PNG_TWO, "image/png")),
        ],
        headers={"Idempotency-Key": SUBMISSION_ID},
    )


def test_receipt_upload_extracts_multiple_images_and_confirms(monkeypatch, tmp_path: Path):
    def extractor(*_args, **_kwargs):
        return _complete_draft(), {"response_id": "test-response"}

    with _client(monkeypatch, tmp_path, extractor) as client:
        assert client.get("/members").json() == {
            "members": ["AmyTest", "BenTest", "CindyTest", "DanTest"]
        }
        response = _submission(client)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "confirmed"
        assert body["submission_id"] == SUBMISSION_ID
        assert body["note"] == "AmyTest and BenTest split this."
        assert body["shares"] == {"AmyTest": 3000, "BenTest": 3000}
        assert len(body["photo_urls"]) == 2
        assert client.get(body["photo_urls"][0]).content == PNG_ONE
        assert client.get(body["photo_urls"][1]).content == PNG_TWO
        assert client.get("/settlements/2026-11").json()["balances_cents"] == {
            "AmyTest": -3000,
            "BenTest": -3000,
            "CindyTest": 6000,
            "DanTest": 0,
        }

    database = sqlite3.connect(tmp_path / "data" / "ledger.sqlite")
    row = database.execute(
        "SELECT status, note, photo_paths_json, raw_model_response_json FROM receipts"
    ).fetchone()
    assert row[0] == "confirmed"
    assert row[1] == "AmyTest and BenTest split this."
    assert row[2].count("objects/") == 2
    assert row[3] == '{"response_id":"test-response"}'


def test_extraction_error_returns_json_and_service_stays_available(monkeypatch, tmp_path: Path):
    def extractor(*_args, **_kwargs):
        raise RuntimeError("model timeout")

    with _client(monkeypatch, tmp_path, extractor) as client:
        response = _submission(client)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "extraction_failed"
        assert client.get("/healthz").json() == {"status": "ok"}

    database = sqlite3.connect(tmp_path / "data" / "ledger.sqlite")
    assert database.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_unconfirmed_model_draft_returns_json_without_writing_receipt(monkeypatch, tmp_path: Path):
    def extractor(*_args, **_kwargs):
        draft = _complete_draft()
        draft.needs_clarification = [
            Clarification(field="total_cents", reason="The total is unclear.")
        ]
        return draft, {"response_id": "unclear"}

    with _client(monkeypatch, tmp_path, extractor) as client:
        response = _submission(client)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "receipt_needs_clarification"

    database = sqlite3.connect(tmp_path / "data" / "ledger.sqlite")
    assert database.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
