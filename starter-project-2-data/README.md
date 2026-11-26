# Starter Project 2: data pack

Use this together with your Starter Project 1 data pack. Keep everything from Starter Project 1: the same `seed.sql`, the same 15 products and the same stock.

## What's here

| Path | What it is |
|---|---|
| `sp2_seed.sql` | Turns on the pgvector extension and creates the `order_history` table (past orders, for "same as last time" emails). Run it after the Starter Project 1 `seed.sql` |
| `docs/brochures/` | One brochure per product (15 Markdown files): overview, key specs, who it's for, warranty. These are what you search with RAG |
| `docs/pricing_policy.md` | Bulk discounts, what a quote covers, delivery charge and times, quote validity, payment terms, warranty and returns |
| `emails/labelled/` | 10 new emails, `email_21.txt` to `email_30.txt`. Their expected results are in `answer_key.json` |
| `emails/practice/` | 5 more emails with no answers, `email_31.txt` to `email_35.txt` |
| `answer_key.json` | The expected output for each labelled email: the output contract without `reply` |

The brochures don't contain prices. Unit prices come from the `products` table, and discounts from the pricing policy.

## Loading the data

Switch the Postgres image in your Compose file from `postgres:16` to `pgvector/pgvector:pg16`, and mount both seed files into `/docker-entrypoint-initdb.d/`. Postgres runs them in name order, so name the mounts so the Starter Project 1 seed goes first:

```yaml
volumes:
  - ./starter-project-1-data/seed.sql:/docker-entrypoint-initdb.d/01_seed.sql
  - ./starter-project-2-data/sp2_seed.sql:/docker-entrypoint-initdb.d/02_sp2_seed.sql
```

The scripts only run on an empty data volume, so remove your old volume (`docker compose down -v`) or use a new one. Then check: `products` and `stock` have 15 rows each, `order_history` has 9, and `SELECT extname FROM pg_extension;` lists `vector`.

Your `documents` table (chunks and their embeddings) is yours to design. Fill it with an ingest script that reads `docs/`.

## Regression check

Your Starter Project 1 emails and `answer_key.json` still apply. Every Starter Project 1 field must come out the same as before. Run your Starter Project 1 evaluation against Starter Project 2 to prove nothing broke.

## How results are scored

| Field | Counts as correct when |
|---|---|
| `status` | Exact match |
| `items` | The set of `(product_code, requested)` pairs matches, in any order |
| `customer`, `delivery_city` | Match ignoring case and surrounding spaces. `null` must be `null` |
| `deadline` | Exact `YYYY-MM-DD` match. `null` must be `null` |
| `quote` | `null` must be `null`. Otherwise: the set of `(product_code, quantity, discount_percent, line_total_inr)` lines matches, and `subtotal_inr`, `delivery_inr`, `total_inr` and `valid_until` match exactly, to the paisa |
| `issues` | Not scored. They're there so you can see why a result came out the way it did |
| `reply` | Not scored automatically. Your buddy reads it |

Every email has `From`, `Date` and `Subject` headers, then a blank line, then the body. Read them with Python's `email` module, as in Starter Project 1.
