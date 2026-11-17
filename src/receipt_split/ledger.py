"""SQLite-backed receipt ledger with immutable source snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from pydantic import BaseModel, Field, field_validator

from .models import ExpenseDraft
from .splitting import calculate_shares


class LedgerValidationError(ValueError):
    """Raised when a receipt is not eligible to be confirmed."""


class SubmissionConflictError(ValueError):
    """Raised when a submission ID is retried with different content."""


class ReceiptProvenance(BaseModel):
    """Original upload and extraction data retained with a confirmed receipt."""

    uploader_id: str = Field(min_length=1)
    photo_bytes: bytes = Field(min_length=1)
    photo_mime_type: str = Field(pattern=r"^image/(jpeg|png)$")
    raw_model_response: Any
    extracted_draft: ExpenseDraft
    extractor_model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    source_run_id: str | None = None

    @field_validator("uploader_id")
    @classmethod
    def strip_uploader_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class ConfirmedReceipt(BaseModel):
    receipt_id: int
    submission_id: str
    receipt_date: str
    total_cents: int
    subtotal_cents: int
    payer_id: str
    uploader_id: str
    note: str
    photo_sha256: str
    shares: dict[str, int]
    created: bool


class TransferSuggestion(BaseModel):
    from_member_id: str
    to_member_id: str
    amount_cents: int = Field(gt=0)


class MonthlySettlement(BaseModel):
    month: str
    balances_cents: dict[str, int]
    transfers: list[TransferSuggestion]


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS members (
    member_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id INTEGER PRIMARY KEY,
    receipt_date TEXT NOT NULL,
    total_cents INTEGER NOT NULL CHECK (total_cents > 0),
    subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents > 0 AND subtotal_cents <= total_cents),
    payer_id TEXT NOT NULL REFERENCES members(member_id),
    uploader_id TEXT NOT NULL REFERENCES members(member_id),
    note TEXT NOT NULL,
    submission_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    photo_bytes BLOB NOT NULL,
    photo_mime_type TEXT NOT NULL CHECK (photo_mime_type IN ('image/jpeg', 'image/png')),
    photo_sha256 TEXT NOT NULL,
    raw_model_response_json TEXT NOT NULL,
    extracted_draft_json TEXT NOT NULL,
    confirmed_draft_json TEXT NOT NULL,
    extractor_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    source_run_id TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipt_shares (
    receipt_id INTEGER NOT NULL REFERENCES receipts(receipt_id) ON DELETE CASCADE,
    member_id TEXT NOT NULL REFERENCES members(member_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    PRIMARY KEY (receipt_id, member_id)
);

CREATE INDEX IF NOT EXISTS receipts_receipt_date_idx ON receipts(receipt_date);
CREATE INDEX IF NOT EXISTS receipt_shares_member_idx ON receipt_shares(member_id);
"""


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class Ledger:
    """A single-process SQLite ledger with idempotent receipt confirmation."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def seed_members(self, member_ids: list[str]) -> None:
        normalized = sorted({member_id.strip() for member_id in member_ids if member_id.strip()})
        if not normalized:
            raise LedgerValidationError("seed at least one member")
        with self._transaction():
            self.connection.executemany(
                "INSERT OR IGNORE INTO members(member_id) VALUES (?)",
                ((member_id,) for member_id in normalized),
            )

    def confirm_receipt(
        self,
        draft: ExpenseDraft,
        *,
        note: str,
        submission_id: str,
        provenance: ReceiptProvenance,
    ) -> ConfirmedReceipt:
        """Recalculate and atomically persist one reviewed receipt plus its source."""
        if not note.strip():
            raise LedgerValidationError("note must not be empty")
        if not submission_id.strip():
            raise LedgerValidationError("submission_id must not be empty")
        if draft.needs_clarification:
            raise LedgerValidationError("resolve needs_clarification before confirming")
        if draft.receipt_date is None or draft.total_cents is None or draft.subtotal_cents is None:
            raise LedgerValidationError("receipt_date, total_cents, and subtotal_cents are required")
        if draft.payer_id is None:
            raise LedgerValidationError("payer_id is required")
        try:
            shares = calculate_shares(draft)
        except ValueError as error:
            raise LedgerValidationError(str(error)) from error

        photo_sha256 = sha256(provenance.photo_bytes).hexdigest()
        extracted_draft = provenance.extracted_draft.model_dump(mode="json")
        confirmed_draft = draft.model_dump(mode="json")
        payload = {
            "confirmed_draft": confirmed_draft,
            "extracted_draft": extracted_draft,
            "note": note,
            "photo_sha256": photo_sha256,
            "photo_mime_type": provenance.photo_mime_type,
            "raw_model_response": provenance.raw_model_response,
            "uploader_id": provenance.uploader_id,
            "extractor_model": provenance.extractor_model,
            "prompt_version": provenance.prompt_version,
            "source_run_id": provenance.source_run_id,
            "shares": shares,
        }
        content_hash = sha256(_compact_json(payload).encode()).hexdigest()
        raw_model_response_json = _compact_json(provenance.raw_model_response)
        extracted_draft_json = _compact_json(extracted_draft)
        confirmed_draft_json = _compact_json(confirmed_draft)
        timestamp = datetime.now(UTC).isoformat()

        with self._transaction():
            existing = self.connection.execute(
                "SELECT receipt_id, content_hash FROM receipts WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise SubmissionConflictError(
                        "submission_id already exists with different content"
                    )
                return self._get_receipt(existing["receipt_id"], created=False)

            required_members = set(shares) | {draft.payer_id, provenance.uploader_id}
            placeholders = ",".join("?" for _ in required_members)
            rows = self.connection.execute(
                f"SELECT member_id FROM members WHERE member_id IN ({placeholders})",
                tuple(sorted(required_members)),
            ).fetchall()
            known_members = {row["member_id"] for row in rows}
            missing_members = sorted(required_members - known_members)
            if missing_members:
                raise LedgerValidationError(
                    f"members are not in the ledger: {', '.join(missing_members)}"
                )

            cursor = self.connection.execute(
                """
                INSERT INTO receipts(
                    receipt_date, total_cents, subtotal_cents, payer_id, uploader_id, note,
                    submission_id, content_hash, photo_bytes, photo_mime_type, photo_sha256,
                    raw_model_response_json, extracted_draft_json, confirmed_draft_json,
                    extractor_model, prompt_version, source_run_id, created_at, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.receipt_date, draft.total_cents, draft.subtotal_cents, draft.payer_id,
                    provenance.uploader_id, note, submission_id, content_hash,
                    sqlite3.Binary(provenance.photo_bytes), provenance.photo_mime_type, photo_sha256,
                    raw_model_response_json, extracted_draft_json, confirmed_draft_json,
                    provenance.extractor_model, provenance.prompt_version, provenance.source_run_id,
                    timestamp, timestamp,
                ),
            )
            receipt_id = cursor.lastrowid
            self.connection.executemany(
                "INSERT INTO receipt_shares(receipt_id, member_id, amount_cents) VALUES (?, ?, ?)",
                ((receipt_id, member_id, amount_cents) for member_id, amount_cents in shares.items()),
            )
            return self._get_receipt(receipt_id, created=True)

    def _get_receipt(self, receipt_id: int, *, created: bool) -> ConfirmedReceipt:
        row = self.connection.execute(
            """
            SELECT receipt_id, receipt_date, total_cents, subtotal_cents, payer_id, uploader_id,
                   note, submission_id, photo_sha256
            FROM receipts WHERE receipt_id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"receipt does not exist: {receipt_id}")
        share_rows = self.connection.execute(
            "SELECT member_id, amount_cents FROM receipt_shares WHERE receipt_id = ? ORDER BY member_id",
            (receipt_id,),
        ).fetchall()
        return ConfirmedReceipt(
            receipt_id=row["receipt_id"], submission_id=row["submission_id"],
            receipt_date=row["receipt_date"], total_cents=row["total_cents"],
            subtotal_cents=row["subtotal_cents"], payer_id=row["payer_id"],
            uploader_id=row["uploader_id"], note=row["note"], photo_sha256=row["photo_sha256"],
            shares={share["member_id"]: share["amount_cents"] for share in share_rows}, created=created,
        )

    def monthly_settlement(self, month: str) -> MonthlySettlement:
        try:
            date.fromisoformat(f"{month}-01")
        except ValueError as error:
            raise LedgerValidationError("month must use YYYY-MM") from error
        if len(month) != 7:
            raise LedgerValidationError("month must use YYYY-MM")
        balances = {row["member_id"]: 0 for row in self.connection.execute("SELECT member_id FROM members ORDER BY member_id")}
        paid_rows = self.connection.execute(
            "SELECT payer_id AS member_id, COALESCE(SUM(total_cents), 0) AS amount_cents FROM receipts WHERE substr(receipt_date, 1, 7) = ? GROUP BY payer_id",
            (month,),
        ).fetchall()
        for row in paid_rows:
            balances[row["member_id"]] += row["amount_cents"]
        share_rows = self.connection.execute(
            "SELECT s.member_id, COALESCE(SUM(s.amount_cents), 0) AS amount_cents FROM receipt_shares s JOIN receipts r ON r.receipt_id = s.receipt_id WHERE substr(r.receipt_date, 1, 7) = ? GROUP BY s.member_id",
            (month,),
        ).fetchall()
        for row in share_rows:
            balances[row["member_id"]] -= row["amount_cents"]

        creditors = [[member_id, amount] for member_id, amount in sorted(balances.items()) if amount > 0]
        debtors = [[member_id, -amount] for member_id, amount in sorted(balances.items()) if amount < 0]
        transfers: list[TransferSuggestion] = []
        creditor_index = debtor_index = 0
        while creditor_index < len(creditors) and debtor_index < len(debtors):
            creditor_id, receivable = creditors[creditor_index]
            debtor_id, payable = debtors[debtor_index]
            amount = min(receivable, payable)
            transfers.append(TransferSuggestion(from_member_id=debtor_id, to_member_id=creditor_id, amount_cents=amount))
            creditors[creditor_index][1] -= amount
            debtors[debtor_index][1] -= amount
            if creditors[creditor_index][1] == 0:
                creditor_index += 1
            if debtors[debtor_index][1] == 0:
                debtor_index += 1
        return MonthlySettlement(month=month, balances_cents=balances, transfers=transfers)
