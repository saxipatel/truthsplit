#!/bin/sh
# Demo "shop" reporting schema with deterministic synthetic data.
#
# Every value is derived from the row number with integer arithmetic (no random()), so the data is
# identical on every machine and PostgreSQL version, and expected totals can be recomputed by hand.
# Runs on first initialisation of the source volume; `make seed-source` re-runs it (drops and recreates).
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v reader="$SOURCE_READER_USER" <<'SQL'
BEGIN;
DROP TABLE IF EXISTS refunds, payments, order_items, orders, products, customers CASCADE;

CREATE TABLE customers (
    id integer PRIMARY KEY,
    name text NOT NULL,
    region text NOT NULL
);

CREATE TABLE products (
    id integer PRIMARY KEY,
    sku text NOT NULL UNIQUE,
    category text NOT NULL,
    unit_price numeric(12, 2) NOT NULL CHECK (unit_price > 0)
);

CREATE TABLE orders (
    id integer PRIMARY KEY,
    order_number text NOT NULL UNIQUE,
    customer_id integer NOT NULL REFERENCES customers,
    status text NOT NULL CHECK (status IN ('completed', 'cancelled')),
    ordered_at timestamptz NOT NULL,
    currency char(3) NOT NULL,
    total_amount numeric(12, 2) NOT NULL DEFAULT 0
);
CREATE INDEX ON orders (ordered_at);

CREATE TABLE order_items (
    id integer PRIMARY KEY,
    order_id integer NOT NULL REFERENCES orders,
    line_no integer NOT NULL,
    product_id integer NOT NULL REFERENCES products,
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(12, 2) NOT NULL,
    line_amount numeric(12, 2) NOT NULL,
    UNIQUE (order_id, line_no)
);

CREATE TABLE payments (
    id integer PRIMARY KEY,
    order_id integer NOT NULL REFERENCES orders,
    paid_at timestamptz NOT NULL,
    method text NOT NULL,
    amount numeric(12, 2) NOT NULL CHECK (amount > 0)
);
CREATE INDEX ON payments (order_id);

CREATE TABLE refunds (
    id integer PRIMARY KEY,
    order_id integer NOT NULL REFERENCES orders,
    refunded_at timestamptz NOT NULL,
    reason text NOT NULL,
    amount numeric(12, 2) NOT NULL CHECK (amount > 0)
);
CREATE INDEX ON refunds (order_id);

INSERT INTO customers
SELECT i, 'Customer ' || i, (ARRAY['NA', 'EU', 'APAC', 'LATAM'])[1 + i % 4]
FROM generate_series(1, 200) i;

INSERT INTO products
SELECT i, 'SKU-' || lpad(i::text, 3, '0'), (ARRAY['hardware', 'software', 'services'])[1 + i % 3],
       5 + (i * 37) % 200 + (i % 4) * 0.25
FROM generate_series(1, 40) i;

-- 6,000 orders spread over March and April 2026 (UTC), minute resolution.
INSERT INTO orders (id, order_number, customer_id, status, ordered_at, currency)
SELECT i,
       'SO-' || lpad(i::text, 5, '0'),
       1 + (i * 31) % 200,
       CASE WHEN (i * 13) % 100 BETWEEN 85 AND 89 THEN 'cancelled' ELSE 'completed' END,
       timestamptz '2026-03-01 00:00:00+00' + ((i * 7919) % (61 * 24 * 60)) * interval '1 minute',
       'USD'
FROM generate_series(1, 6000) i;

-- 1 to 4 lines per order.
INSERT INTO order_items (id, order_id, line_no, product_id, quantity, unit_price, line_amount)
SELECT row_number() OVER (ORDER BY o.id, l), o.id, l, p.id, q, p.unit_price, q * p.unit_price
FROM orders o
CROSS JOIN LATERAL generate_series(1, 1 + (o.id * 17) % 4) l
CROSS JOIN LATERAL (SELECT 1 + (o.id + l) % 3 AS q) qty
JOIN products p ON p.id = 1 + (o.id * 11 + l * 7) % 40;

UPDATE orders o SET total_amount = t.total
FROM (SELECT order_id, sum(line_amount) total FROM order_items GROUP BY order_id) t
WHERE t.order_id = o.id;

-- One payment per non-cancelled order; every 12th order is split 60/40 across two payments.
INSERT INTO payments (id, order_id, paid_at, method, amount)
SELECT row_number() OVER (ORDER BY o.id, part), o.id, o.ordered_at + (o.id % 90) * interval '1 minute',
       CASE part WHEN 1 THEN 'card' ELSE 'gift_card' END,
       CASE WHEN o.id % 12 <> 0 THEN o.total_amount
            WHEN part = 1 THEN round(o.total_amount * 0.6, 2)
            ELSE o.total_amount - round(o.total_amount * 0.6, 2) END
FROM orders o
CROSS JOIN LATERAL generate_series(1, CASE WHEN o.id % 12 = 0 THEN 2 ELSE 1 END) part
WHERE o.status = 'completed';

-- Partial refunds (25%) on orders with id % 10 = 3, full refunds on id % 25 = 7. The two sets are disjoint.
INSERT INTO refunds (id, order_id, refunded_at, reason, amount)
SELECT row_number() OVER (ORDER BY o.id), o.id, o.ordered_at + (2 + o.id % 20) * interval '1 day',
       CASE WHEN o.id % 25 = 7 THEN 'full' ELSE 'partial' END,
       CASE WHEN o.id % 25 = 7 THEN o.total_amount ELSE round(o.total_amount * 0.25, 2) END
FROM orders o
WHERE o.status = 'completed' AND (o.id % 10 = 3 OR o.id % 25 = 7);

GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader";
COMMIT;
ANALYZE;
SQL
