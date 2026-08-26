"""The solver IO contract, and the one rule JSON Schema cannot state.

Payloads cross a queue boundary, so a malformed one fails minutes later in a
worker rather than at the call site. These tests are what make that boundary
trustworthy from both directions.
"""

import copy
import json
from pathlib import Path

import pytest

from planbrain.contracts import (
    ContractError,
    payload_kinds,
    schema,
    validate_payload,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "planbrain" / "contracts" / "examples"


def _example(kind):
    return json.loads((EXAMPLES / f"{kind}.json").read_text())


def test_the_contract_declares_payload_kinds_at_all():
    """Guards every parametrised test below.

    payload_kinds() feeds @parametrize. If it ever returned empty -- a renamed
    suffix, a failed load -- pytest would collect zero cases and the whole
    contract suite would report green while testing nothing.
    """
    kinds = payload_kinds()
    assert len(kinds) >= 6, f"only {len(kinds)} payload kinds found: {kinds}"
    assert "netreq_input" in kinds


def test_every_payload_kind_has_a_worked_example():
    """A schema says what is legal; an example says what a real payload looks like."""
    missing = [k for k in payload_kinds() if not (EXAMPLES / f"{k}.json").exists()]
    assert missing == []


@pytest.mark.parametrize("kind", payload_kinds())
def test_example_validates_against_its_schema(kind):
    validate_payload(kind, _example(kind))


@pytest.mark.parametrize("kind", payload_kinds())
def test_unknown_field_is_rejected(kind):
    """additionalProperties is false throughout: a typo'd key must not be ignored."""
    payload = _example(kind)
    payload["surprise"] = 1
    with pytest.raises(ContractError):
        validate_payload(kind, payload)


def test_short_dense_series_is_rejected():
    """The worst bug this boundary can carry: it shifts the plan by a bucket."""
    payload = _example("netreq_input")
    payload["items"][0]["gross_req"] = payload["items"][0]["gross_req"][:-1]
    with pytest.raises(ContractError, match="exactly 5 buckets"):
        validate_payload("netreq_input", payload)


def test_long_dense_series_is_rejected():
    payload = _example("netreq_output")
    payload["items"][0]["net_req"].append(0.0)
    with pytest.raises(ContractError, match="exactly 5 buckets"):
        validate_payload("netreq_output", payload)


def test_series_length_is_checked_below_a_nested_array():
    """rccp nests series two levels deep; the walker must reach them."""
    payload = _example("rccp_input")
    payload["planned_order_release"][0]["series"] = [1.0]
    with pytest.raises(ContractError, match="planned_order_release"):
        validate_payload("rccp_input", payload)


def test_rccp_loads_releases_not_receipts():
    """Work happens between release and receipt.

    Loading at the receipt bucket would report a plant that looks free exactly
    when it is busiest, which is a wrong number that reads as good news.
    """
    payload = _example("rccp_input")
    assert "planned_order_release" in payload
    assert "planned_order_receipt" not in payload


def test_horizon_must_be_daily():
    payload = _example("netreq_input")
    payload["horizon"]["bucket"] = "week"
    with pytest.raises(ContractError):
        validate_payload("netreq_input", payload)


def test_best_so_far_is_representable():
    """Architecture rule 3: a timed-out solve returns a usable answer, not an error."""
    payload = _example("haulplan_output")
    assert payload["meta"]["status"] == "timeout"
    validate_payload("haulplan_output", payload)


def test_unassigned_trip_is_representable():
    """Under a time limit some trips may go unassigned. That must be sayable."""
    payload = _example("haulplan_output")
    payload["assignments"][0]["truck_id"] = None
    validate_payload("haulplan_output", payload)


def test_fairness_ledger_is_required_input():
    """Drop the ledger and fairness silently restarts from zero every run."""
    payload = _example("haulplan_input")
    del payload["trucks"][0]["ytd_long_haul_km"]
    with pytest.raises(ContractError):
        validate_payload("haulplan_input", payload)


def test_unknown_kind_is_rejected():
    with pytest.raises(ContractError, match="unknown payload kind"):
        validate_payload("netreq", {})


def test_contract_is_self_contained():
    """No external $ref: the contract must be readable without network access."""
    refs = []

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                refs.append(node["$ref"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema())
    assert [r for r in refs if not r.startswith("#/")] == []


def test_every_ref_resolves():
    doc = schema()
    defs = doc["$defs"]

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref:
                assert ref.removeprefix("#/$defs/") in defs, f"dangling {ref}"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)


def test_examples_on_disk_match_their_generator():
    """A schema change nobody reflected in the examples should fail the build."""
    from tools.make_examples import EXAMPLES as SOURCE, netreq_output

    expected = {**SOURCE, "netreq_output": netreq_output()}
    for kind, payload in expected.items():
        assert _example(kind) == payload, f"{kind}.json is stale; run tools.make_examples"


def test_netreq_example_is_produced_by_the_engine():
    """The output example is computed from the input example, not hand-written.

    An earlier hand-written version claimed a second order and a different
    balance series -- it was simply wrong, and it was the thing a reader would
    have copied. Deriving it makes drift impossible rather than merely detected.
    """
    from planbrain.netreq import Item, LotSizing, plan_item

    spec = _example("netreq_input")["items"][0]
    plan = plan_item(Item(
        sku_id=spec["sku_id"], loc_id=spec["loc_id"],
        lead_time_days=spec["lead_time_days"], on_hand=spec["on_hand"],
        safety_stock=spec["safety_stock"], lot_sizing=LotSizing(**spec["lot_sizing"]),
        gross_req=spec["gross_req"], scheduled_receipt=spec["scheduled_receipt"],
    ))
    item = _example("netreq_output")["items"][0]
    assert item["projected_on_hand"] == plan.projected_on_hand
    assert item["planned_order_receipt"] == plan.planned_order_receipt


def test_wagner_whitin_payload_requires_its_costs():
    """The schema's if/then: a cost-trade-off policy cannot run without costs."""
    payload = _example("netreq_input")
    payload["items"][0]["lot_sizing"] = {"policy": "wagner_whitin"}
    with pytest.raises(ContractError):
        validate_payload("netreq_input", payload)

    payload["items"][0]["lot_sizing"] = {
        "policy": "wagner_whitin", "setup_cost": 500.0, "holding_cost": 2.0,
    }
    validate_payload("netreq_input", payload)


def test_fixed_qty_payload_requires_a_quantity():
    payload = _example("netreq_input")
    payload["items"][0]["lot_sizing"] = {"policy": "fixed_qty"}
    with pytest.raises(ContractError):
        validate_payload("netreq_input", payload)
