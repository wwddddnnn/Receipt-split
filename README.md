# Receipt Split — phase 1

This is the requested first-phase command-line baseline. It sends one local JPG/PNG receipt and a split note to a vision model, returns a validated `ExpenseDraft`, and saves an immutable per-run audit bundle.

## Setup with Conda

```bash
cd /Users/doriswu/code/Python/receipt-split
conda create -n receipt-split python=3.12 -y
conda run -n receipt-split python -m pip install -e '.[dev]'
export OPENAI_API_KEY='your-key-here'
```

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

Initialize a local ledger with its fixed member group, then confirm a reviewed `draft.json`. Confirmation recalculates shares, rejects unresolved clarifications, writes the expense and all shares in one SQLite transaction, and makes identical submission-ID retries idempotent.

```bash
receipt-split-ledger init --database ledger.sqlite --members CindyTest,AmyTest,BenTest
receipt-split-ledger confirm path/to/draft.json \
  --database ledger.sqlite --note 'AmyTest tea' --submission-id 'unique-client-id' \
  --uploader CindyTest --photo path/to/receipt.png --raw-response path/to/raw_response.json \
  --model gpt-6-astra --prompt-version 2026-11-16.phase1-fee-proration
receipt-split-ledger monthly 2026-02 --database ledger.sqlite
```

The database has `members`, `receipts`, and `receipt_shares` tables. `receipts` retains the original JPG/PNG bytes, SHA-256, raw model response, extraction draft, confirmation draft, and extractor version alongside its public accounting fields. `monthly` uses the confirmed receipt date to report each member's paid-minus-owed balance and suggested transfers. `ledger.sqlite` and its image BLOBs are ignored by Git.

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
