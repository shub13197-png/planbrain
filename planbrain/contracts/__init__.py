"""Payload contracts for the Django <-> solver boundary.

Solvers run behind a queue (CLAUDE.md architecture rule 3), so a malformed
payload surfaces as a failed job minutes later rather than as a stack trace at
the call site. Validating on both sides of the boundary is what keeps that
debuggable.

The one constraint JSON Schema cannot express is the important one: every dense
series must be exactly ``horizon.bucket_count`` long. A series one element short
does not fail -- it shifts the whole plan by a bucket and every number it
produces stays plausible. ``validate_payload`` checks it explicitly.
"""

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).with_name("solver_io.schema.json")

DENSE_SERIES_REF = "#/$defs/dense_series"


class ContractError(ValueError):
    """A payload does not satisfy the solver IO contract."""


@lru_cache(maxsize=1)
def schema() -> dict:
    """The parsed contract document. Cached; it is read-only."""
    return json.loads(SCHEMA_PATH.read_text())


def payload_kinds() -> list[str]:
    """Every payload type the contract defines, e.g. ``netreq_input``."""
    return sorted(k for k in schema()["$defs"] if k.endswith(("_input", "_output")))


def validate_payload(kind: str, payload: dict) -> None:
    """Validate ``payload`` as ``kind``. Raises ContractError on any violation.

    Two passes: JSON Schema draft 2020-12 for structure, then the cross-field
    series-length rule that JSON Schema has no way to state.
    """
    doc = schema()
    if kind not in doc["$defs"]:
        raise ContractError(f"unknown payload kind {kind!r}; expected one of {payload_kinds()}")

    validator = jsonschema.Draft202012Validator(
        {**doc, "$ref": f"#/$defs/{kind}"},
    )
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise ContractError(f"{kind} invalid at {where}: {first.message}")

    bucket_count = payload.get("horizon", {}).get("bucket_count")
    if bucket_count is not None:
        problems = _series_length_problems(doc["$defs"][kind], payload, doc, bucket_count, "")
        if problems:
            raise ContractError(
                f"{kind}: dense series must be exactly {bucket_count} buckets long. "
                + "; ".join(problems)
            )


def _series_length_problems(node, data, doc, bucket_count, path) -> list[str]:
    """Walk the schema alongside the payload, checking every dense_series length."""
    if not isinstance(node, dict):
        return []

    ref = node.get("$ref")
    if ref == DENSE_SERIES_REF:
        if isinstance(data, list) and len(data) != bucket_count:
            return [f"{path or '<root>'} has {len(data)}"]
        return []
    if ref:
        return _series_length_problems(_resolve(doc, ref), data, doc, bucket_count, path)

    problems = []
    if isinstance(data, dict):
        for key, sub in node.get("properties", {}).items():
            if key in data:
                problems += _series_length_problems(
                    sub, data[key], doc, bucket_count, f"{path}.{key}" if path else key
                )
    elif isinstance(data, list) and "items" in node:
        for i, item in enumerate(data):
            problems += _series_length_problems(
                node["items"], item, doc, bucket_count, f"{path}[{i}]"
            )
    return problems


def _resolve(doc: dict, ref: str):
    """Resolve a local JSON pointer. The contract has no external refs by design."""
    if not ref.startswith("#/"):
        raise ContractError(f"non-local $ref {ref!r}; the contract must stay self-contained")
    node = doc
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node
