"""Small HTTP entry point used by the Docker deployment.

The receipt upload and ledger endpoints will be added in the next phase.  This
module intentionally does not initialise a ledger or mutate persistent data.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI


app = FastAPI(title="Receipt Split", version="0.1.0")


@app.get("/", tags=["service"])
def service_status() -> dict[str, str]:
    """Identify the running service without exposing private storage paths."""
    return {"status": "setup", "message": "Receipt Split API is running."}


@app.get("/healthz", tags=["service"])
def healthcheck() -> dict[str, str]:
    """Confirm that the configured persistent-data directory is available."""
    data_dir = Path(os.environ.get("RECEIPT_SPLIT_DATA_DIR", "/data"))
    return {"status": "ok" if data_dir.is_dir() else "storage_unavailable"}
