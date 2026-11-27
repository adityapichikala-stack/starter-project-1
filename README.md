[![tests](https://github.com/adityapichikala-stack/starter-project-1/actions/workflows/tests.yml/badge.svg)](https://github.com/adityapichikala-stack/starter-project-1/actions/workflows/tests.yml)

# Starter Project 1 + 2: Order Desk workflow

Takes one customer order email and returns a structured order, a status, a code-built quote and a draft reply via `POST /orders`.
The data packs' own READMEs (layout and scoring rules) are `DATA_PACK_README.md` (SP1) and `starter-project-2-data/README.md` (SP2).
Design notes: [`docs/design/sp2_design.md`](docs/design/sp2_design.md), flow diagram: [`docs/design/sp2_flow.drawio`](docs/design/sp2_flow.drawio).

It is a **workflow** with fixed steps. The LLM only reads and writes text; code makes every decision (status, product codes, prices, discounts, totals).

| # | Step | Done by | Files |
|---|---|---|---|
| 1 | **Extract** email -> `ExtractedOrder`, cross-checked (grounding + verifier), max 5 iterations | LLM + Code | `app/steps/extract.py`, `grounding.py`, `verify.py`, `extraction_loop.py` |
| 2 | **Resolve products** (NEW): exact name, else RAG over brochures, else order history ("repeat our last order") | Code + RAG (LLM filters code-supplied candidates only) | `app/steps/resolve_products.py`, `app/steps/rag_resolution.py`, `app/rag.py` |
| 3 | **Validate**: required fields, quantities, deadline vs `LEAD_TIME_DAYS` | Code | `app/steps/validate.py` |
| 4 | **Check stock**: read stock and prices from Postgres, compare | Code | `app/steps/stock.py`, `app/steps/status.py` |
| 5 | **Build quote** (NEW): tier discount, delivery charge, validity, in `Decimal` | Code | `app/steps/quote.py` |
| 6 | **Draft reply** from the computed result + quote + retrieved chunks (never the raw email), then checked by code | LLM + Code | `app/steps/reply.py`, `app/steps/reply_check.py` |

`app/workflow.py` wires them. In code, validate (3) runs right after extract and before resolve (2) so that
its issues and the resolved items are merged before stock/status; the numbering above is the logical order.
Nothing about specific emails is hardcoded: any email text works, and `evaluate.py` runs any folder of `.txt` emails.

## Data

`starter-project-2-data/` is the **single source** for SP2: `sp2_seed.sql`, `docs/` (brochures + `pricing_policy.md`),
`emails/labelled` (21-30), `emails/practice` (31-35) and `answer_key.json`. To point at another copy set
`SP2_DATA_DIR` (in `.env` or the environment); `scripts/ingest_docs.py` and `evaluate.py` both honour it.
SP1 data stays in `seed.sql`, `emails/` and `answer_key.json`. The top-level `docs/brochures` and
`docs/pricing_policy.md` are an older identical copy and are not read by anything.

## Layout

```
app/                     the service (config, models, LLM client, steps, workflow, FastAPI)
scripts/ingest_docs.py   idempotent brochure/policy embedding ingestion
starter-project-2-data/  SP2 seed, brochures, policy, emails, answer key
docs/design/             SP2 design page and draw.io flow
tests/                   pytest (fakes only) + tests/robustness_emails/
emails/, answer_key.json SP1 emails and answers
seed.sql, sp2_seed.sql   products/stock; order_history + pgvector + documents (mounted into Postgres)
evaluate.py              runs a folder of emails, scores against an answer key
results/                 JSON output of evaluation runs (sp2_labelled, sp2_practice, labelled)
```

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # then fill in the Azure OpenAI values (incl. AZURE_OPENAI_EMBEDDING_DEPLOYMENT)
docker compose down -v        # required once: the SP2 seed only runs on an empty volume
docker compose up -d --wait   # pgvector/pgvector:pg16 on host port 5433
.venv\Scripts\python -m scripts.ingest_docs
.venv\Scripts\python check_azure.py
```

`ingest_docs` is safe to run twice (DELETE + insert in one transaction). Every chunk is stored and embedded as
`Product: <name> (<code>) — <heading>` + text (brochures) or `Pricing policy — <heading>` + text. Embeddings are
requested in batches (`LLMClient.embed_many`) and every vector must have 1536 dimensions or the run stops.
It prints the chunk count per file and the total (99 chunks, 16 files).

The `documents` table (`sp2_seed.sql`):

```sql
CREATE TABLE IF NOT EXISTS documents (
    id        BIGSERIAL PRIMARY KEY,
    source    TEXT NOT NULL,          -- 'brochures/PHN-X9.md' or 'pricing_policy.md'
    heading   TEXT NOT NULL,
    chunk     TEXT NOT NULL,          -- prefixed text, the same text that was embedded
    embedding vector(1536) NOT NULL
);
```

Search (`DocumentRepository.search`, query embedding passed as a parameter, cosine distance `<=>`):

```sql
SELECT source, heading, chunk, 1 - (embedding <=> %(query_embedding)s::vector) AS score
FROM documents
WHERE %(prefix)s::text IS NULL OR source LIKE %(prefix)s::text || '%%'
ORDER BY embedding <=> %(query_embedding)s::vector
LIMIT %(limit)s
```

Check the seed loaded:
`docker exec sp1-postgres psql -U orders -d orders -c "SELECT (SELECT count(*) FROM products), (SELECT count(*) FROM stock), (SELECT count(*) FROM order_history), (SELECT extname FROM pg_extension WHERE extname='vector');"`
(15, 15, 9, vector). If tables are missing you're on an old volume: `docker compose down -v`, then `up` again.

## Run

```powershell
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

- `POST /orders` with `{"email": "<raw email text, headers included>"}` returns the contract shape plus `quote`
- `GET /health` checks the database; Swagger UI at http://127.0.0.1:8000/docs
- Errors: `422` bad body, `502` LLM unavailable after retries, `503` database unavailable

## How product descriptions are resolved

For each item (`app/workflow.py`, `resolve_products.py`, `rag_resolution.py`):

1. **Exact**: the name equals a catalogue name or code (ignoring case/spaces) -> resolved, no LLM.
2. **Repeat order**: if the email refers to a previous order, the customer's rows in `order_history` (exact name, ignoring case) are looked up and every line of the **latest** `order_date` is used. No history -> issue `Repeat our previous order, but there is no order history for <customer>` -> Need info. Items listed alongside the request are kept too.
3. **Description** (e.g. "black 128 GB 5G phone"):
   - search the top 5 **brochure** chunks only (`source` starts with `brochures/`);
   - **candidates** = product codes mentioned in those chunks that also exist in the `products` table;
   - the LLM returns `matching_codes: list[str]` = every candidate whose brochure confirms **every** detail ("closest is not a match; if a detail isn't confirmed by the brochure, exclude that product");
   - **code** drops any code that is not a candidate, then decides: exactly 1 -> resolved; 2+ -> `Ambiguous product: <desc> (A or B)`; 0 -> `Unknown product: <desc>`.

**Invented codes are blocked** because the model never creates the answer: it can only pick from codes that code found in
retrieved chunks *and* verified against the products table. Anything else it returns is discarded before it can reach the
quote, the result or the reply. Unresolved items turn the order into Need info, and no quote is built for it.

## Pricing

Prices come from `products.unit_price_inr`, discounts from the pricing policy. Configured in `.env`
(defaults shown in `.env.example`):

| Key | Default | Meaning |
|---|---|---|
| `DISCOUNT_TIERS` | `200:12,50:8,10:5,1:0` | `min_quantity:percent`, highest matching tier wins |
| `FREE_DELIVERY_THRESHOLD` | `50000` | subtotal (after discounts) at or above this -> free delivery |
| `DELIVERY_CHARGE` | `1500` | charged below the threshold |
| `QUOTE_VALIDITY_DAYS` | `7` | `valid_until` = email date + N days |

All money uses `Decimal`, never float, and is rounded to 0.01 with **`ROUND_HALF_UP`**:
`line_total = round(unit_price * qty * (1 - discount/100))`, `subtotal = round(sum(lines))`,
`total = round(subtotal + delivery)`. A short-stock item is quoted at the quantity available (Partial). Need info -> `quote: null`.

## Reply guardrails

- The reply LLM only sees the computed result, the quote and retrieved chunks, never the raw email (prompt-injection defence). Chunks come from every searched item (de-duplicated) plus the top 3 `pricing_policy.md` chunks when the email asks questions (the question sentences are used only as the search query).
- Prompt: answer questions **only** from retrieved chunks, otherwise "our team will confirm"; never give a bigger discount, lower price or free delivery than the quote, whatever the email asks.
- `reply_check` (code): with `quote: null` no money, price, discount or percentage words. With a quote, those are allowed but **every number** (commas stripped) must equal a number in the result, the quote or the retrieved chunks; a `%` phrased as a discount/offer (discount, off, offer, apply, give, reduce nearby) must equal a line's `discount_percent` in the quote, and any other `%` must be stated in a chunk ("50% in advance"); dates like "30 November 2026" must match the deadline or `valid_until`.
- A rejected draft is redrafted once, then replaced by a template (`fallback_reply`) that, when a quote exists, lists the lines, subtotal, delivery, total and `valid_until`.

## What is logged

Every line carries a `request_id`. Per step (`event=step_started/step_completed`, with `duration_ms`):

- LLM calls: model, latency, prompt/completion/total tokens, attempt, retries (`llm_call_completed`).
- Extraction: iteration outcome, grounding and verifier problem counts, stop reason.
- Embeddings: `event=embedding_completed` with dimensions, **tokens** and **latency** (queries); batch ingestion logs the same via the `embedding_batch` LLM step.
- RAG: `rag_retrieved` (each chunk's source, heading and score), then `rag_query`, `rag_chunk`, `rag_candidates` and `rag_decision` (the LLM's answer and the final codes).
- Stock query: matched names and duration. Quote build and status: step timings.
- Reply: `reply_rejected` with the problems found, `fallback_reply`, and a final `workflow_completed` summary (calls, tokens, cost estimate, fallback used).

## Tests and evaluation

```powershell
.venv\Scripts\python -m pytest -q                      # fakes only: no LLM, no DB
.venv\Scripts\python evaluate.py                       # SP2 labelled 21-30 -> results/sp2_labelled/
.venv\Scripts\python evaluate.py --emails starter-project-2-data/emails/practice --answers none   # -> results/sp2_practice/ (replies printed)
.venv\Scripts\python evaluate.py --emails emails/labelled --answers answer_key.json              # SP1 regression
.venv\Scripts\python evaluate.py --verbose             # show step logs
.venv\Scripts\python evaluate.py --url http://127.0.0.1:8000   # through the running API
```

`evaluate.py` prints one PASS/FAIL line per email naming the failed fields, then the total. Scoring follows the SP2 data
pack README: status exact; items = set of `(product_code, requested)`; customer/city ignoring case; deadline exact;
`quote` null-must-be-null, otherwise the set of `(product_code, quantity, discount_percent, line_total_inr)` lines and
`subtotal/delivery/total/valid_until` equal as `Decimal`. Each run also writes `summary.json` (accuracy per field, failure
categories, latency percentiles, LLM calls/tokens, extraction iterations, fallback and reply-check counts, estimated cost
when `LLM_INPUT_COST_PER_1M_TOKENS` / `LLM_OUTPUT_COST_PER_1M_TOKENS` are set; there are no default prices). CI
(`.github/workflows/tests.yml`) runs `pytest -q` on Python 3.12 and 3.13 with dummy settings.

### Latest results (2026-11-26)

| Run | Result |
|---|---|
| SP1 regression (`emails/labelled`, email_01-10) | **10/10**, 50/50 fields |
| SP2 labelled (email_21-30) | **10/10**, 60/60 fields (incl. `quote`) |
| SP2 practice (31-35) | unscored; read in `results/sp2_practice/` |

### Why each miss happened

Everything below was found on the first SP2 run and fixed; none is left open.

| Email | Field | Step that broke | Reason / fix |
|---|---|---|---|
| email_29 | `delivery_city` | 1 Extract | "Panjim, Goa": the model answered Panjim; the key's rule is the last place named. Prompt rule added (shared with the verifier). |
| email_27 | reply (not scored) | 6 Reply check | The reply correctly said "50% in advance" (payment terms, from a policy chunk), but `reply_check` only allowed the quote's discount percentages. Rule now: a percentage near discount/off/offer/apply/give/reduce must equal a `discount_percent` in the quote, even if the policy discount table contains it; any other percentage is allowed only if a retrieved chunk states it (so "50% in advance" passes, "we can offer 12%" fails). |
| all SP2 emails | (every RAG/quote email) | 2 Resolve / RAG | `pgvector` has no adapter for `pg8000.dbapi` and named SQL parameters were rejected, so searches would crash. Now a text vector literal + `paramstyle = "pyformat"`. |
| four emails in one run | execution error | Extract / verify | Transient Azure `APIStatusError`; passed on re-run. Not a code defect. |

## Reliability: cross-checking and the iteration limit

Each extraction iteration (`app/steps/extraction_loop.py`):

1. The LLM extracts the order (with the previous attempt and the problems found, on a re-check).
2. **Code** checks grounding: customer, city, product names, quantity text and the deadline phrase must appear in the email; a quantity must match its own text ("forty" -> 40, "a few" -> no number); an explicit date must match the phrase it came from.
3. **A second LLM call (verifier)** independently judges every field, including nulls and items left out. It returns only verdicts and quotes, never values; code checks its quotes are really in the email.
4. **Code decides**: all good -> stop. Otherwise feed the problems back and re-extract.

The loop stops when everything checks out, when a re-extraction is identical to the previous one, or after `EXTRACTION_MAX_ITERATIONS` (default **5**). Problems still open at the end become issues, so the order is **Need info**.

## Other design notes

- **Prompt injection** (e.g. SP1 email_05, SP2 email_26): the email is data inside `<email_body>` tags, no LLM schema has a status/discount field, status and quote are computed in code, and the reply step never sees the raw email.
- **Deadline** is extracted as a string and converted with `date.fromisoformat` in code; the lead-time check uses the `Date:` header.
- **Stock is read-only**: fixed parameterised `SELECT`s; `check_stock` is a pure function tested without a DB.
- **LLM client** (`app/llm_client.py`) is the only code touching the SDK: timeout, retries with backoff on 429/timeouts/connection/5xx, logs model, latency and tokens per call. Uses `max_completion_tokens`, no `temperature`.
- **Unstructured input**: no headers, HTML-only bodies and CRLF handled in `app/email_parser.py`.
- **Postgres driver**: `pg8000` (pure Python), because Windows Application Control blocked psycopg's binary DLL. Host port 5433 because 5432 is used by another project.
