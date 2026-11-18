"""FastAPI routes for the first end-to-end receipt submission flow."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .extractor import DEFAULT_MODEL, PROMPT_VERSION, extract_expense
from .ledger import (
    ConfirmedReceipt,
    Ledger,
    LedgerValidationError,
    ReceiptPhoto,
    ReceiptProvenance,
    SubmissionConflictError,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_MEMBERS = ("AmyTest", "BenTest", "CindyTest", "DanTest")
MAX_PHOTOS = 10
MAX_PHOTO_BYTES = 10 * 1024 * 1024


class ApiProblem(Exception):
    """Expected request failure returned as a stable JSON response."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class MembersResponse(BaseModel):
    members: list[str]


class ReceiptResponse(BaseModel):
    receipt_id: int
    submission_id: str
    status: str
    receipt_date: str | None
    total_cents: int | None
    subtotal_cents: int | None
    payer_id: str | None
    uploader_id: str
    note: str
    photo_urls: list[str]
    shares: dict[str, int]
    created: bool


class SettlementResponse(BaseModel):
    month: str
    strategy: Literal["group_netting", "receipt_links"]
    balances_cents: dict[str, int]
    transfers: list[dict[str, Any]]


def _configured_members() -> tuple[str, ...]:
    raw_members = os.environ.get("RECEIPT_SPLIT_MEMBERS", ",".join(DEFAULT_MEMBERS))
    members = tuple(member.strip() for member in raw_members.split(",") if member.strip())
    if not members or len(set(members)) != len(members):
        raise RuntimeError("RECEIPT_SPLIT_MEMBERS must contain unique member IDs")
    return members


def _data_dir() -> Path:
    return Path(os.environ.get("RECEIPT_SPLIT_DATA_DIR", "data"))


def _database_path() -> Path:
    return Path(os.environ.get("RECEIPT_SPLIT_DB_PATH", _data_dir() / "ledger.sqlite"))


def _photo_mime_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ApiProblem(415, "unsupported_image", "Each photo must be a PNG or JPEG image.")


def _submission_id(idempotency_key: str | None) -> str:
    if idempotency_key is None:
        return str(uuid4())
    try:
        return str(UUID(idempotency_key.strip()))
    except (AttributeError, ValueError) as error:
        raise ApiProblem(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key must be a UUID.",
        ) from error


def _receipt_response(receipt: ConfirmedReceipt) -> ReceiptResponse:
    return ReceiptResponse(
        **receipt.model_dump(exclude={"photo_paths"}),
        photo_urls=[f"/receipts/{receipt.receipt_id}/photos/{index}" for index in range(len(receipt.photo_paths))],
    )


def _open_ledger() -> Ledger:
    try:
        return Ledger(_database_path())
    except Exception as error:
        LOGGER.exception("Could not open receipt ledger")
        raise ApiProblem(503, "storage_unavailable", "Receipt storage is temporarily unavailable.") from error


@asynccontextmanager
async def _lifespan(app: FastAPI):
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    members = _configured_members()
    try:
        with Ledger(_database_path()) as ledger:
            ledger.seed_members(list(members))
    except Exception:
        LOGGER.exception("Could not initialize receipt ledger")
        raise
    app.state.members = members
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Receipt Split", version="0.1.0", lifespan=_lifespan)

    @app.exception_handler(ApiProblem)
    async def handle_problem(_: Request, error: ApiProblem) -> JSONResponse:
        payload: dict[str, Any] = {"error": {"code": error.code, "message": error.message}}
        if error.details is not None:
            payload["error"]["details"] = error.details
        return JSONResponse(status_code=error.status_code, content=jsonable_encoder(payload))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "The request body is invalid.",
                    "details": jsonable_encoder(error.errors()),
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, error: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled API error", exc_info=error)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Unexpected server error."}},
        )

    @app.get("/", tags=["service"])
    def service_status() -> dict[str, str]:
        return {"status": "ready", "message": "Receipt Split API is running."}

    @app.get("/healthz", tags=["service"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok" if _data_dir().is_dir() else "storage_unavailable"}

    @app.get("/members", response_model=MembersResponse, tags=["members"])
    def list_members(request: Request) -> MembersResponse:
        return MembersResponse(members=list(request.app.state.members))

    @app.post("/receipts", response_model=ReceiptResponse, tags=["receipts"])
    async def create_receipt(
        request: Request,
        uploader_id: str = Form(...),
        note: str = Form(...),
        photos: list[UploadFile] = File(...),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        members = request.app.state.members
        if uploader_id not in members:
            raise ApiProblem(422, "unknown_uploader", "uploader_id must be a configured member.")
        if not note.strip():
            raise ApiProblem(422, "empty_note", "note must not be empty.")
        if not photos:
            raise ApiProblem(422, "missing_photos", "Provide at least one receipt image.")
        if len(photos) > MAX_PHOTOS:
            raise ApiProblem(413, "too_many_photos", f"Provide at most {MAX_PHOTOS} receipt images.")
        submission_id = _submission_id(idempotency_key)

        receipt_photos: list[ReceiptPhoto] = []
        for upload in photos:
            image_bytes = await upload.read()
            if not image_bytes:
                raise ApiProblem(422, "empty_photo", "Receipt images must not be empty.")
            if len(image_bytes) > MAX_PHOTO_BYTES:
                raise ApiProblem(413, "photo_too_large", "Each receipt image must be 10 MB or smaller.")
            receipt_photos.append(
                ReceiptPhoto(image_bytes=image_bytes, mime_type=_photo_mime_type(image_bytes))
            )

        data_dir = _data_dir()
        try:
            with TemporaryDirectory(prefix="receipt-upload-", dir=data_dir) as temporary_dir:
                temporary_paths: list[Path] = []
                for index, photo in enumerate(receipt_photos, start=1):
                    suffix = ".jpg" if photo.mime_type == "image/jpeg" else ".png"
                    temporary_path = Path(temporary_dir) / f"page-{index}{suffix}"
                    temporary_path.write_bytes(photo.image_bytes)
                    temporary_paths.append(temporary_path)
                draft, raw_model_response = await run_in_threadpool(
                    extract_expense,
                    temporary_paths,
                    note,
                    uploader_id,
                    list(members),
                    model=DEFAULT_MODEL,
                )
        except ApiProblem:
            raise
        except Exception as error:
            LOGGER.exception("Receipt extraction failed")
            raise ApiProblem(
                502,
                "extraction_failed",
                "Could not extract this receipt. Please retry.",
            ) from error

        required_facts_missing = any(
            value is None
            for value in (draft.receipt_date, draft.total_cents, draft.subtotal_cents, draft.payer_id)
        )
        if required_facts_missing or draft.needs_clarification:
            raise ApiProblem(
                422,
                "receipt_needs_clarification",
                "The receipt cannot be automatically confirmed.",
                details={"draft": draft.model_dump(mode="json")},
            )

        try:
            with _open_ledger() as ledger:
                receipt = ledger.confirm_receipt(
                    draft,
                    note=note,
                    submission_id=submission_id,
                    provenance=ReceiptProvenance(
                        uploader_id=uploader_id,
                        photos=receipt_photos,
                        raw_model_response=raw_model_response,
                        extractor_model=DEFAULT_MODEL,
                        prompt_version=PROMPT_VERSION,
                    ),
                )
        except SubmissionConflictError as error:
            raise ApiProblem(
                409,
                "idempotency_conflict",
                "This Idempotency-Key was already used with different content.",
            ) from error
        except LedgerValidationError as error:
            raise ApiProblem(422, "invalid_receipt", str(error)) from error
        except ApiProblem:
            raise
        except Exception as error:
            LOGGER.exception("Receipt persistence failed")
            raise ApiProblem(500, "persistence_failed", "Could not save this receipt.") from error

        response = _receipt_response(receipt)
        return JSONResponse(
            status_code=201 if receipt.created else 200,
            content=jsonable_encoder(response),
        )

    @app.get("/receipts/{receipt_id}", response_model=ReceiptResponse, tags=["receipts"])
    def get_receipt(receipt_id: int) -> ReceiptResponse:
        try:
            with _open_ledger() as ledger:
                receipt = ledger._get_receipt(receipt_id, created=False)
        except LookupError as error:
            raise ApiProblem(404, "receipt_not_found", "Receipt was not found.") from error
        return _receipt_response(receipt)

    @app.get("/receipts/{receipt_id}/photos/{photo_index}", tags=["receipts"])
    def get_receipt_photo(receipt_id: int, photo_index: int) -> FileResponse:
        try:
            with _open_ledger() as ledger:
                receipt = ledger._get_receipt(receipt_id, created=False)
        except LookupError as error:
            raise ApiProblem(404, "receipt_not_found", "Receipt was not found.") from error
        if photo_index < 0 or photo_index >= len(receipt.photo_paths):
            raise ApiProblem(404, "photo_not_found", "Receipt photo was not found.")
        image_path = (_database_path().parent / receipt.photo_paths[photo_index]).resolve()
        data_root = _database_path().parent.resolve()
        if data_root not in image_path.parents or not image_path.is_file():
            raise ApiProblem(404, "photo_not_found", "Receipt photo was not found.")
        return FileResponse(image_path)

    @app.get("/settlements/{month}", response_model=SettlementResponse, tags=["settlements"])
    def get_settlement(
        month: str,
        strategy: Literal["group_netting", "receipt_links"] = "group_netting",
    ) -> SettlementResponse:
        try:
            with _open_ledger() as ledger:
                settlement = ledger.monthly_settlement(month, strategy=strategy)
        except LedgerValidationError as error:
            raise ApiProblem(422, "invalid_month", str(error)) from error
        return SettlementResponse(
            month=settlement.month,
            strategy=settlement.strategy,
            balances_cents=settlement.balances_cents,
            transfers=[transfer.model_dump() for transfer in settlement.transfers],
        )

    return app


app = create_app()
