"""The packaged backend entry point. JSON-RPC over stdin/stdout.

**The first statement that runs is `engage()`.** Before argparse, before the
planning imports, before anything. An import-time call-out is a real pattern and
a guard engaged later would never see it. `tests/test_packaging.py` reads this
file and asserts the ordering, because a comment saying "keep this first" is not
enforcement.

Protocol: one JSON object per line in, one per line out. No framing beyond the
newline, no ports, no sockets — see `docs/packaging.md` for why stdio rather
than localhost HTTP, which is a consequence of the offline guarantee rather than
a preference.

    {"id": 1, "method": "demo.build", "params": {"seed": 7}}
    {"id": 1, "ok": true, "result": {...}}
    {"id": 1, "ok": false, "error": {"type": "ValueError", "message": "..."}}

A method may also emit progress lines *before* its response:

    {"id": 2, "progress": true, "stage": "fitting demand models"}
    {"id": 2, "ok": true, "result": {...}}

They carry the request id and are distinguished by `progress`, never by `ok`,
so a client that ignores them still sees exactly one response per request. A
plan run takes the better part of a minute; without these the window is simply
frozen, and a frozen window is indistinguishable from a crashed one.

Every error is returned as a response rather than raised into the pipe. A
sidecar that dies on a bad request takes the whole application with it, and the
frontend cannot tell that from a crash.
"""

from planbrain.offline import engage

engage()  # noqa: E402 -- must precede every other import. Do not move.

import json  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402

from planbrain.backend.api import METHODS, session  # noqa: E402

PROTOCOL_VERSION = 1


def handle(request: dict, state) -> dict:
    """Dispatch one request. Never raises."""
    request_id = request.get("id")
    method = request.get("method")

    if method not in METHODS:
        return _error(request_id, "UnknownMethod",
                      f"no method {method!r}; known: {sorted(METHODS)}")
    try:
        result = METHODS[method](state, **(request.get("params") or {}))
        return {"id": request_id, "ok": True, "result": result}
    except TypeError as exc:
        # Almost always a bad params shape from the frontend, which is a caller
        # bug worth naming distinctly from an engine failure.
        return _error(request_id, "BadParams", str(exc))
    except Exception as exc:  # noqa: BLE001 -- deliberate; see module docstring
        return _error(request_id, type(exc).__name__, str(exc),
                      trace=traceback.format_exc())


def _error(request_id, kind, message, trace=None) -> dict:
    error = {"type": kind, "message": message}
    if trace:
        error["traceback"] = trace
    return {"id": request_id, "ok": False, "error": error}


def main(stdin=None, stdout=None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    state = session()

    # Announced before the first request so the shell can fail fast on a version
    # mismatch rather than on a confusing method error later.
    _write(stdout, {"ready": True, "protocol": PROTOCOL_VERSION,
                    "offline": True, "methods": sorted(METHODS)})

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(stdout, _error(None, "BadJSON", str(exc)))
            continue
        if request.get("method") == "shutdown":
            _write(stdout, {"id": request.get("id"), "ok": True, "result": None})
            break
        # Bound to this request, so a progress line can be attributed to the
        # call that produced it rather than to whatever is on screen.
        state.emit = lambda payload, _id=request.get("id"): _write(
            stdout, {"id": _id, "progress": True, **payload}
        )
        try:
            _write(stdout, handle(request, state))
        finally:
            state.emit = None
    return 0


def _write(stream, payload) -> None:
    """One JSON object per line, flushed.

    Flushing matters: the shell blocks on a response, and a buffered reply
    deadlocks the pair with no error on either side.
    """
    stream.write(json.dumps(payload, default=str) + "\n")
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
