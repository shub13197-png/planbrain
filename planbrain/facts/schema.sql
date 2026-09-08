-- Planning fact schema. Portable SQL subset: runs on SQLite (tests) and
-- PostgreSQL (InvenTree production). Django migrations in the plugin app are
-- generated FROM this file; this file is the contract, not the deployment tool.
--
-- Three fact tables, three grains, one shared measure vocabulary:
--   fact_supply_demand  (sku_id, loc_id, bucket_date, measure, scenario_id)
--   fact_capacity       (resource_id,    bucket_date, measure, scenario_id)
--   fact_fleet          (truck_id,       bucket_date, measure, scenario_id)
--
-- Buckets are DAILY. bucket_date IS the bucket; there is no bucket table.
-- Storage is SPARSE: an absent row means zero, never unknown. Read only through
-- planbrain.facts.access.read_facts — see docs/contracts/facts.md.
--
-- sku_id, loc_id, resource_id and truck_id are logical references into
-- InvenTree. No foreign key — InvenTree core is read-only (architecture rule 1)
-- and may live in a separate database.

-- Scenarios are FLAT PEERS. Every scenario physically contains all of its rows;
-- nothing is inherited and no read ever walks source_scenario_id.
CREATE TABLE IF NOT EXISTS scenario (
    scenario_id         INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    created_at          TEXT NOT NULL,
    -- NULL means live. Non-NULL means the rows are a snapshot and must not change.
    frozen_at           TEXT,
    -- Provenance only. Displayed as "branched from working on 3 Mar".
    -- NEVER read to resolve a value.
    source_scenario_id  INTEGER REFERENCES scenario (scenario_id),
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'committed', 'archived')),
    -- Annual growth assumptions, as a percentage. Scalars, not time series, so
    -- they live here rather than in a fact table.
    --
    -- Two separate parameters on purpose. A single control moving both sides
    -- together reports a comfortable factory at every setting, which is the
    -- answer a planner is least likely to question and precisely the one rccp
    -- exists to prevent. See docs/forecast.md.
    --
    -- At or below -100% is not a rate, and the engines refuse it; the CHECK is
    -- here so the database cannot hold one either.
    demand_growth_pct   REAL NOT NULL DEFAULT 0.0
                        CHECK (demand_growth_pct > -100.0),
    capacity_growth_pct REAL NOT NULL DEFAULT 0.0
                        CHECK (capacity_growth_pct > -100.0)
);

-- At most one committed scenario. Unique across the rows where status is
-- 'committed', which is a partial unique index on both SQLite and PostgreSQL.
CREATE UNIQUE INDEX IF NOT EXISTS scenario_one_committed
    ON scenario (status) WHERE status = 'committed';

INSERT OR IGNORE INTO scenario (scenario_id, name, created_at, status)
VALUES (0, 'working', '1970-01-01', 'open');

-- Closed vocabulary shared by all three fact tables. A new measure is a
-- deliberate decision, not a string someone types at a call site.
CREATE TABLE IF NOT EXISTS measure (
    measure      TEXT PRIMARY KEY,
    grain        TEXT NOT NULL
                 CHECK (grain IN ('supply_demand', 'capacity', 'fleet')),
    unit         TEXT NOT NULL,     -- 'qty' | 'hours' | 'km' | 'trips'
    derived      INTEGER NOT NULL,  -- 0 = imported input, 1 = computed by a planning run
    description  TEXT NOT NULL
);

INSERT OR IGNORE INTO measure (measure, grain, unit, derived, description) VALUES
    ('demand_actual',         'supply_demand', 'qty',   0, 'Historical shipped or consumed quantity, from the system of record'),
    ('scheduled_receipt',     'supply_demand', 'qty',   0, 'Confirmed open PO or work order due in this bucket'),
    -- derived = 0 DELIBERATELY. A firm planned order is an INPUT authored by a
    -- human, in the same class as a confirmed PO, so `write_plans`'s existing
    -- refusal to write a non-derived measure already stops a planning run from
    -- overwriting it. No new rule was needed to protect it, which is the
    -- reason this classification was chosen over inventing one.
    ('firm_planned_order',    'supply_demand', 'qty',   0, 'Quantity fixed by a planner; a planning run nets around it and never resizes or reschedules it'),
    ('forecast',              'supply_demand', 'qty',   1, 'Statistical forecast of independent demand'),
    ('gross_req',             'supply_demand', 'qty',   1, 'Total requirement: independent demand plus dependent demand from BOM explosion'),
    -- A LEVEL, not a flow: it carries across buckets, so a slow mover stores
    -- densely where its demand stores sparsely. Balance at bucket END, after
    -- that bucket's requirements and receipts. Negative means shortage.
    ('projected_on_hand',     'supply_demand', 'qty',   1, 'Projected on-hand at bucket end; negative means shortage'),
    ('net_req',               'supply_demand', 'qty',   1, 'Requirement remaining after netting on-hand and scheduled receipts'),
    ('planned_order_receipt', 'supply_demand', 'qty',   1, 'Lot-sized planned receipt, dated when the material is needed'),
    ('planned_order_release', 'supply_demand', 'qty',   1, 'planned_order_receipt offset backward by lead time'),
    -- Dated at the bucket the material is NEEDED, not at the release that
    -- covers it: the release is in bucket zero by definition -- that is what
    -- makes it past due -- and dating them all there would merge every overdue
    -- order into one number with no due date a planner could chase.
    --
    -- Added because infeasibility does not show up in projected_on_hand. netreq
    -- dates a planned receipt at the bucket it is needed, so the projection
    -- balances even when the order to cover it should have gone out weeks ago.
    -- A shortage screen reading only negative projections reported "nothing
    -- runs out" on a plan over 2,947 real stock codes that carried hundreds of
    -- overdue releases.
    ('past_due_release',     'supply_demand', 'qty',   1, 'Quantity whose release date falls before the horizon; the order is already overdue'),
    ('capacity_avail_hours',  'capacity',      'hours', 0, 'Available hours on a resource in this bucket'),
    ('capacity_load_hours',   'capacity',      'hours', 1, 'Hours of load placed on a resource by the plan'),
    -- Fleet measures, defined at build item 9 from what the fairness ledger
    -- actually needs. The grain was reserved with an EMPTY measure set from item
    -- 2 until then, so nothing could write to it before the decision was made.
    -- All three are FLOWS: what happened in this bucket, zero otherwise, stored
    -- sparsely. The cumulative year-to-date ledger is summed on read. A stored
    -- cumulative would make a fact row mean "running total" here and "quantity
    -- in this bucket" everywhere else, which this schema forbids permanently.
    ('long_haul_km',          'fleet',         'km',    1, 'Long-haul kilometres assigned to a truck in this bucket; the fairness ledger measure'),
    ('total_km',              'fleet',         'km',    1, 'All kilometres assigned to a truck in this bucket, long-haul or not'),
    ('trips_assigned',        'fleet',         'trips', 1, 'Trips assigned to a truck in this bucket; few long runs and many short ones are different working weeks');

CREATE TABLE IF NOT EXISTS fact_supply_demand (
    sku_id       INTEGER NOT NULL,
    loc_id       INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    measure      TEXT    NOT NULL REFERENCES measure (measure),
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    qty          NUMERIC NOT NULL,
    PRIMARY KEY (sku_id, loc_id, bucket_date, measure, scenario_id)
);

-- Who fixed a quantity, and why. The quantity itself is NOT here: it lives in
-- the `firm_planned_order` measure and only there, because two copies of a
-- number is two numbers. This table answers "who and why"; the fact table
-- answers "how much"; `tests/test_overrides.py` asserts the two agree about
-- which addresses exist.
--
-- `reason` is NOT NULL and must be non-empty. An override with no reason is
-- indistinguishable in three weeks from a typo, and the person who has to work
-- that out is usually the person who typed it.
CREATE TABLE IF NOT EXISTS plan_override (
    sku_id       INTEGER NOT NULL,
    loc_id       INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    author       TEXT    NOT NULL CHECK (length(trim(author)) > 0),
    reason       TEXT    NOT NULL CHECK (length(trim(reason)) > 0),
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (sku_id, loc_id, bucket_date, scenario_id)
);

CREATE TABLE IF NOT EXISTS fact_capacity (
    resource_id  INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    measure      TEXT    NOT NULL REFERENCES measure (measure),
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    qty          NUMERIC NOT NULL,
    PRIMARY KEY (resource_id, bucket_date, measure, scenario_id)
);

-- The fairness ledger's grain. Reserved with an empty measure set from item 2
-- until item 9 defined what the ledger actually measures.
CREATE TABLE IF NOT EXISTS fact_fleet (
    truck_id     INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    measure      TEXT    NOT NULL REFERENCES measure (measure),
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    qty          NUMERIC NOT NULL,
    PRIMARY KEY (truck_id, bucket_date, measure, scenario_id)
);

-- --------------------------------------------------------------------------
-- Master data: what the facts are ABOUT.
--
-- **These tables did not exist, and their absence was the product's largest
-- open defect.** Facts were persisted from the first release; lead times, lot
-- sizes, the BOM, locations, resources and the working calendar were not. They
-- lived on a per-process object set by exactly one method, so a planner could
-- import a year of history, close the window, reopen it, and be told there was
-- no dataset loaded while their demand rows sat in the file.
--
-- Deliberately NOT in the fact tables. A fact is a quantity at a
-- (sku, loc, bucket, measure, scenario); a lead time is a property of a part
-- and has no bucket. Putting it in a fact table would have forced a date on
-- something that does not have one.
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS location (
    loc_id  INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    kind    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS part (
    sku_id         INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL,
    level          TEXT    NOT NULL,
    lead_time_days INTEGER NOT NULL,
    safety_stock   NUMERIC NOT NULL,
    lot_policy     TEXT    NOT NULL,
    lot_qty        NUMERIC NOT NULL,
    unit_cost      NUMERIC NOT NULL
);

CREATE TABLE IF NOT EXISTS bom (
    parent_sku_id INTEGER NOT NULL REFERENCES part (sku_id),
    child_sku_id  INTEGER NOT NULL REFERENCES part (sku_id),
    qty_per       NUMERIC NOT NULL,
    PRIMARY KEY (parent_sku_id, child_sku_id)
);

CREATE TABLE IF NOT EXISTS resource (
    resource_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routing (
    sku_id         INTEGER NOT NULL REFERENCES part (sku_id),
    resource_id    INTEGER NOT NULL REFERENCES resource (resource_id),
    hours_per_unit NUMERIC NOT NULL,
    setup_hours    NUMERIC NOT NULL,
    PRIMARY KEY (sku_id, resource_id)
);

CREATE TABLE IF NOT EXISTS truck (
    truck_id    INTEGER PRIMARY KEY,
    plate       TEXT    NOT NULL,
    capacity_kg NUMERIC NOT NULL,
    available   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_on_hand (
    sku_id INTEGER NOT NULL REFERENCES part (sku_id),
    loc_id INTEGER NOT NULL REFERENCES location (loc_id),
    qty    NUMERIC NOT NULL,
    PRIMARY KEY (sku_id, loc_id)
);

-- One row, or none. The dates the plan runs between and the week the business
-- works, which every seasonal period is derived from.
CREATE TABLE IF NOT EXISTS dataset (
    only_row         INTEGER PRIMARY KEY CHECK (only_row = 1),
    history_start    TEXT NOT NULL,
    history_end      TEXT NOT NULL,
    horizon_start    TEXT NOT NULL,
    horizon_end      TEXT NOT NULL,
    working_weekdays TEXT NOT NULL
);

-- The planning grid reads one measure across many entities for a date window;
-- the primary keys are entity-leading and cannot serve that scan.
CREATE INDEX IF NOT EXISTS fact_supply_demand_by_bucket
    ON fact_supply_demand (scenario_id, measure, bucket_date);
CREATE INDEX IF NOT EXISTS fact_capacity_by_bucket
    ON fact_capacity (scenario_id, measure, bucket_date);
CREATE INDEX IF NOT EXISTS fact_fleet_by_bucket
    ON fact_fleet (scenario_id, measure, bucket_date);
