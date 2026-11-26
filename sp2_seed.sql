-- Starter Project 2 seed: run AFTER the Starter Project 1 seed.sql.
-- Adds the pgvector extension and past orders for "same as last time" emails.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE order_history (
    order_id      INTEGER     NOT NULL,
    customer      TEXT        NOT NULL,
    order_date    DATE        NOT NULL,
    product_code  VARCHAR(10) NOT NULL REFERENCES products (product_code),
    quantity      INTEGER     NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, product_code)
);

INSERT INTO order_history (order_id, customer, order_date, product_code, quantity) VALUES
    (1001, 'Sahyadri Tech Solutions', '2026-08-14', 'LAP-14', 3),
    (1001, 'Sahyadri Tech Solutions', '2026-08-14', 'CHG-65', 3),
    (1007, 'Sahyadri Tech Solutions', '2026-10-02', 'LAP-14', 5),
    (1007, 'Sahyadri Tech Solutions', '2026-10-02', 'CHG-65', 5),
    (1003, 'Greenfield Public School', '2026-09-10', 'TAB-08', 20),
    (1003, 'Greenfield Public School', '2026-09-10', 'EAR-01', 20),
    (1005, 'Brightpath Coaching Centre', '2026-07-22', 'TAB-10', 10),
    (1009, 'Brightpath Coaching Centre', '2026-10-18', 'TAB-08', 25),
    (1004, 'Orbit Retail', '2026-09-28', 'PHN-A5', 8);

CREATE TABLE IF NOT EXISTS documents (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    heading     TEXT NOT NULL,
    chunk       TEXT NOT NULL,
    embedding   vector(1536) NOT NULL
);
