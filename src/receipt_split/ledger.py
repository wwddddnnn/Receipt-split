"""SQLite-backed confirmed expense ledger.

The ledger is intentionally independent from the extraction provider: callers submit an
editable, already-reviewed ExpenseDraft and the ledger validates and recalculates shares
inside the confirmation transaction.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from pydantic import BaseModel, Field

from .models import ExpenseDraft
from .splitting import calculate_shares


class LedgerValidationError(ValueError):
    """Raised when a draft is not eligible to become a confirmed expense."""


class SubmissionConflictError(ValueError):
    """Raised when a submission ID is retried with different content."""


class ConfirmedExpense(BaseModel):
    expense_id: int
    submission_id: str
    receipt_date: str
    total_cents: int
    subtotal_cents: int
    payer_id: str
    note: str
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

CREATE TABLE IF NOT EXISTS expenses (
    expense_id INTEGER PRIMARY KEY,
    receipt_date TEXT NOT NULL,
    total_cents INTEGER NOT NULL CHECK (total_cents > 0),
    subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents > 0 AND subtotal_cents <= total_cents),
    payer_id TEXT NOT NULL REFERENCES members(member_id),
    note TEXT NOT NULL,
    submission_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expense_shares (
    expense_id INTEGER NOT NULL REFERENCES expenses(expense_id) ON DELETE CASCADE,
    member_id TEXT NOT NULL REFERENCES members(member_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    PRIMARY KEY (expense_id, member_id)
);

CREATE INDEX IF NOT EXISTS expenses_receipt_date_idx ON expenses(receipt_date);
CREATE INDEX IF NOT EXISTS expense_shares_member_idx ON expense_shares(member_id);
"""


class Ledger:
    """A single-process SQLite ledger with idempotent expense confirmation."""

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

    def confirm_expense(
        self, draft: ExpenseDraft, *, note: str, submission_id: str
    ) -> ConfirmedExpense:
        """Validate, recalculate, and atomically store a confirmed expense."""
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

        payload = {
            "receipt_date": draft.receipt_date,
            "total_cents": draft.total_cents,
            "subtotal_cents": draft.subtotal_cents,
            "payer_id": draft.payer_id,
            "note": note,
            "individual_charges": sorted(
                (charge.model_dump(mode="json") for charge in draft.individual_charges),
                key=lambda charge: (charge["member_id"], charge["amount_cents"]),
            ),
            "remainder_member_ids": sorted(draft.remainder_member_ids),
            "shares": shares,
        }
        content_hash = sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

        with self._transaction():
            existing = self.connection.execute(
                "SELECT expense_id, content_hash FROM expenses WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise SubmissionConflictError(
                        "submission_id already exists with different content"
                    )
                return self._get_expense(existing["expense_id"], created=False)

            required_members = set(shares) | {draft.payer_id}
            rows = self.connection.execute(
                f"SELECT member_id FROM members WHERE member_id IN ({','.join('?' for _ in required_members)})",
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
                INSERT INTO expenses(
                    receipt_date, total_cents, subtotal_cents, payer_id, note, submission_id, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.receipt_date,
                    draft.total_cents,
                    draft.subtotal_cents,
                    draft.payer_id,
                    note,
                    submission_id,
                    content_hash,
                ),
            )
            expense_id = cursor.lastrowid
            self.connection.executemany(
                "INSERT INTO expense_shares(expense_id, member_id, amount_cents) VALUES (?, ?, ?)",
                ((expense_id, member_id, amount_cents) for member_id, amount_cents in shares.items()),
            )
            return self._get_expense(expense_id, created=True)

    def _get_expense(self, expense_id: int, *, created: bool) -> ConfirmedExpense:
        row = self.connection.execute(
            """
            SELECT expense_id, receipt_date, total_cents, subtotal_cents, payer_id, note, submission_id
            FROM expenses WHERE expense_id = ?
            """,
            (expense_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"expense does not exist: {expense_id}")
        share_rows = self.connection.execute(
            "SELECT member_id, amount_cents FROM expense_shares WHERE expense_id = ? ORDER BY member_id",
            (expense_id,),
        ).fetchall()
        return ConfirmedExpense(
            expense_id=row["expense_id"],
            submission_id=row["submission_id"],
            receipt_date=row["receipt_date"],
            total_cents=row["total_cents"],
            subtotal_cents=row["subtotal_cents"],
            payer_id=row["payer_id"],
            note=row["note"],
            shares={share["member_id"]: share["amount_cents"] for share in share_rows},
            created=created,
        )

    def monthly_settlement(self, month: str) -> MonthlySettlement:
        try:
            date.fromisoformat(f"{month}-01")
        except ValueError as error:
            raise LedgerValidationError("month must use YYYY-MM") from error
        if len(month) != 7:
            raise LedgerValidationError("month must use YYYY-MM")

        balances = {
            row["member_id"]: 0
            for row in self.connection.execute("SELECT member_id FROM members ORDER BY member_id")
        }
        paid_rows = self.connection.execute(
            """
            SELECT payer_id AS member_id, COALESCE(SUM(total_cents), 0) AS amount_cents
            FROM expenses WHERE substr(receipt_date, 1, 7) = ? GROUP BY payer_id
            """,
            (month,),
        ).fetchall()
        for row in paid_rows:
            balances[row["member_id"]] += row["amount_cents"]
        share_rows = self.connection.execute(
            """
            SELECT s.member_id, COALESCE(SUM(s.amount_cents), 0) AS amount_cents
            FROM expense_shares s JOIN expenses e ON e.expense_id = s.expense_id
            WHERE substr(e.receipt_date, 1, 7) = ? GROUP BY s.member_id
            """,
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
            transfers.append(
                TransferSuggestion(
                    from_member_id=debtor_id,
                    to_member_id=creditor_id,
                    amount_cents=amount,
                )
            )
            creditors[creditor_index][1] -= amount
            debtors[debtor_index][1] -= amount
            if creditors[creditor_index][1] == 0:
                creditor_index += 1
            if debtors[debtor_index][1] == 0:
                debtor_index += 1
        return MonthlySettlement(month=month, balances_cents=balances, transfers=transfers)
