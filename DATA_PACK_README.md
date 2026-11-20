# Starter Project 1: data pack

## What's here

| Path | What it is |
|---|---|
| `seed.sql` | Creates and fills the `products` and `stock` tables for a fictional electronics distributor (15 products) |
| `emails/labelled/` | 10 customer emails, `email_01.txt` to `email_10.txt`. Their expected results are in `answer_key.json` |
| `emails/practice/` | 5 more emails with no answers. Use them to see how your workflow handles mail it wasn't tuned on |
| `answer_key.json` | The expected output for each labelled email: the output contract without `reply` |

Every email has `From`, `Date` and `Subject` headers, then a blank line, then the body. Read them with Python's `email` module (`email.message_from_string`) instead of splitting the text on a blank line yourself. On Windows, Git can turn the line endings into CRLF, and a split on `"\n\n"` then finds nothing.

## Loading the seed data

Mount `seed.sql` into the Postgres container's `/docker-entrypoint-initdb.d/` folder in your Compose file. The official Postgres image runs the scripts in that folder only when it starts with an empty data volume. If the tables don't show up, you're reusing an old volume: remove it or point Compose at a new one.

`unit_price_inr` isn't used in Starter Project 1. Starter Project 2 needs it.

Stock is read-only in this project. Check every email against the seeded quantities, and don't reserve or reduce stock after an order. Otherwise the results depend on the order you run the emails in.

## How results are scored

| Field | Counts as correct when |
|---|---|
| `status` | Exact match |
| `items` | The set of `(product_code, requested)` pairs matches, in any order |
| `customer`, `delivery_city` | Match ignoring case and surrounding spaces. `null` must be `null` |
| `deadline` | Exact `YYYY-MM-DD` match. `null` must be `null` |
| `issues` | Not scored. They're there so you can see why a result came out the way it did |
| `reply` | Not scored automatically. Your buddy reads it |
