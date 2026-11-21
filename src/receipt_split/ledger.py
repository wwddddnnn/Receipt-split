"""SQLite-backed receipt ledger with file-backed receipt images."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field, field_validator

from .models import Clarification, ExpenseDraft
from .splitting import calculate_shares


class LedgerValidationError(ValueError):
    """Raised when a receipt is not eligible to be confirmed."""


class SubmissionConflictError(ValueError):
    """Raised when a submission ID is retried with different content."""


class ReceiptUpdateConflictError(ValueError):
    """Raised when a receipt was changed after the editor loaded it."""


class ReceiptStateError(ValueError):
    """Raised when an invalid receipt-status transition is requested."""


class ReceiptPhoto(BaseModel):
    """One upload, retained in memory only until persisted to image storage."""

    image_bytes: bytes = Field(min_length=1)
    mime_type: str = Field(pattern=r"^image/(jpeg|png)$")


class ReceiptProvenance(BaseModel):
    """Original upload and extraction data retained with a receipt."""

    uploader_id: str = Field(min_length=1)
    photos: list[ReceiptPhoto] = Field(min_length=1)
    raw_model_response: Any
    extractor_model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)

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
    status: str
    receipt_date: str | None
    total_cents: int | None
    subtotal_cents: int | None
    payer_id: str | None
    uploader_id: str
    note: str
    photo_paths: list[str]
    shares: dict[str, int]
    individual_charges: list[dict[str, Any]] = Field(default_factory=list)
    remainder_member_ids: list[str] = Field(default_factory=list)
    rule_editable: bool = False
    version: int = 1
    needs_clarification: list[dict[str, str]] = Field(default_factory=list)
    created: bool


class TransferSuggestion(BaseModel):
    from_member_id: str
    to_member_id: str
    amount_cents: int = Field(gt=0)


class MonthlySettlement(BaseModel):
    month: str
    strategy: Literal["group_netting", "receipt_links"]
    balances_cents: dict[str, int]
    transfers: list[TransferSuggestion]


CREATE_RECEIPTS_TABLE = """
CREATE TABLE receipts (
    receipt_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed')),
    receipt_date TEXT,
    total_cents INTEGER CHECK (total_cents > 0),
    subtotal_cents INTEGER CHECK (subtotal_cents > 0 AND subtotal_cents <= total_cents),
    payer_id TEXT REFERENCES members(member_id),
    uploader_id TEXT NOT NULL REFERENCES members(member_id),
    note TEXT NOT NULL,
    submission_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    photo_paths_json TEXT NOT NULL,
    raw_model_response_json TEXT NOT NULL,
    confirmed_draft_json TEXT NOT NULL,
    extractor_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT
)
"""

CREATE_RECEIPT_SHARES_TABLE = """
CREATE TABLE receipt_shares (
    receipt_id INTEGER NOT NULL REFERENCES receipts(receipt_id) ON DELETE CASCADE,
    member_id TEXT NOT NULL REFERENCES members(member_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    PRIMARY KEY (receipt_id, member_id)
)
"""

SCHEMA = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS members (
    member_id TEXT PRIMARY KEY
);

{CREATE_RECEIPTS_TABLE.replace('CREATE TABLE receipts', 'CREATE TABLE IF NOT EXISTS receipts')};

{CREATE_RECEIPT_SHARES_TABLE.replace('CREATE TABLE receipt_shares', 'CREATE TABLE IF NOT EXISTS receipt_shares')};

CREATE INDEX IF NOT EXISTS receipts_receipt_date_idx ON receipts(receipt_date);
CREATE INDEX IF NOT EXISTS receipt_shares_member_idx ON receipt_shares(member_id);
"""


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _photo_extension(mime_type: str) -> str:
    return ".jpg" if mime_type == "image/jpeg" else ".png"


class Ledger:
    """A single-process SQLite ledger with idempotent receipt confirmation.

    Images are immutable content-addressed files in ``objects/`` beside the
    SQLite file. The database stores only relative paths and audit metadata.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.media_root = self.database_path.parent / "objects"
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        migrated_legacy_blobs = self._migrate_receipts_schema_if_needed()
        self.connection.executescript(SCHEMA)
        self._ensure_edit_columns()
        if migrated_legacy_blobs:
            # Dropped BLOB pages remain in the SQLite file until VACUUM rewrites it.
            self.connection.execute("VACUUM")

    def _ensure_edit_columns(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(receipts)")}
        for name, definition in {
            "created_by": "TEXT REFERENCES members(member_id)",
            "updated_by": "TEXT REFERENCES members(member_id)",
            "updated_at": "TEXT",
            "version": "INTEGER NOT NULL DEFAULT 1",
        }.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE receipts ADD COLUMN {name} {definition}")
        self.connection.execute("UPDATE receipts SET created_by = COALESCE(created_by, uploader_id), updated_by = COALESCE(updated_by, uploader_id), updated_at = COALESCE(updated_at, created_at), version = COALESCE(version, 1)")
        self.connection.commit()

    def _migrate_receipts_schema_if_needed(self) -> bool:
        """Migrate legacy BLOB or verbose-path schemas to the minimal schema."""
        table_exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'receipts'"
        ).fetchone()
        if table_exists is None:
            return False
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(receipts)")
        }
        required_columns = {
            "receipt_id", "status", "receipt_date", "total_cents", "subtotal_cents", "payer_id",
            "uploader_id", "note", "submission_id", "content_hash", "photo_paths_json",
            "raw_model_response_json", "confirmed_draft_json", "extractor_model", "prompt_version",
            "created_at", "confirmed_at",
        }
        if required_columns.issubset(columns) and "photo_bytes" not in columns:
            return False
        if "photo_bytes" not in columns and "photo_paths_json" not in columns:
            raise RuntimeError("receipts has an unsupported image-storage schema")

        legacy_rows = list(self.connection.execute("SELECT * FROM receipts ORDER BY receipt_id"))
        migrated_rows: list[dict[str, Any]] = []
        for row in legacy_rows:
            values = dict(row)
            if "photo_bytes" in columns:
                mime_type = values["photo_mime_type"]
                image_bytes = values["photo_bytes"]
                image_hash = values.get("photo_sha256") or sha256(image_bytes).hexdigest()
                reference = self._photo_reference(image_hash, mime_type)
                self._persist_photo(reference, image_bytes)
                photo_paths = [reference["path"]]
            else:
                photo_paths = json.loads(values["photo_paths_json"])
            status = values.get("status", "confirmed")
            shares = {
                share["member_id"]: share["amount_cents"]
                for share in self.connection.execute(
                    "SELECT member_id, amount_cents FROM receipt_shares WHERE receipt_id = ?",
                    (values["receipt_id"],),
                )
            }
            confirmed_at = values.get("confirmed_at")
            if status == "confirmed" and confirmed_at is None:
                confirmed_at = values["created_at"]
            migrated_rows.append({
                **values,
                "status": status,
                "confirmed_at": confirmed_at,
                "photo_paths": photo_paths,
                "content_hash": self._content_hash(
                    confirmed_draft=json.loads(values["confirmed_draft_json"]),
                    note=values["note"],
                    photo_paths=photo_paths,
                    raw_model_response=json.loads(values["raw_model_response_json"]),
                    uploader_id=values["uploader_id"],
                    extractor_model=values["extractor_model"],
                    prompt_version=values["prompt_version"],
                    status=status,
                    shares=shares,
                ),
            })

        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("ALTER TABLE receipt_shares RENAME TO receipt_shares_legacy")
            self.connection.execute("ALTER TABLE receipts RENAME TO receipts_legacy")
            self.connection.execute(CREATE_RECEIPTS_TABLE)
            self.connection.execute(CREATE_RECEIPT_SHARES_TABLE)
            for row in migrated_rows:
                self.connection.execute(
                    """
                    INSERT INTO receipts(
                        receipt_id, status, receipt_date, total_cents, subtotal_cents, payer_id,
                        uploader_id, note, submission_id, content_hash, photo_paths_json,
                        raw_model_response_json, confirmed_draft_json, extractor_model, prompt_version,
                        created_at, confirmed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["receipt_id"], row["status"], row["receipt_date"], row["total_cents"],
                        row["subtotal_cents"], row["payer_id"], row["uploader_id"], row["note"],
                        row["submission_id"], row["content_hash"], _compact_json(row["photo_paths"]),
                        row["raw_model_response_json"], row["confirmed_draft_json"],
                        row["extractor_model"], row["prompt_version"], row["created_at"],
                        row["confirmed_at"],
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO receipt_shares(receipt_id, member_id, amount_cents)
                SELECT receipt_id, member_id, amount_cents FROM receipt_shares_legacy
                """
            )
            self.connection.execute("DROP TABLE receipt_shares_legacy")
            self.connection.execute("DROP TABLE receipts_legacy")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")
        return True

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

    @staticmethod
    def _photo_reference(photo_sha256: str, mime_type: str) -> dict[str, str]:
        return {
            "path": f"objects/{photo_sha256}{_photo_extension(mime_type)}",
            "sha256": photo_sha256,
            "mime_type": mime_type,
        }

    def _photo_references(self, photos: list[ReceiptPhoto]) -> list[dict[str, str]]:
        return [
            self._photo_reference(sha256(photo.image_bytes).hexdigest(), photo.mime_type)
            for photo in photos
        ]

    def _persist_photo(self, reference: dict[str, str], image_bytes: bytes) -> None:
        destination = self.database_path.parent / reference["path"]
        if destination.exists():
            if sha256(destination.read_bytes()).hexdigest() != reference["sha256"]:
                raise RuntimeError(f"stored image hash mismatch: {destination}")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=destination.parent, prefix=".upload-", delete=False) as handle:
            temporary_path = Path(handle.name)
            try:
                handle.write(image_bytes)
                handle.flush()
                temporary_path.replace(destination)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise

    @staticmethod
    def _content_hash(
        *,
        confirmed_draft: dict[str, Any],
        note: str,
        photo_paths: list[str],
        raw_model_response: Any,
        uploader_id: str,
        extractor_model: str,
        prompt_version: str,
        status: str,
        shares: dict[str, int],
    ) -> str:
        payload = {
            "confirmed_draft": confirmed_draft,
            "note": note,
            "photo_paths": photo_paths,
            "raw_model_response": raw_model_response,
            "uploader_id": uploader_id,
            "extractor_model": extractor_model,
            "prompt_version": prompt_version,
            "status": status,
            "shares": shares,
        }
        return sha256(_compact_json(payload).encode()).hexdigest()

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
        """Recalculate and persist one reviewed receipt plus file-backed images."""
        if not note.strip():
            raise LedgerValidationError("note must not be empty")
        if not submission_id.strip():
            raise LedgerValidationError("submission_id must not be empty")
        required_facts_missing = any(
            value is None
            for value in (draft.receipt_date, draft.total_cents, draft.subtotal_cents, draft.payer_id)
        )
        status = "pending" if draft.needs_clarification or required_facts_missing else "confirmed"
        shares: dict[str, int] = {}
        if status == "confirmed":
            try:
                shares = calculate_shares(draft)
            except ValueError as error:
                raise LedgerValidationError(str(error)) from error

        photo_references = self._photo_references(provenance.photos)
        confirmed_draft = draft.model_dump(mode="json")
        content_hash = self._content_hash(
            confirmed_draft=confirmed_draft,
            note=note,
            photo_paths=[reference["path"] for reference in photo_references],
            raw_model_response=provenance.raw_model_response,
            uploader_id=provenance.uploader_id,
            extractor_model=provenance.extractor_model,
            prompt_version=provenance.prompt_version,
            status=status,
            shares=shares,
        )
        raw_model_response_json = _compact_json(provenance.raw_model_response)
        confirmed_draft_json = _compact_json(confirmed_draft)
        timestamp = datetime.now(UTC).isoformat()

        with self._transaction():
            existing = self.connection.execute(
                "SELECT receipt_id, content_hash FROM receipts WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise SubmissionConflictError("submission_id already exists with different content")
                return self._get_receipt(existing["receipt_id"], created=False)

            required_members = set(shares) | {provenance.uploader_id}
            if draft.payer_id is not None:
                required_members.add(draft.payer_id)
            placeholders = ",".join("?" for _ in required_members)
            rows = self.connection.execute(
                f"SELECT member_id FROM members WHERE member_id IN ({placeholders})",
                tuple(sorted(required_members)),
            ).fetchall()
            known_members = {row["member_id"] for row in rows}
            missing_members = sorted(required_members - known_members)
            if missing_members:
                raise LedgerValidationError(f"members are not in the ledger: {', '.join(missing_members)}")

            for photo, reference in zip(provenance.photos, photo_references, strict=True):
                self._persist_photo(reference, photo.image_bytes)
            cursor = self.connection.execute(
                """
                INSERT INTO receipts(
                    status, receipt_date, total_cents, subtotal_cents, payer_id, uploader_id, note,
                    submission_id, content_hash, photo_paths_json, raw_model_response_json,
                    confirmed_draft_json, extractor_model, prompt_version, created_at, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    status, draft.receipt_date, draft.total_cents, draft.subtotal_cents, draft.payer_id,
                    provenance.uploader_id, note, submission_id, content_hash,
                    _compact_json([reference["path"] for reference in photo_references]),
                    raw_model_response_json, confirmed_draft_json, provenance.extractor_model,
                    provenance.prompt_version, timestamp, timestamp if status == "confirmed" else None,
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
            SELECT receipt_id, status, receipt_date, total_cents, subtotal_cents, payer_id, uploader_id,
                   note, submission_id, photo_paths_json, confirmed_draft_json, version
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
        try:
            draft = ExpenseDraft.model_validate(json.loads(row["confirmed_draft_json"]))
            rule_editable = bool(draft.individual_charges or draft.remainder_member_ids)
        except Exception:
            draft = ExpenseDraft()
            rule_editable = False
        return ConfirmedReceipt(
            receipt_id=row["receipt_id"], submission_id=row["submission_id"], status=row["status"],
            receipt_date=row["receipt_date"], total_cents=row["total_cents"],
            subtotal_cents=row["subtotal_cents"], payer_id=row["payer_id"],
            uploader_id=row["uploader_id"], note=row["note"],
            photo_paths=json.loads(row["photo_paths_json"]),
            shares={share["member_id"]: share["amount_cents"] for share in share_rows}, created=created,
            individual_charges=[charge.model_dump() for charge in draft.individual_charges],
            remainder_member_ids=draft.remainder_member_ids,
            rule_editable=rule_editable,
            version=row["version"] or 1,
            needs_clarification=[item.model_dump() for item in draft.needs_clarification],
        )

    @staticmethod
    def edit_draft(*, receipt_date: str | None, total_cents: int | None, payer_id: str | None, individual_charges: list[dict[str, Any]], remainder_member_ids: list[str]) -> ExpenseDraft:
        return ExpenseDraft(receipt_date=receipt_date, total_cents=total_cents, subtotal_cents=total_cents, payer_id=payer_id, individual_charges=individual_charges, remainder_member_ids=remainder_member_ids)

    def preview_edit(self, draft: ExpenseDraft, member_ids: set[str]) -> dict[str, int]:
        involved = {draft.payer_id, *draft.remainder_member_ids, *(charge.member_id for charge in draft.individual_charges)}
        unknown = sorted(member_id for member_id in involved if member_id and member_id not in member_ids)
        if unknown:
            raise LedgerValidationError(f"members are not in the ledger: {', '.join(unknown)}")
        try:
            return calculate_shares(draft)
        except ValueError as error:
            raise LedgerValidationError(str(error)) from error

    def update_receipt(self, receipt_id: int, *, version: int, draft: ExpenseDraft, note: str, updated_by: str, confirm: bool) -> ConfirmedReceipt:
        members = {row["member_id"] for row in self.connection.execute("SELECT member_id FROM members")}
        involved = {draft.payer_id, *draft.remainder_member_ids, *(charge.member_id for charge in draft.individual_charges)}
        unknown = sorted(member_id for member_id in involved if member_id and member_id not in members)
        if unknown:
            raise LedgerValidationError(f"members are not in the ledger: {', '.join(unknown)}")
        shares: dict[str, int] = {}
        if confirm:
            if not note.strip():
                raise LedgerValidationError("note must not be empty")
            if any(value is None for value in (draft.receipt_date, draft.total_cents, draft.subtotal_cents, draft.payer_id)):
                raise LedgerValidationError("date, total, and payer must be provided")
            shares = self.preview_edit(draft, members)
            draft.needs_clarification = []
        else:
            clarifications = []
            for field, value in (("receipt_date", draft.receipt_date), ("total_cents", draft.total_cents), ("payer_id", draft.payer_id)):
                if value is None:
                    clarifications.append({"field": field, "reason": "Please provide this field before confirming."})
            if not clarifications and not draft.individual_charges and not draft.remainder_member_ids:
                clarifications.append({"field": "split", "reason": "Choose a split rule before confirming."})
            draft.needs_clarification = [Clarification(**item) for item in clarifications]
        timestamp = datetime.now(UTC).isoformat()
        with self._transaction():
            row = self.connection.execute("SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
            if row is None:
                raise LookupError("receipt does not exist")
            if row["version"] != version:
                raise ReceiptUpdateConflictError("receipt was updated by someone else")
            if not confirm and row["status"] != "pending":
                raise ReceiptStateError("confirmed receipts cannot be saved as drafts")
            status = "confirmed" if confirm else "pending"
            content_hash = self._content_hash(confirmed_draft=draft.model_dump(mode="json"), note=note, photo_paths=json.loads(row["photo_paths_json"]), raw_model_response=json.loads(row["raw_model_response_json"]), uploader_id=row["uploader_id"], extractor_model=row["extractor_model"], prompt_version=row["prompt_version"], status=status, shares=shares)
            cursor = self.connection.execute(
                """UPDATE receipts SET status=?, receipt_date=?, total_cents=?, subtotal_cents=?, payer_id=?, note=?, confirmed_draft_json=?, content_hash=?, confirmed_at=?, updated_by=?, updated_at=?, version=version+1 WHERE receipt_id=? AND version=?""",
                (status, draft.receipt_date, draft.total_cents, draft.subtotal_cents, draft.payer_id, note, _compact_json(draft.model_dump(mode="json")), content_hash, timestamp if confirm else None, updated_by, timestamp, receipt_id, version),
            )
            if cursor.rowcount != 1:
                raise ReceiptUpdateConflictError("receipt was updated by someone else")
            self.connection.execute("DELETE FROM receipt_shares WHERE receipt_id = ?", (receipt_id,))
            self.connection.executemany("INSERT INTO receipt_shares(receipt_id, member_id, amount_cents) VALUES (?, ?, ?)", ((receipt_id, member_id, amount) for member_id, amount in shares.items()))
            return self._get_receipt(receipt_id, created=False)

    def monthly_settlement(
        self,
        month: str,
        strategy: Literal["group_netting", "receipt_links"] = "group_netting",
    ) -> MonthlySettlement:
        """Return balances plus transfers using the selected settlement policy.

        ``group_netting`` nets the whole group and can route money between
        people without a shared receipt. ``receipt_links`` preserves the
        original debtor-to-payer relationships, only netting reciprocal debts
        between the same two members.
        """
        try:
            date.fromisoformat(f"{month}-01")
        except ValueError as error:
            raise LedgerValidationError("month must use YYYY-MM") from error
        if len(month) != 7:
            raise LedgerValidationError("month must use YYYY-MM")
        if strategy not in {"group_netting", "receipt_links"}:
            raise LedgerValidationError("unknown settlement strategy")
        balances = {row["member_id"]: 0 for row in self.connection.execute("SELECT member_id FROM members ORDER BY member_id")}
        paid_rows = self.connection.execute(
            "SELECT payer_id AS member_id, COALESCE(SUM(total_cents), 0) AS amount_cents FROM receipts WHERE status = 'confirmed' AND substr(receipt_date, 1, 7) = ? GROUP BY payer_id",
            (month,),
        ).fetchall()
        for row in paid_rows:
            balances[row["member_id"]] += row["amount_cents"]
        share_rows = self.connection.execute(
            "SELECT s.member_id, COALESCE(SUM(s.amount_cents), 0) AS amount_cents FROM receipt_shares s JOIN receipts r ON r.receipt_id = s.receipt_id WHERE r.status = 'confirmed' AND substr(r.receipt_date, 1, 7) = ? GROUP BY s.member_id",
            (month,),
        ).fetchall()
        for row in share_rows:
            balances[row["member_id"]] -= row["amount_cents"]

        if strategy == "group_netting":
            transfers = self._group_netting_transfers(balances)
        else:
            transfers = self._receipt_link_transfers(month)
        return MonthlySettlement(
            month=month, strategy=strategy, balances_cents=balances, transfers=transfers
        )

    @staticmethod
    def _group_netting_transfers(balances: dict[str, int]) -> list[TransferSuggestion]:
        """Settle global net balances, allowing transfers across receipt links."""
        creditors = [[member_id, amount] for member_id, amount in sorted(balances.items()) if amount > 0]
        debtors = [[member_id, -amount] for member_id, amount in sorted(balances.items()) if amount < 0]
        transfers: list[TransferSuggestion] = []
        creditor_index = debtor_index = 0
        while creditor_index < len(creditors) and debtor_index < len(debtors):
            creditor_id, receivable = creditors[creditor_index]
            debtor_id, payable = debtors[debtor_index]
            amount = min(receivable, payable)
            transfers.append(
                TransferSuggestion(
                    from_member_id=debtor_id, to_member_id=creditor_id, amount_cents=amount
                )
            )
            creditors[creditor_index][1] -= amount
            debtors[debtor_index][1] -= amount
            if creditors[creditor_index][1] == 0:
                creditor_index += 1
            if debtors[debtor_index][1] == 0:
                debtor_index += 1
        return transfers

    def _receipt_link_transfers(self, month: str) -> list[TransferSuggestion]:
        """Settle only direct receipt debts, netting each member pair both ways."""
        rows = self.connection.execute(
            """
            SELECT s.member_id AS debtor_id, r.payer_id AS creditor_id,
                   SUM(s.amount_cents) AS amount_cents
            FROM receipt_shares AS s
            JOIN receipts AS r ON r.receipt_id = s.receipt_id
            WHERE r.status = 'confirmed'
              AND substr(r.receipt_date, 1, 7) = ?
              AND s.member_id != r.payer_id
            GROUP BY s.member_id, r.payer_id
            """,
            (month,),
        ).fetchall()
        directed_debts = {
            (row["debtor_id"], row["creditor_id"]): row["amount_cents"] for row in rows
        }
        transfers: list[TransferSuggestion] = []
        handled_pairs: set[frozenset[str]] = set()
        for debtor_id, creditor_id in sorted(directed_debts):
            pair = frozenset((debtor_id, creditor_id))
            if pair in handled_pairs:
                continue
            handled_pairs.add(pair)
            forward = directed_debts.get((debtor_id, creditor_id), 0)
            reverse = directed_debts.get((creditor_id, debtor_id), 0)
            if forward > reverse:
                transfers.append(
                    TransferSuggestion(
                        from_member_id=debtor_id,
                        to_member_id=creditor_id,
                        amount_cents=forward - reverse,
                    )
                )
            elif reverse > forward:
                transfers.append(
                    TransferSuggestion(
                        from_member_id=creditor_id,
                        to_member_id=debtor_id,
                        amount_cents=reverse - forward,
                    )
                )
        return transfers
