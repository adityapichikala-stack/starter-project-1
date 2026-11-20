-- Starter Project 1 seed data: a fictional electronics distributor.
-- Loaded automatically when mounted into /docker-entrypoint-initdb.d on a fresh volume.
-- unit_price_inr is not used until Starter Project 2.

CREATE TABLE products (
    product_code   VARCHAR(10) PRIMARY KEY,
    name           TEXT        NOT NULL UNIQUE,
    category       TEXT        NOT NULL,
    unit_price_inr INTEGER     NOT NULL CHECK (unit_price_inr > 0)
);

CREATE TABLE stock (
    product_code       VARCHAR(10) PRIMARY KEY REFERENCES products (product_code),
    quantity_available INTEGER     NOT NULL CHECK (quantity_available >= 0)
);

INSERT INTO products (product_code, name, category, unit_price_inr) VALUES
    ('TAB-10', 'Nimbus Tab 10', 'Tablet', 14999),
    ('TAB-08', 'Nimbus Tab 8 Lite', 'Tablet', 9499),
    ('LAP-14', 'Corelink Book 14', 'Laptop', 52000),
    ('LAP-15', 'Corelink Book 15 Pro', 'Laptop', 78500),
    ('PHN-A5', 'Astra A5', 'Smartphone', 12999),
    ('PHN-X9', 'Astra X9', 'Smartphone', 34999),
    ('EAR-01', 'Pebble Buds', 'Earbuds', 1299),
    ('EAR-02', 'Pebble Buds Pro', 'Earbuds', 3499),
    ('HDS-10', 'Quietline Headset', 'Headset', 2299),
    ('WCH-S2', 'Pulse Watch S2', 'Smartwatch', 4999),
    ('RTR-AX3', 'Meshwave AX3000 Router', 'Router', 6499),
    ('RTR-5G', 'Meshwave 5G Home Router', 'Router', 11999),
    ('MON-24', 'Clearview 24 Monitor', 'Monitor', 10500),
    ('PWB-20', 'Volt 20000 Power Bank', 'Power bank', 1999),
    ('CHG-65', 'Volt 65W Charger', 'Charger', 1499);

INSERT INTO stock (product_code, quantity_available) VALUES
    ('TAB-10', 25),
    ('TAB-08', 60),
    ('LAP-14', 12),
    ('LAP-15', 0),
    ('PHN-A5', 40),
    ('PHN-X9', 6),
    ('EAR-01', 350),
    ('EAR-02', 80),
    ('HDS-10', 45),
    ('WCH-S2', 0),
    ('RTR-AX3', 30),
    ('RTR-5G', 15),
    ('MON-24', 18),
    ('PWB-20', 120),
    ('CHG-65', 200);
