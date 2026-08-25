-- Planning fact schema. Portable SQL subset: runs on SQLite (tests) and
-- PostgreSQL (InvenTree production). Django migrations in the plugin app are
-- generated FROM this file; this file is the contract, not the deployment tool.
--
-- Grain: (sku_id, loc_id, bucket_date, measure, scenario_id) -> qty
-- Buckets are DAILY. bucket_date IS the bucket; there is no bucket table.
-- Storage is SPARSE: an absent row means zero, never unknown.
-- sku_id and loc_id are logical references into InvenTree. No foreign key —
-- InvenTree core is read-only and may live in a separate database.

CREATE TABLE scenario (
    scenario_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL
);

-- Scenario 0 is the working plan. Whether other scenarios are branches of it or
-- peers alongside it is UNDECIDED — see docs/decisions.md. No parent_scenario_id
-- or committed flag is added until that is settled.
INSERT INTO scenario (scenario_id, name, created_at)
VALUES (0, 'working', '1970-01-01');

-- Closed vocabulary. A new measure is a schema change and a deliberate decision,
-- not a string someone types at a call site.
CREATE TABLE measure (
    measure      TEXT PRIMARY KEY,
    unit         TEXT NOT NULL,     -- 'qty' today; capacity measures are not in this table
    derived      INTEGER NOT NULL,  -- 0 = imported input, 1 = computed by a planning run
    description  TEXT NOT NULL
);

INSERT INTO measure (measure, unit, derived, description) VALUES
    ('demand_actual',         'qty', 0, 'Historical shipped or consumed quantity, from the system of record'),
    ('scheduled_receipt',     'qty', 0, 'Confirmed open PO or work order due in this bucket'),
    ('forecast',              'qty', 1, 'Statistical forecast of independent demand'),
    ('gross_req',             'qty', 1, 'Total requirement: independent demand plus dependent demand from BOM explosion'),
    ('on_hand_open',          'qty', 1, 'Projected on-hand at bucket start; negative means shortage'),
    ('net_req',               'qty', 1, 'Requirement remaining after netting on-hand and scheduled receipts'),
    ('planned_order_receipt', 'qty', 1, 'Lot-sized planned receipt, dated when the material is needed'),
    ('planned_order_release', 'qty', 1, 'planned_order_receipt offset backward by lead time');

CREATE TABLE fact_supply_demand (
    sku_id       INTEGER NOT NULL,
    loc_id       INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    measure      TEXT    NOT NULL REFERENCES measure (measure),
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    qty          NUMERIC NOT NULL,
    PRIMARY KEY (sku_id, loc_id, bucket_date, measure, scenario_id)
);

-- The planning grid reads one measure across many SKUs for a date window;
-- the primary key is SKU-leading and cannot serve that scan.
CREATE INDEX fact_supply_demand_by_bucket
    ON fact_supply_demand (scenario_id, measure, bucket_date);
