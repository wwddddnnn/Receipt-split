from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from receipt_split.models import Clarification, ExpenseDraft
from receipt_split.auth import AuthStore
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


@contextmanager
def _client(monkeypatch, tmp_path: Path, extractor):
    monkeypatch.setenv("RECEIPT_SPLIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RECEIPT_SPLIT_DB_PATH", str(tmp_path / "data" / "ledger.sqlite"))
    monkeypatch.setenv("RECEIPT_SPLIT_MEMBERS", "AmyTest,BenTest,CindyTest,DanTest")
    monkeypatch.setattr("receipt_split.web.extract_expense", extractor)
    database_path = tmp_path / "data" / "ledger.sqlite"
    with TestClient(create_app()) as client:
        with AuthStore(database_path) as store:
            for member_id in ("AmyTest", "BenTest", "CindyTest", "DanTest"):
                store.provision_member(member_id, member_id.removesuffix("Test"), f"{member_id}-password")
        yield client


def _login(client: TestClient, member_id: str = "CindyTest"):
    response = client.post("/auth/login", json={"member_id": member_id, "password": f"{member_id}-password"})
    assert response.status_code == 200
    return response


def _submission(client: TestClient):
    return client.post(
        "/receipts",
        data={"payer_id": "CindyTest", "note": "AmyTest and BenTest split this."},
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
        assert client.get("/members").status_code == 401
        _login(client)
        assert client.get("/members").json() == {"members": [
            {"member_id": "AmyTest", "display_name": "Amy"},
            {"member_id": "BenTest", "display_name": "Ben"},
            {"member_id": "CindyTest", "display_name": "Cindy"},
            {"member_id": "DanTest", "display_name": "Dan"},
        ]}
        response = _submission(client)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "confirmed"
        assert body["submission_id"] == SUBMISSION_ID
        assert body["note"] == "AmyTest and BenTest split this."
        assert body["created_by"] == "CindyTest"
        assert body["shares"] == {"AmyTest": 3000, "BenTest": 3000}
        assert len(body["photo_urls"]) == 2
        assert client.get(body["photo_urls"][0]).content == PNG_ONE
        assert client.get(body["photo_urls"][1]).content == PNG_TWO
        settlement = client.get("/settlements/2026-11").json()
        assert settlement["my_balance_cents"] == 6000
        assert settlement["my_member_id"] == "CindyTest"
        assert {item["member_id"] for item in settlement["balances"]} == {"AmyTest", "BenTest", "CindyTest"}
        assert "DanTest" not in {item["member_id"] for item in settlement["balances"]}
        assert {item["counterparty_member_id"] for item in settlement["transfers"]} == {"AmyTest", "BenTest"}

        client.post("/auth/logout")
        _login(client, "DanTest")
        assert client.get("/receipts").json() == []
        assert client.get(f"/receipts/{body['receipt_id']}").status_code == 404
        assert client.get(body["photo_urls"][0]).status_code == 404

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
        _login(client)
        response = _submission(client)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "extraction_failed"
        assert client.get("/healthz").json() == {"status": "ok"}

    database = sqlite3.connect(tmp_path / "data" / "ledger.sqlite")
    assert database.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_unconfirmed_model_draft_is_saved_as_pending_receipt(monkeypatch, tmp_path: Path):
    def extractor(*_args, **_kwargs):
        draft = _complete_draft()
        draft.needs_clarification = [
            Clarification(field="total_cents", reason="The total is unclear.")
        ]
        return draft, {"response_id": "unclear"}

    with _client(monkeypatch, tmp_path, extractor) as client:
        _login(client)
        response = _submission(client)
        assert response.status_code == 201
        assert response.json()["status"] == "pending"
        assert response.json()["needs_clarification"] == [{"field": "total_cents", "reason": "The total is unclear."}]

    database = sqlite3.connect(tmp_path / "data" / "ledger.sqlite")
    assert database.execute("SELECT status FROM receipts").fetchone()[0] == "pending"


def test_login_failure_does_not_leak_whether_account_exists(monkeypatch, tmp_path: Path):
    def extractor(*_args, **_kwargs):
        return _complete_draft(), {"response_id": "unused"}

    with _client(monkeypatch, tmp_path, extractor) as client:
        for member_id, password in (("CindyTest", "incorrect"), ("Unknown", "incorrect")):
            response = client.post("/auth/login", json={"member_id": member_id, "password": password})
            assert response.status_code == 401
            assert response.json()["error"]["message"] == "账号或密码错误"


def test_receipt_edit_recalculates_shares_and_rejects_stale_version(monkeypatch, tmp_path: Path):
    def extractor(*_args, **_kwargs):
        return _complete_draft(), {"response_id": "edit-test"}

    with _client(monkeypatch, tmp_path, extractor) as client:
        _login(client)
        original = _submission(client).json()
        payload = {
            "version": original["version"], "receipt_date": "2026-12-01", "total_cents": 6000,
            "payer_id": "AmyTest", "note": "edited note",
            "individual_charges": [{"member_id": "AmyTest", "amount_cents": 1000}],
            "remainder_member_ids": ["BenTest"],
        }
        assert client.post(f"/receipts/{original['receipt_id']}/preview", json=payload).json() == {"AmyTest": 1000, "BenTest": 5000}
        saved = client.put(f"/receipts/{original['receipt_id']}", json=payload)
        assert saved.status_code == 200
        assert saved.json()["shares"] == {"AmyTest": 1000, "BenTest": 5000}
        assert saved.json()["version"] == original["version"] + 1
        assert client.put(f"/receipts/{original['receipt_id']}", json=payload).status_code == 409


def test_receipt_edit_can_save_incomplete_draft_before_confirmation(monkeypatch, tmp_path: Path):
    def extractor(*_args, **_kwargs):
        return _complete_draft(), {"response_id": "draft-edit"}

    with _client(monkeypatch, tmp_path, extractor) as client:
        _login(client)
        original = _submission(client).json()
        draft = client.put(f"/receipts/{original['receipt_id']}/draft", json={
            "version": original["version"], "receipt_date": None, "total_cents": None,
            "payer_id": None, "note": "come back later", "individual_charges": [], "remainder_member_ids": [],
        })
        assert draft.status_code == 200
        assert draft.json()["status"] == "pending"
        assert {item["field"] for item in draft.json()["needs_clarification"]} >= {"receipt_date", "total_cents", "payer_id"}
