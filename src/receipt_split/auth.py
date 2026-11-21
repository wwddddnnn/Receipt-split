"""Member login, cookie-session, audit, and receipt-access helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256, scrypt
import base64
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3

from pydantic import BaseModel


class MemberIdentity(BaseModel):
    member_id: str
    display_name: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _password_hash(password: str) -> str:
    if len(password) < 10:
        raise ValueError("password must contain at least 10 characters")
    salt = os.urandom(16)
    digest = scrypt(password.encode(), salt=salt, n=16_384, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, digest = encoded.split("$")
        if algorithm != "scrypt":
            return False
        expected = scrypt(
            password.encode(), salt=base64.urlsafe_b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
        return hmac.compare_digest(expected, base64.urlsafe_b64decode(digest))
    except (ValueError, TypeError):
        return False


class AuthStore:
    """Authentication data in the same SQLite file as the ledger."""

    def __init__(self, database_path: str | Path):
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "AuthStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        member_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(members)")}
        additions = {
            "display_name": "TEXT",
            "password_hash": "TEXT",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in member_columns:
                self.connection.execute(f"ALTER TABLE members ADD COLUMN {name} {definition}")
        timestamp = _now()
        self.connection.execute(
            "UPDATE members SET display_name = COALESCE(NULLIF(display_name, ''), member_id), "
            "created_at = COALESCE(created_at, ?), updated_at = COALESCE(updated_at, ?)",
            (timestamp, timestamp),
        )

        receipt_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(receipts)")}
        for name in ("created_by", "updated_by"):
            if name not in receipt_columns:
                self.connection.execute(f"ALTER TABLE receipts ADD COLUMN {name} TEXT REFERENCES members(member_id)")
        if "updated_at" not in receipt_columns:
            self.connection.execute("ALTER TABLE receipts ADD COLUMN updated_at TEXT")
        self.connection.execute(
            "UPDATE receipts SET created_by = COALESCE(created_by, uploader_id), "
            "updated_by = COALESCE(updated_by, uploader_id), "
            "updated_at = COALESCE(updated_at, created_at)"
        )
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL REFERENCES members(member_id),
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS sessions_member_idx ON sessions(member_id);
            CREATE INDEX IF NOT EXISTS receipts_created_by_idx ON receipts(created_by);
            """
        )
        self.connection.commit()

    def seed_members(self, member_ids: list[str]) -> None:
        timestamp = _now()
        self.connection.executemany(
            """
            INSERT INTO members(member_id, display_name, is_active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(member_id) DO NOTHING
            """,
            ((member_id, member_id, timestamp, timestamp) for member_id in member_ids),
        )
        self.connection.commit()

    def provision_member(self, member_id: str, display_name: str, password: str) -> None:
        member_id, display_name = member_id.strip(), display_name.strip()
        if not member_id or not display_name:
            raise ValueError("member_id and display_name must not be empty")
        password_hash = _password_hash(password)
        timestamp = _now()
        self.connection.execute(
            """
            INSERT INTO members(member_id, display_name, password_hash, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(member_id) DO UPDATE SET display_name = excluded.display_name,
              password_hash = excluded.password_hash, is_active = 1, updated_at = excluded.updated_at
            """,
            (member_id, display_name, password_hash, timestamp, timestamp),
        )
        self.connection.commit()

    def reset_password(self, member_id: str, password: str) -> None:
        cursor = self.connection.execute(
            "UPDATE members SET password_hash = ?, updated_at = ? WHERE member_id = ?",
            (_password_hash(password), _now(), member_id),
        )
        if cursor.rowcount != 1:
            self.connection.rollback()
            raise ValueError("member does not exist")
        self.connection.commit()

    def authenticate(self, member_id: str, password: str) -> MemberIdentity | None:
        row = self.connection.execute(
            "SELECT member_id, display_name, password_hash, is_active FROM members WHERE member_id = ?",
            (member_id.strip(),),
        ).fetchone()
        if row is None or not row["is_active"] or not row["password_hash"]:
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        return MemberIdentity(member_id=row["member_id"], display_name=row["display_name"])

    def create_session(self, member_id: str, days: int = 14) -> str:
        token = secrets.token_urlsafe(32)
        self.connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, NULL)",
            (secrets.token_hex(16), member_id, sha256(token.encode()).hexdigest(),
             (datetime.now(UTC) + timedelta(days=days)).isoformat(), _now()),
        )
        self.connection.commit()
        return token

    def session_member(self, token: str | None) -> MemberIdentity | None:
        if not token:
            return None
        row = self.connection.execute(
            """
            SELECT m.member_id, m.display_name FROM sessions s JOIN members m ON m.member_id = s.member_id
            WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND m.is_active = 1
            """,
            (sha256(token.encode()).hexdigest(), _now()),
        ).fetchone()
        return MemberIdentity(**dict(row)) if row else None

    def revoke_session(self, token: str | None) -> None:
        if token:
            self.connection.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", (_now(), sha256(token.encode()).hexdigest()))
            self.connection.commit()

    def members(self) -> list[MemberIdentity]:
        return [MemberIdentity(**dict(row)) for row in self.connection.execute(
            "SELECT member_id, display_name FROM members WHERE is_active = 1 ORDER BY display_name, member_id"
        )]

    def can_access_receipt(self, member_id: str, receipt_id: int) -> bool:
        row = self.connection.execute(
            """
            SELECT r.*, EXISTS(SELECT 1 FROM receipt_shares s WHERE s.receipt_id = r.receipt_id AND s.member_id = ?) AS has_share
            FROM receipts r WHERE r.receipt_id = ?
            """,
            (member_id, receipt_id),
        ).fetchone()
        return row is not None and self._row_is_participant(row, member_id)

    @staticmethod
    def _row_is_participant(row: sqlite3.Row, member_id: str) -> bool:
        if member_id in {row["created_by"], row["uploader_id"], row["payer_id"]} or row["has_share"]:
            return True
        try:
            draft = json.loads(row["confirmed_draft_json"])
        except (TypeError, json.JSONDecodeError):
            return False
        return member_id in set(draft.get("remainder_member_ids", [])) | {
            item.get("member_id") for item in draft.get("individual_charges", []) if isinstance(item, dict)
        }

    def accessible_receipt_rows(self, member_id: str, *, month: str | None = None, status: str | None = None) -> list[sqlite3.Row]:
        conditions: list[str] = []
        parameters: list[str] = []
        if month is not None:
            conditions.append("substr(r.receipt_date, 1, 7) = ?")
            parameters.append(month)
        if status is not None:
            conditions.append("r.status = ?")
            parameters.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            """
            SELECT r.*, EXISTS(SELECT 1 FROM receipt_shares s WHERE s.receipt_id = r.receipt_id AND s.member_id = ?) AS has_share,
                   payer.display_name AS payer_display_name
            FROM receipts r LEFT JOIN members payer ON payer.member_id = r.payer_id
            """ + where + " ORDER BY r.receipt_date DESC, r.receipt_id DESC",
            [member_id, *parameters],
        ).fetchall()
        return [row for row in rows if self._row_is_participant(row, member_id)]

    def accessible_receipt_ids(self, member_id: str) -> list[int]:
        return [row["receipt_id"] for row in self.accessible_receipt_rows(member_id)]

    def audit_receipt(self, receipt_id: int, member_id: str) -> None:
        self.connection.execute(
            "UPDATE receipts SET created_by = COALESCE(created_by, ?), updated_by = ?, updated_at = ? WHERE receipt_id = ?",
            (member_id, member_id, _now(), receipt_id),
        )
        self.connection.commit()
