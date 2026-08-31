"""Assert a packaged sidecar run was healthy and had the offline guard engaged.

    python packaging/check_offline_run.py out.jsonl [--no-demo]

Reads the JSONL a sidecar wrote. Checking the exit code is not enough: the
process exits 0 whether or not the guard engaged, and whether or not every
request failed.
"""

import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    paths = [a for a in argv if not a.startswith("--")]
    expect_demo = "--no-demo" not in argv
    if not paths:
        print("usage: check_offline_run.py <output.jsonl> [--no-demo]", file=sys.stderr)
        return 2

    lines = [l for l in Path(paths[0]).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        # A silent empty file is the failure this project keeps finding: it
        # would otherwise satisfy every check below vacuously.
        print("the sidecar produced no output at all", file=sys.stderr)
        return 1

    messages = [json.loads(l) for l in lines]
    handshake = messages[0]
    problems = []

    if not handshake.get("ready"):
        problems.append("no handshake; the sidecar did not start cleanly")
    if handshake.get("offline") is not True:
        problems.append(
            "the handshake does not report offline=True -- engage() did not run, "
            "or did not run first"
        )

    responses = [m for m in messages if "ok" in m]
    if not responses:
        problems.append("no request was answered")
    for message in responses:
        if not message["ok"]:
            problems.append(f"request {message.get('id')} failed: {message.get('error')}")

    if expect_demo:
        built = [m for m in responses if isinstance(m.get("result"), dict)
                 and "parts" in m["result"]]
        if not built:
            problems.append("demo.build did not return a dataset")
        elif built[0]["result"]["parts"] <= 0:
            problems.append("demo.build returned an empty dataset")

    if problems:
        print("PACKAGED RUN FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"packaged run OK: {len(responses)} request(s), guard engaged, no network")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
