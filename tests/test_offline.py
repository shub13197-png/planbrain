"""The offline guarantee, as a test rather than a claim.

The packaged app must run fully offline: no data leaves the machine, ever. Three
things make that auditable, and this file covers two of them.

* `docs/offline.md` — the dependency audit, by hand and by scan.
* `planbrain.offline.engage()` — in-process socket block, tested here.
* CI under `--network=none` — the falsifiable version, in `.github/workflows`.

The container check is the strong one: with no interface present, a leak cannot
succeed regardless of what the code tries. The in-process guard covers the case
the container cannot — a customer's laptop, where the network *does* work and a
stray call would succeed silently.
"""

import socket

import pytest

from planbrain.offline import (
    LOCAL_FAMILIES,
    NetworkAccessDenied,
    engage,
    is_engaged,
    release,
)


@pytest.fixture
def blocked():
    engage()
    yield
    release()


# --------------------------------------------------------------------------
# the guard blocks, and can be shown to block
# --------------------------------------------------------------------------

def test_opening_an_inet_socket_raises(blocked):
    with pytest.raises(NetworkAccessDenied):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_ipv6_is_blocked_too(blocked):
    """A guard that only covers IPv4 is a guard someone routes around by accident."""
    with pytest.raises(NetworkAccessDenied):
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM)


def test_create_connection_raises_and_names_the_address(blocked):
    """The traceback has to say where it was going, or a report is useless."""
    with pytest.raises(NetworkAccessDenied, match="example.com"):
        socket.create_connection(("example.com", 443), timeout=1)


def test_a_udp_socket_is_blocked(blocked):
    """DNS goes over UDP. Blocking TCP alone would let a lookup leak the query."""
    with pytest.raises(NetworkAccessDenied):
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def test_the_error_tells_the_user_what_to_do(blocked):
    with pytest.raises(NetworkAccessDenied) as exc:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    assert "report it" in str(exc.value)


# --------------------------------------------------------------------------
# and does not block what it should not
# --------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="no AF_UNIX here")
def test_local_sockets_still_work(blocked):
    """A unix socket cannot leave the machine, and blocking it breaks unrelated
    things on some platforms."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.close()


def test_local_families_are_not_empty_on_this_platform():
    """If this set were empty the exemption would be silently doing nothing --
    the empty-result-reads-as-success failure again."""
    assert LOCAL_FAMILIES or not hasattr(socket, "AF_UNIX")


# --------------------------------------------------------------------------
# the guard's own lifecycle
# --------------------------------------------------------------------------

def test_engage_is_idempotent():
    """Called twice, it must not capture its own blocked socket as the original
    and leave the process permanently offline after release()."""
    try:
        engage()
        engage()
        release()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.close()
    finally:
        release()


def test_release_restores_normal_behaviour():
    """A guard that cannot be lifted cannot be tested for false positives."""
    engage()
    assert is_engaged()
    release()
    assert not is_engaged()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()


def test_release_without_engage_is_harmless():
    release()
    assert not is_engaged()


# --------------------------------------------------------------------------
# the audited property: the pipeline runs with the network gone
# --------------------------------------------------------------------------

def test_the_whole_pipeline_runs_with_sockets_blocked(demo_offline):
    """The claim, as a test. If a dependency ever starts calling out, this fails.

    Deliberately exercises statsforecast (model fitting) and jsonschema (payload
    validation), the two runtime dependencies whose closure contains
    network-capable code -- see docs/offline.md.
    """
    assert demo_offline["forecast_rows"] > 0
    assert demo_offline["plan_measures"]
    assert demo_offline["capacity_buckets"] > 0


def test_a_remote_schema_reference_does_not_fetch(blocked):
    """jsonschema's closure contains `requests`, via the deprecated RefResolver.

    Our validator is `Draft202012Validator` with a self-contained schema, and
    modern jsonschema refuses an unresolvable remote reference rather than
    fetching it. Both facts are asserted here, because the second is a library
    default that a future version could change.
    """
    import jsonschema

    validator = jsonschema.Draft202012Validator(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "$ref": "https://example.invalid/schema.json"}
    )
    with pytest.raises(Exception) as exc:
        validator.validate({"a": 1})
    # Either outcome is acceptable; a successful fetch is not.
    assert not isinstance(exc.value, socket.timeout)
    assert "Unresolvable" in str(exc.value) or isinstance(exc.value, NetworkAccessDenied)


def test_our_own_contract_has_no_remote_references():
    """Belt and braces with the library default above: even if a future
    jsonschema resolved remote refs again, ours are all local."""
    from planbrain.contracts import schema

    refs = []

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                refs.append(node["$ref"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema())
    assert refs, "if this is empty the check is vacuous"
    assert [r for r in refs if not r.startswith("#/")] == []
