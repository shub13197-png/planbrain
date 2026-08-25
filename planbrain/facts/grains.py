"""The fact table registry: one entry per grain.

Adding a grain means adding a table to schema.sql and a line here. Nothing else
in the codebase should hard-code a fact table name.
"""

FACT_TABLES = {
    "fact_supply_demand": ("sku_id", "loc_id"),
    "fact_capacity": ("resource_id",),
    "fact_fleet": ("truck_id",),
}

#: measure.grain value that each fact table accepts.
TABLE_GRAIN = {
    "fact_supply_demand": "supply_demand",
    "fact_capacity": "capacity",
    "fact_fleet": "fleet",
}
