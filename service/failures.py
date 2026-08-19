"""Turning a worker crash into a sentence a person can act on (**B1**).

**What this is not.** It is not a security control. `GET /runs/{id}` and
`GET /runs/{id}/log` are both `VIEWER`, so moving a traceback from one to the other
restricts nothing, and claiming otherwise would be the kind of comfortable
half-truth this project's documentation exists to avoid. This is a **legibility**
fix: the run page's default view stops being a stack trace and becomes a sentence,
with the full detail one deliberate click away in the log.

**What stays exactly as it is.** Some exceptions carry a message already written for
a human: `ReconHardStop` ("Store-count check FAILED for shopee/orders. Missing
stores: [...]", and `docs/09-OPERATIONS.md` is written against those strings) and
`MaterializationError`, which promises in its own docstring to name the file or the
store. Those pass through untouched — replacing them with a generic sentence discards
the most actionable text in the system. Only *infrastructure* failures are
translated, because those are the ones whose text is a Python type name and a repr.

The distinction the schema already draws is the one this follows: `run()` catches
data problems itself and returns `HARD_STOP`. Reaching the handler below means the
**worker** broke — the database went away, the object store refused, a disk filled —
and none of those have anything useful to say to the person who clicked Run.
"""

from __future__ import annotations

# Both tables below match on the exception's class NAME rather than on the class, so
# this module imports nothing: it must be safe to call from the failure path of a
# worker whose imports may be exactly what went wrong.

# Exceptions whose OWN message is already the human message. These are written
# deliberately, for this reader, by code in this repository — `ReconHardStop` names
# the store and the config key to fix, `MaterializationError` promises in its own
# docstring to "always name the file or the store". Replacing either with a generic
# sentence throws away the most useful text in the system, which is what the first
# version of this module did until three materialize tests caught it.
_OWN_MESSAGE = frozenset({"ReconHardStop", "MaterializationError"})

_BY_TYPE: dict[str, str] = {
    "OperationalError":
        "The database became unreachable while this run was in progress. The run "
        "did not finish; nothing was written to the finance file.",
    "InterfaceError":
        "The connection to the database was lost while this run was in progress. "
        "The run did not finish.",
    "ObjectNotFound":
        "A file this window needs is recorded but is not in storage. The run "
        "stopped rather than proceeding without it.",
    "PermissionError":
        "The service was refused access to a file or folder it needs. This is a "
        "deployment problem, not a problem with your data.",
    "FileNotFoundError":
        "A file or folder the service expects was not there. This is a deployment "
        "problem, not a problem with your data.",
    "OSError":
        "The service could not read or write a file it needs — most often no disk "
        "space. Nothing was invoiced.",
    "MemoryError":
        "The service ran out of memory processing this window. Nothing was "
        "invoiced. A very large window may need to be run on its own.",
    "TimeoutError":
        "Something this run depends on stopped responding. The run did not finish.",
}

_FALLBACK = (
    "This run failed for a reason that is not a problem with your data — the "
    "service itself hit an error. Nothing was invoiced and nothing was changed. "
    "The full detail is in the run log below, and it needs someone technical."
)


def humanise(exc: BaseException) -> str:
    """One sentence for the run record. Never a traceback, never a repr.

    Falls back deliberately rather than interpolating `str(exc)`: an unrecognised
    exception's message is by definition text nobody wrote for this audience, and
    half of them are a file path or a connection string.
    """
    for cls in type(exc).__mro__:
        if cls.__name__ in _OWN_MESSAGE:
            return str(exc) or cls.__name__
        if cls.__name__ in _BY_TYPE:
            return _BY_TYPE[cls.__name__]
    return _FALLBACK


def technical(exc: BaseException) -> str:
    """The line that goes in the LOG, where detail belongs. Type and message only.

    The traceback is written separately by the caller — keeping it out of here
    means this is safe to put in a log line without deciding how long a log line
    may be.
    """
    return f"{type(exc).__name__}: {exc}"
