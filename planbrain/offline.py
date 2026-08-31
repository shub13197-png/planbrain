"""Block outbound network access in-process, so a leak fails loudly.

The packaged application must run fully offline: no data leaves the machine,
ever. That is an **audited property**, not a claim, and this module is one of
the three things that make it auditable.

* `docs/offline.md` records the dependency audit — what each runtime dependency
  can reach and under what conditions.
* `engage()` here makes any outbound connection raise at the point of attempt.
* CI runs the whole pipeline under `--network=none`, which is the falsifiable
  version: it either passes with no interface at all, or it does not.

**Why block in-process as well as at the container boundary.** A container
without a network is the strong guarantee, and it only holds where someone
remembered to configure it. The packaged app runs on a customer's laptop with a
working network card. If a dependency calls out there, the call succeeds and
nothing anywhere reports it. Blocking in-process means the failure happens on
the customer's machine too, at the line that tried, with a traceback naming it.

**What this blocks and does not block.** It replaces `socket.socket` and
`socket.create_connection`, which is how every pure-Python client reaches the
network. It does **not** stop a C extension that opens a file descriptor itself,
nor a subprocess. Those are real gaps and are named in `docs/offline.md` rather
than papered over; the container-level check is what covers them.

AF_UNIX and AF_LOCAL are left alone. A local socket does not leave the machine
and blocking it would break unrelated things on some platforms.
"""

import socket

#: Address families that cannot leave the machine.
LOCAL_FAMILIES = {
    getattr(socket, name)
    for name in ("AF_UNIX", "AF_LOCAL", "AF_PIPE")
    if hasattr(socket, name)
}


class NetworkAccessDenied(RuntimeError):
    """Something tried to open a network connection in an offline build.

    This is not a failure to handle gracefully. It means a dependency reached
    for the network in a product that promises it never will, and the promise is
    worth more than whatever the call was for.
    """


_original_socket = None
_original_create_connection = None
_engaged = False


def engage() -> None:
    """Make outbound network access raise. Idempotent.

    Call once at application startup, before anything else runs. Fails loudly by
    design: a silent degradation would let the guarantee rot unnoticed, which is
    the failure mode this whole project keeps finding.
    """
    global _original_socket, _original_create_connection, _engaged
    if _engaged:
        return

    _original_socket = socket.socket
    _original_create_connection = socket.create_connection

    class _BlockedSocket(_original_socket):
        def __init__(self, family=socket.AF_INET, *args, **kwargs):
            if family not in LOCAL_FAMILIES:
                raise NetworkAccessDenied(
                    f"outbound network access is disabled in this build "
                    f"(socket family {family!r}). If you are seeing this, a "
                    f"dependency tried to reach the network -- please report it, "
                    f"including this traceback."
                )
            super().__init__(family, *args, **kwargs)

    def _blocked_create_connection(address, *args, **kwargs):
        raise NetworkAccessDenied(
            f"outbound network access is disabled in this build "
            f"(attempted connection to {address!r})."
        )

    socket.socket = _BlockedSocket
    socket.create_connection = _blocked_create_connection
    _engaged = True


def release() -> None:
    """Restore normal socket behaviour. For tests only.

    The application never calls this. It exists so a test can prove the guard
    both blocks and can be lifted -- a guard that cannot be turned off is
    difficult to test for false positives.
    """
    global _engaged
    if not _engaged:
        return
    socket.socket = _original_socket
    socket.create_connection = _original_create_connection
    _engaged = False


def is_engaged() -> bool:
    return _engaged
