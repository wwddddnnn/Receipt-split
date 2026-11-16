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
  --uploader Doris \
  --members Doris,Amy,Ben \
  --expected examples/expected-case-01.json
```

The default model is `gpt-6-astra`; override it with `--model` when desired. Each invocation writes `run.json`, `raw_response.json`, `draft.json`, `timing.json`, and (when requested) `evaluation.json` under `artifacts/<UTC timestamp>-<id>/`. A failed request retains `run.json` and `error.json` instead. Expected answers are never sent to the model.

`artifacts/latest` always points to the newest run, so inspect the latest parsed response with `conda run -n receipt-split python -m json.tool artifacts/latest/draft.json`; use `raw_response.json` for the complete API response and `evaluation.json` when an expected answer was supplied.

### Fee allocation

The draft records the items/services subtotal separately from the paid total. `individual_charges` contain pre-fee item ownership; any unassigned subtotal is split over `remainder_member_ids`. Tax, tip, service, delivery, and other fees are then allocated proportionally to each member's resulting pre-fee subtotal. Fractional cents use the largest-remainder method, with member ID as the deterministic tie-breaker.

## Offline checks (no API call)

```bash
conda run -n receipt-split pytest
conda run -n receipt-split receipt-split does-not-exist.jpg \
  --note '我垫付，我们三个平摊。' --uploader Doris --members Doris,Amy,Ben
```

The second command verifies failure-audit creation and intentionally exits with status `1`.

## Synthetic phase-one fixtures

`examples/receipts/` holds three generated, fictional Canadian receipts for cases 01–03; no real purchase data is included. `examples/cases.json` ties each image to its note, member set, and standard answer. Standard answers are supplied to the CLI only after model extraction, so they are used for scoring and never sent to the API.

Expected case 02 encodes the **extraction rule** (`Amy: 1200`, then all three share the remainder), rather than the derived final shares. Case 03 asserts `5650`, the paid total, rather than the `$50.00` subtotal.

## API behavior

The implementation uses the Responses API with Pydantic Structured Outputs. The OpenAI image-input guide supports Base64 data URLs for image analysis, and notes that vision can make mistakes; therefore all raw responses are retained for human review. See the [official image-input guide](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses) and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
