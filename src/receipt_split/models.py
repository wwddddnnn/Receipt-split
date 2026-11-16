"""Shared structured types for model extraction and later web use."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class IndividualCharge(BaseModel):
    """A portion of an expense assigned directly to one member, in cents."""

    member_id: str = Field(min_length=1)
    amount_cents: int = Field(ge=1)


class Clarification(BaseModel):
    """A field that must be checked before the expense can be confirmed."""

    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ExpenseDraft(BaseModel):
    """Model-produced, editable extraction result. Money is always integer cents."""

    receipt_date: str | None = Field(
        default=None, description="YYYY-MM-DD, or null when not legible"
    )
    total_cents: int | None = Field(default=None, ge=1)
    payer_id: str | None = Field(default=None, min_length=1)
    individual_charges: list[IndividualCharge] = Field(default_factory=list)
    remainder_member_ids: list[str] = Field(default_factory=list)
    needs_clarification: list[Clarification] = Field(default_factory=list)

    @field_validator("receipt_date")
    @classmethod
    def valid_iso_date_or_none(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("must use YYYY-MM-DD")

        from datetime import date

        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("must use YYYY-MM-DD") from error
        return value
