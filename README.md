# Starter Project 1 + 2: Order Desk workflow

Takes one customer order email and returns a structured order, a status and a draft reply via `POST /orders`.
The data pack's own README (layout and scoring rules) is in `DATA_PACK_README.md`.

It is a **workflow** with fixed steps. The LLM only reads and writes text; code makes every decision.

| Step | Done by | File |
|---|---|---|
| 1. Extract: email -> `ExtractedOrder` (customer, city, deadline, items) | LLM (structured output) | `app/steps/extract.py` |
| 1b. Cross-check each extracted value against the email | Code (grounding) + LLM (verifier) | `app/steps/grounding.py`, `app/steps/verify.py` |
| Loop control: re-extract with feedback, max 5 iterations | Code | `app/steps/extraction_loop.py` |
| 2. Validate: required fields, quantities, deadline vs `LEAD_TIME_DAYS` | Code | `app/steps/validate.py` |
| 3. Check stock: exact name match (ignoring case/spaces), read stock from Postgres | Code | `app/steps/stock.py` |
| Status: Need info > Partial > Ready | Code | `app/steps/status.py` |
| 4. Draft reply from the computed result only (never sees the raw email) | LLM | `app/steps/reply.py` |
| Reply check: no prices/discounts/invented numbers | Code | `app/steps/reply_check.py` |

Nothing about specific emails is hardcoded: any email text works, and `evaluate.py` runs any folder of `.txt` emails.

## Layout

```
app/                 the service (config, models, LLM client, steps, workflow, FastAPI)
docs/                product brochures and pricing policy used by RAG
scripts/             idempotent document embedding ingestion
starter-project-2-data/  SP2 seeds, brochures, emails and answer key
tests/               pytest + tests/robustness_emails/ (messy extra emails)
emails/labelled/     10 emails with answers in answer_key.json
emails/practice/     5 emails without answers
seed.sql             products + stock (mounted into Postgres)
evaluate.py          runs a folder of emails, scores against the answer key
check_azure.py       one-call check of the Azure OpenAI settings
results/             JSON output of the last evaluation runs
```

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # then fill in the Azure OpenAI values
docker compose down -v        # required once to recreate pgvector database data
docker compose up -d --wait   # pgvector/pg16 on host port 5433
.venv\Scripts\python scripts\ingest_docs.py
.venv\Scripts\python check_azure.py
```

Check the seed loaded:
`docker exec sp1-postgres psql -U orders -d orders -c "SELECT (SELECT count(*) FROM products), (SELECT count(*) FROM stock), (SELECT count(*) FROM order_history), (SELECT extname FROM pg_extension WHERE extname='vector');"`
If tables are missing you're on an old volume: `docker compose down -v`, then `up` again.

## Run

```powershell
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

- `POST /orders` with `{"email": "<raw email text, headers included>"}` returns the contract shape
- `GET /health` checks the database; Swagger UI at http://127.0.0.1:8000/docs
- Errors: `422` bad body, `502` LLM unavailable after retries, `503` database unavailable

## Tests and evaluation

Each evaluation writes per-email results and a deterministic `summary.json` beside them. The
summary includes exact whole-order accuracy, accuracy for each scored field, failure categories,
latency percentiles, order-level LLM calls/attempts/retries, token totals, extraction iterations,
fallback/reply-check counts, and error categories. Local evaluations also include estimated costs
when token usage and configured rates are available. Use
`--group-errors` to include counts and reasons grouped by category. For an empty email folder, the
evaluator writes a valid summary with null accuracy values and does not start the workflow.

Estimated cost rates are optional: set `LLM_INPUT_COST_PER_1M_TOKENS` and
`LLM_OUTPUT_COST_PER_1M_TOKENS` from your Azure deployment/pricing source. There are deliberately
no default prices. Costs are estimates from provider-reported token counts, not billed amounts;
missing token usage makes that order's cost unavailable.

```powershell
.venv\Scripts\python -m pytest                                    # code steps, no LLM or DB needed
.venv\Scripts\python evaluate.py                                  # 10 labelled emails, scored
.venv\Scripts\python evaluate.py --emails emails/practice
.venv\Scripts\python evaluate.py --emails path\to\new_emails --answers none   # unseen emails
.venv\Scripts\python evaluate.py --verbose                        # show step logs (iterations, tokens)
.venv\Scripts\python evaluate.py --url http://127.0.0.1:8000      # through the running API
```

### Latest result

Labelled evaluation run **2026-11-23**: **10/10 emails and 50/50 fields correct**.
All 10 orders completed with **30 LLM calls** (30 attempts, 0 retries), **33,171 prompt
tokens** and **2,814 completion tokens**. Extraction averaged **1 iteration** per order; no
fallback replies, reply-check failures, or recorded errors occurred. End-to-end latency averaged
**6,022 ms** (P95: **9,797 ms**). Estimated cost is unavailable because pricing rates are not
configured. The machine-readable per-order and aggregate report is in
[`results/labelled/summary.json`](./results/labelled/summary.json).

## Reliability: cross-checking and the iteration limit

Each extraction iteration (`app/steps/extraction_loop.py`):

1. The LLM extracts the order (with the previous attempt and the problems found, on a re-check).
2. **Code** checks grounding: customer, city, product names, quantity text and the deadline phrase must appear in the email; a quantity must match its own text ("forty" -> 40, "a few" -> no number); an explicit date must match the phrase it came from.
3. **A second LLM call (verifier)** independently judges every field, including nulls ("is the city really missing?") and items left out. It returns only verdicts and quotes, never values; code checks its quotes are really in the email.
4. **Code decides**: all good -> stop. Otherwise feed the problems back and re-extract.

The loop stops when everything checks out, when a re-extraction is identical to the previous one (no progress), or after `EXTRACTION_MAX_ITERATIONS` (default **5**, in `.env`). It cannot loop forever. Problems still open at the end become issues, so the order is **Need info** and a person checks it.

Happy path cost: 2 LLM calls for extraction + 1 for the reply. Worst case: 10 + 2.

## Other design notes

- **Prompt injection** (e.g. email_05): the email is data inside `<email_body>` tags, no LLM schema has a status/discount field, status is computed in code, and the reply step only gets the computed result.
- **Deadline** is extracted as a string and converted with `date.fromisoformat` in code. Relative dates are resolved by the model using the `Date:` header (with weekday); the lead-time check also uses the header date.
- **Stock is read-only**: one fixed parameterised `SELECT` per email (`StockRepository.find_by_names`). The comparison (`check_stock`) is a pure function, tested without a DB.
- **Reply check**: no prices, money, discounts or percentages, and no number not in the result. A failing draft is redrafted once, then replaced by a template.
- **LLM client** (`app/llm_client.py`) is the only code touching the SDK: timeout, retries with backoff on 429/timeouts/connection/5xx, logs model, latency and tokens per call. Uses `max_completion_tokens`, no `temperature`.
- **Unstructured input**: no headers, HTML-only bodies and CRLF line endings are handled in `app/email_parser.py`.
- **Postgres driver**: `pg8000` (pure Python), because Windows Application Control blocked psycopg's binary DLL on this machine. Host port 5433 because 5432 is used by another project.
