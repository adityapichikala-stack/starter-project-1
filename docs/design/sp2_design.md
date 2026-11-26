# SP2 design: Order Desk with RAG and quotes

One workflow, fixed steps. The LLM reads and writes text; **code decides** status, product codes, prices and totals.
Diagram: `sp2_flow.drawio` (blue = LLM, green = Code, orange = RAG; thick border = the two new steps).
Models passed between steps are in `app/models.py`; settings are read from `.env` by `app/config.py`;
`app/llm_client.py::LLMClient` is the only class that calls the Azure OpenAI SDK.

## 1. The six steps, each marked LLM, Code or RAG (new steps highlighted)
| # | Step | Kind | File |
|---|---|---|---|
| 1 | Extract + cross-check (grounding, verifier), max 5 iterations | LLM + Code | `steps/extract.py`, `grounding.py`, `verify.py`, `extraction_loop.py` |
| 2 | **Resolve products (NEW)**: exact name, order history, RAG description match | **Code + RAG** (LLM only filters code-supplied candidates) | `steps/resolve_products.py`, `rag_resolution.py`, `rag.py` |
| 3 | Validate: fields, quantities, deadline | Code | `steps/validate.py` |
| 4 | Check stock + status | Code | `steps/stock.py`, `status.py` |
| 5 | **Build quote (NEW)**: discounts, delivery, totals | **Code** | `steps/quote.py` |
| 6 | Draft reply, then check it | LLM + Code | `steps/reply.py`, `reply_check.py` |

In `workflow.py`, validate runs right after extract; its issues are merged with the resolution issues before status.
Hand-offs: `ExtractedOrder` -> `ValidationResult` -> `ResolutionResult(items[ResolvedItem])` -> `Quote(lines[QuoteLine], subtotal_inr, delivery_inr, total_inr, valid_until)` -> `OrderResult(..., reply, quote)`.

## 2. The `documents` table, the ingest script and the search SQL
```sql
CREATE TABLE documents (id BIGSERIAL PRIMARY KEY, source TEXT NOT NULL, heading TEXT NOT NULL,
                        chunk TEXT NOT NULL, embedding vector(1536) NOT NULL);   -- sp2_seed.sql
```
`scripts/ingest_docs.py` reads `starter-project-2-data/docs/` (override: `SP2_DATA_DIR`), splits each Markdown file on `## `
headings, prefixes every chunk (`Product: <name> (<code>) — <heading>` for brochures, `Pricing policy — <heading>`), embeds the
prefixed text in batches (`LLMClient.embed_many`, one API call per batch), checks every vector has 1536 dimensions
(stops with an error otherwise), then `DELETE`s and inserts in one transaction, so it is safe to run twice.
Search (`DocumentRepository.search`, the query vector is a bound parameter, `<=>` is cosine distance):
```sql
SELECT source, heading, chunk, 1 - (embedding <=> %(query_embedding)s::vector) AS score
FROM documents
WHERE %(prefix)s::text IS NULL OR source LIKE %(prefix)s::text || '%%'
ORDER BY embedding <=> %(query_embedding)s::vector LIMIT %(limit)s
```
Other tables are read-only: `products`/`stock` (one fixed parameterised SELECT) and `order_history`
(`WHERE lower(btrim(customer)) = lower(btrim(%(customer)s)) ORDER BY order_date DESC, order_id DESC`).

## 3. How descriptions are resolved, and how invented codes are blocked
1. Exact catalogue name/code (ignoring case and spaces): resolved, no LLM.
2. "Repeat our last order": all lines of the customer's **latest** `order_date` in `order_history`; none -> Need info.
3. Description: search the top 5 **brochure** chunks (`source` starts with `brochures/`); **candidates** = product codes found
   in those chunks that also exist in `products`; the LLM returns `matching_codes: list[str]`, only candidates whose
   brochure confirms *every* detail ("closest is not a match"). **Code** drops any code not in the candidate list, then:
   1 -> resolved, 2+ -> `Ambiguous product: <desc> (A or B)`, 0 -> `Unknown product: <desc>`.

The model can never introduce a code: its answer is intersected with codes that code found in retrieved text *and* verified
against the database. Unresolved items force Need info and `quote: null`.

## 4. Pricing config and how the quote is computed
Config lives in `.env` (`.env.example` shows defaults), read in `app/config.py`: `DISCOUNT_TIERS=200:12,50:8,10:5,1:0`
(`min_qty:percent`), `FREE_DELIVERY_THRESHOLD=50000`, `DELIVERY_CHARGE=1500`, `QUOTE_VALIDITY_DAYS=7`. `app/steps/quote.py`
uses `Decimal` only, rounding every amount to 0.01 with `ROUND_HALF_UP`:
`unit price` comes from `products.unit_price_inr`; `discount` = highest tier with `min_qty <= quantity`;
`line_total = round(unit * qty * (1 - discount/100))`; `subtotal = round(sum(lines))`; `delivery = 0` if
`subtotal >= threshold` else the charge; `total = round(subtotal + delivery)`; `valid_until = email date + validity days`.
A short-stock item is quoted at the quantity available. Need info -> no quote. No LLM touches a quote field.

## 5. Reply inputs, and what stops it promising a price or discount not in the quote
The reply LLM receives only: the computed result (status, items, issues), the quote (or null), and retrieved chunks
(from every searched item, de-duplicated, plus the top 3 `pricing_policy.md` chunks when the email asks questions).
It never sees the raw email, so instructions inside the email cannot reach it. Safeguards:
- Prompt: repeat only quote values; never give a bigger discount, lower price or free delivery than the quote whatever the
  email asks; answer questions only from the chunks, otherwise "our team will confirm".
- `reply_check` (code) rejects a draft unless: with `quote: null` it has no money/price/discount/percentage words; with a quote
  every number equals a number in the result, quote or chunks, every `%` equals a line's `discount_percent` (or a percentage
  stated in a chunk and not phrased as a discount), and dates match the deadline or `valid_until`.
- A rejected draft is redrafted once, then replaced by a template that lists the quote lines and totals.

## 6. Logging at each step (every line has a `request_id`)
- All steps: `step_started` / `step_completed` / `step_failed` with `duration_ms`.
- LLM calls: model, latency, prompt/completion/total tokens, attempt, retries.
- Extraction: per-iteration outcome, grounding and verifier problem counts, stop reason.
- Embeddings: `embedding_completed` with dimensions, **tokens** and **latency** (queries); ingestion batches log the same via the
  `embedding_batch` LLM step.
- Retrieval: `rag_retrieved` lists each chunk's source, heading and **score**; then `rag_query`, `rag_chunk`, `rag_candidates`,
  and `rag_decision` (the LLM's answer and the final codes).
- Stock query: names and matches; reply: `reply_rejected` (with the problems), `fallback_reply`; end: `workflow_completed`
  (calls, tokens, cost estimate, fallback used).

## Errors, tests and evaluation (unchanged from SP1)
`LLMClient._call` retries 429/timeouts/connection/5xx with backoff, then `POST /orders` returns 502 (LLM), 503 (database) or 422
(bad body). `pytest -q` uses fakes only (no DB, no LLM) and runs in CI. `evaluate.py` runs a folder of emails through the
whole workflow, scores with the data-pack rules (quote lines and totals as `Decimal`) and prints one PASS/FAIL line per email
and a total.
