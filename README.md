# Receipt Split — phase 1

This is the requested first-phase command-line baseline. It sends one local JPG/PNG receipt and a split note to a vision model, returns a validated `ExpenseDraft`, and saves an immutable per-run audit bundle.

## Setup with Conda

```bash
cd /Users/doriswu/code/Python/receipt-split
conda create -n receipt-split python=3.12 -y
conda run -n receipt-split python -m pip install -e '.[dev]'
cp .env.example .env
```

Add the key locally to `.env` as `OPENAI_API_KEY=...`. The extractor loads that file at runtime; `.env` is ignored by Git, while `.env.example` is the safe template. A system `OPENAI_API_KEY`, if set, takes precedence.

## Docker foundation

The repository includes a Docker Compose setup for the forthcoming phone-friendly
web interface. It runs a minimal health-check API today; receipt upload, review,
and authentication endpoints will be added separately. Persistent application
data is kept outside the image in `./data/`:

```text
data/
  ledger.sqlite          # created once the ledger is initialized
  objects/               # future original receipt-image objects
```

Neither the database, images, nor `.env` are committed to Git. Build and start
the local-only service with Docker Desktop:

```bash
docker compose up --build -d
curl http://127.0.0.1:8080/healthz
docker compose logs -f
```

The service defaults to `127.0.0.1:8080`, so it is not reachable from phones or
the public internet. Do not change `RECEIPT_SPLIT_BIND_HOST` to `0.0.0.0` until
the web app has login, receipt-level authorization, and HTTPS via a reverse
proxy. When those are implemented, image files will live under `data/objects/`
while SQLite stores their relative storage keys, hashes, MIME types, order, and
receipt relationship.

## Run a real API extraction

```bash
conda run -n receipt-split receipt-split examples/receipts/case-01-full-split.png \
  --note '我垫付，我们三个平摊。' \
  --uploader CindyTest \
  --members CindyTest,AmyTest,BenTest \
  --expected examples/expected-case-01.json
```

The default model is `gpt-6-astra`; override it with `--model` when desired. Each standalone invocation writes `run.json`, `raw_response.json`, `draft.json`, `timing.json`, and (when requested) `evaluation.json` under `runs/<UTC timestamp>-<id>/`. A failed request retains `run.json` and `error.json` instead. Expected answers are never sent to the model.

Evaluation checks whether each clarification identifies the expected field, but its natural-language `reason` is retained for review rather than scored verbatim.

### Fee allocation

The draft records the items/services subtotal separately from the paid total. `individual_charges` contain pre-fee item ownership; any unassigned subtotal is split over `remainder_member_ids`. Tax, tip, service, delivery, and other fees are then allocated proportionally to each member's resulting pre-fee subtotal. Fractional cents use the largest-remainder method, with member ID as the deterministic tie-breaker.

Clarification reasons are written in concise English by default, even when the receipt or note is in another language.

## SQLite ledger

Initialize a local ledger with its fixed member group, then save a reviewed `draft.json`. A complete draft is stored as `confirmed` with recalculated shares; a draft with unresolved clarifications is retained as `pending` with its full source snapshot but no share rows. Pending receipts are excluded from monthly balances and transfer suggestions. Identical submission-ID retries remain idempotent.

```bash
receipt-split-ledger init --database ledger.sqlite --members CindyTest,AmyTest,BenTest
receipt-split-ledger confirm path/to/draft.json \
  --database ledger.sqlite --note 'AmyTest tea' --submission-id 'unique-client-id' \
  --uploader CindyTest --photo path/to/receipt.png --raw-response path/to/raw_response.json \
  --model gpt-6-astra --prompt-version 2026-11-16.phase1-fee-proration
receipt-split-ledger monthly 2026-02 --database ledger.sqlite --strategy group_netting
```

The database has `members`, `receipts`, and `receipt_shares` tables. `receipts.photo_paths_json` is the only image metadata: a JSON list of relative image addresses, such as `["objects/abc123.png"]`; images themselves are immutable files in the database-adjacent `objects/` directory. Raw model response, the reviewed confirmation draft, and model/prompt versions remain in SQLite for essential troubleshooting and reproducibility. A legacy database is migrated automatically: each existing image BLOB is exported to `objects/` and then removed from SQLite. Repeat `--photo` to associate several ordered images with one receipt. `monthly` uses the confirmed receipt date to report each member's paid-minus-owed balance and suggested transfers. Choose `group_netting` (default) to net the whole group and usually reduce transfers, or `receipt_links` to preserve each debtor-to-payer relationship while netting reciprocal debts between the same two members. `ledger.sqlite` and `objects/` are ignored by Git.

## Offline checks (no API call)

```bash
conda run -n receipt-split pytest
conda run -n receipt-split receipt-split does-not-exist.jpg \
  --note '我垫付，我们三个平摊。' --uploader CindyTest --members CindyTest,AmyTest,BenTest
```

The second command verifies failure-audit creation and intentionally exits with status `1`.

## Live model evaluation with pytest

Regular `pytest` keeps model evaluations skipped, so it stays offline and does not use API credits. To run every case in `examples/cases.json` against the live model and retain every actual output:

```bash
conda run -n receipt-split pytest --run-model-evals
```

To run only case 4, use pytest's selector:

```bash
conda run -n receipt-split pytest --run-model-evals -k 04-individual-menu-items
```

Every pytest invocation creates `runs/<run-id>/pytest-summary.json`, whether it is offline or live. A live run additionally writes actual outputs beneath `runs/<run-id>/cases/<case-id>/`; only selected cases appear there. For example, the case-4 command writes exactly one case's actual output. There is no `latest` alias; the timestamped directory is the run index.

## Synthetic phase-one fixtures

`examples/receipts/` holds fictional or user-supplied receipts for the configured cases; no real payment credentials are included. `examples/cases.json` ties each image to its note, member set, and standard answer. Standard answers are supplied to the CLI only after model extraction, so they are used for scoring and never sent to the API.

Expected case 02 encodes the **extraction rule** (`AmyTest: 1200`, then all three share the remainder), rather than the derived final shares. Case 03 asserts `5650`, the paid total, rather than the `$50.00` subtotal.

## API behavior

The implementation uses the Responses API with Pydantic Structured Outputs. The OpenAI image-input guide supports Base64 data URLs for image analysis, and notes that vision can make mistakes; therefore all raw responses are retained for human review. See the [official image-input guide](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses) and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
