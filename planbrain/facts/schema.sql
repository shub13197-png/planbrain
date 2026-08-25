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
CREATE TABLE scenario (
    scenario_id         INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    created_at          TEXT NOT NULL,
    -- NULL means live. Non-NULL means the rows are a snapshot and must not change.
    frozen_at           TEXT,
    -- Provenance only. Displayed as "branched from working on 3 Mar".
    -- NEVER read to resolve a value.
    source_scenario_id  INTEGER REFERENCES scenario (scenario_id),
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'committed', 'archived'))
);

-- At most one committed scenario. Unique across the rows where status is
-- 'committed', which is a partial unique index on both SQLite and PostgreSQL.
CREATE UNIQUE INDEX scenario_one_committed
    ON scenario (status) WHERE status = 'committed';

INSERT INTO scenario (scenario_id, name, created_at, status)
VALUES (0, 'working', '1970-01-01', 'open');

-- Closed vocabulary shared by all three fact tables. A new measure is a
-- deliberate decision, not a string someone types at a call site.
CREATE TABLE measure (
    measure      TEXT PRIMARY KEY,
    grain        TEXT NOT NULL
                 CHECK (grain IN ('supply_demand', 'capacity', 'fleet')),
    unit         TEXT NOT NULL,     -- 'qty' | 'hours'
    derived      INTEGER NOT NULL,  -- 0 = imported input, 1 = computed by a planning run
    description  TEXT NOT NULL
);

INSERT INTO measure (measure, grain, unit, derived, description) VALUES
    ('demand_actual',         'supply_demand', 'qty',   0, 'Historical shipped or consumed quantity, from the system of record'),
    ('scheduled_receipt',     'supply_demand', 'qty',   0, 'Confirmed open PO or work order due in this bucket'),
    ('forecast',              'supply_demand', 'qty',   1, 'Statistical forecast of independent demand'),
    ('gross_req',             'supply_demand', 'qty',   1, 'Total requirement: independent demand plus dependent demand from BOM explosion'),
    -- A LEVEL, not a flow: it carries across buckets, so a slow mover stores
    -- densely where its demand stores sparsely. Balance at bucket END, after
    -- that bucket's requirements and receipts. Negative means shortage.
    ('projected_on_hand',     'supply_demand', 'qty',   1, 'Projected on-hand at bucket end; negative means shortage'),
    ('net_req',               'supply_demand', 'qty',   1, 'Requirement remaining after netting on-hand and scheduled receipts'),
    ('planned_order_receipt', 'supply_demand', 'qty',   1, 'Lot-sized planned receipt, dated when the material is needed'),
    ('planned_order_release', 'supply_demand', 'qty',   1, 'planned_order_receipt offset backward by lead time'),
    ('capacity_avail_hours',  'capacity',      'hours', 0, 'Available hours on a resource in this bucket'),
    ('capacity_load_hours',   'capacity',      'hours', 1, 'Hours of load placed on a resource by the plan');
-- No measure of grain 'fleet' yet, deliberately. haulplan (build item 6) defines
-- what the fairness ledger actually measures; a guessed vocabulary would invite
-- something to start writing to it before that decision is made. read_facts and
-- write_facts report fact_fleet as reserved until a measure is added here.

CREATE TABLE fact_supply_demand (
    sku_id       INTEGER NOT NULL,
    loc_id       INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    measure      TEXT    NOT NULL REFERENCES measure (measure),
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    qty          NUMERIC NOT NULL,
    PRIMARY KEY (sku_id, loc_id, bucket_date, measure, scenario_id)
);

CREATE TABLE fact_capacity (
    resource_id  INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    measure      TEXT    NOT NULL REFERENCES measure (measure),
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    qty          NUMERIC NOT NULL,
    PRIMARY KEY (resource_id, bucket_date, measure, scenario_id)
);

-- Reserved for haulplan (build item 6). Empty until the fairness ledger lands.
CREATE TABLE fact_fleet (
    truck_id     INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    measure      TEXT    NOT NULL REFERENCES measure (measure),
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    qty          NUMERIC NOT NULL,
    PRIMARY KEY (truck_id, bucket_date, measure, scenario_id)
);

-- The planning grid reads one measure across many entities for a date window;
-- the primary keys are entity-leading and cannot serve that scan.
CREATE INDEX fact_supply_demand_by_bucket
    ON fact_supply_demand (scenario_id, measure, bucket_date);
CREATE INDEX fact_capacity_by_bucket
    ON fact_capacity (scenario_id, measure, bucket_date);
CREATE INDEX fact_fleet_by_bucket
    ON fact_fleet (scenario_id, measure, bucket_date);
