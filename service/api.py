"""The HTTP API: who you are, what ran, what it flagged, and what rules it used.

    python -m service.api            # uvicorn on RECON_API_HOST:RECON_API_PORT

**Every endpoint is authorized.** M4 shipped this unauthenticated and recorded it
as defect 2.1; M5 closed it with bearer tokens and M6 replaced those with username
and password sessions (`service/auth.py`), shaped so Entra ID SSO substitutes for
the session lookup rather than replacing the model. Three roles, least to most:
`recon.viewer` reads, `recon.user` runs work and uploads exports, `recon.admin`
changes the rules and manages accounts.

Two rules that are easy to erode and worth stating:

* **`requested_by` comes from the session, never from the body.** Before M5 it was
  a free-text field the caller supplied, which made it a claim rather than
  evidence. For a system that produces invoices, "who asked for this run" has to
  be a fact — and since M6 the subject is a `users.username` row rather than a
  free-text subject an admin typed while minting a token, so it is more of a fact
  than it was.
* **Sync endpoints on purpose.** FastAPI runs `def` handlers in a threadpool, so
  a psycopg pool is all the concurrency this needs. The slowest thing in the
  system is a 171-second pandas run in a different process.

`create_app(...)` takes its dependencies rather than reading the environment, so
a test can point it at a temporary database.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from src.pipeline import EXIT_CODES, RunStatus

from . import (artifacts, auth, config_rows, config_store, materialize, naming,
               objects as object_lib, passwords, ratelimit, references,
               uploads as upload_lib, verification)
from .artifacts import ArtifactStore
from .auth import (AuthPolicy, Forbidden, PasswordChangeRequired, Principal, Role,
                   Unauthenticated)
from .config import ServiceSettings
from .models import ALL_PLATFORMS, Job, JobKind, JobState, Run, payload
from .repository import ActiveJobExists, NotFound, Repository
from .repository_identity import DuplicateUser, LastAdminProtected
from .repository_m5 import ProposalConflict
from .repository_m5 import DuplicateUpload

# One constant, so the four call sites that reject a sign-in cannot drift into
# saying four subtly different things. See POST /sessions.
_BAD_CREDENTIALS = "invalid username or password"

PLATFORMS = ("tiktok", "shopee", "lazada")
KINDS = ("orders", "income", "weekly", "daily")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str | None:
    """The caller's address, or None if it is not one.

    `user_sessions.client_ip` is `inet`, and `request.client.host` is NOT reliably
    an IP: Starlette's TestClient reports the literal "testclient", and an ASGI
    server behind certain transports can report a socket path or a hostname.
    Passing any of those to Postgres raises InvalidTextRepresentation, which would
    turn every sign-in into a 500 — a login broken by a field that exists only for
    an audit trail. Recorded when parseable, dropped when not.
    """
    host = request.client.host if request.client else None
    if not host:
        return None
    import ipaddress
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _safe_period(v: str) -> str:
    # The period becomes a directory name on the worker. This is a character
    # guard against a path-shaped value, not an attempt to enumerate the naming
    # scheme (`2026-05_w1`, `_s2x`, `_l1`...).
    if not all(ch.isalnum() or ch in "-_" for ch in v):
        raise ValueError("period may contain only letters, digits, '-' and '_'")
    return v


def _safe_month(v: str) -> str:
    # Unlike a period, a month HAS a closed grammar — it is the prefix every
    # period shares (`2026-07_w1` → `2026-07`, service/uploads.month_of) and the
    # month-master job's own `period`. A malformed month would not error; it
    # would quietly queue a master that covers nothing, so it is refused here.
    if not (len(v) == 7 and v[:4].isdigit() and v[4] == "-"
            and v[5:].isdigit() and 1 <= int(v[5:]) <= 12):
        raise HTTPException(422, f"{v!r} is not a month; expected the form 2026-07")
    return v


class EnqueueRequest(BaseModel):
    """No `partial_roster`, since M6.

    It was a per-run checkbox that relaxed the store-count hard stop, and it had
    all three properties a control should not have: ticked by whoever was in a
    hurry, no reason recorded, and invisible to whoever reviewed the numbers
    afterwards. The hard stop itself is unchanged — the override is now a
    per-window declaration with a mandatory reason and a named author
    (`POST /windows/roster`). Developer tooling keeps the flag:
    `tools/make_golden.py --partial-roster` still generates a subset golden, and
    the manifest still labels it as a subset.
    """

    platform: Literal["tiktok", "shopee", "lazada"]
    period: str = Field(min_length=1, max_length=64)
    refs: dict | None = None
    priority: int = 0
    idempotency_key: str | None = Field(default=None, max_length=200)
    # Deliberately not free: raising it means "retry this settlement run
    # automatically if the worker dies", which is a decision about money.
    max_attempts: int = Field(default=1, ge=1, le=3)

    @field_validator("period")
    @classmethod
    def _check_period(cls, v: str) -> str:
        return _safe_period(v)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    # Bounded because an unbounded input to a deliberately-expensive hash is a
    # denial of service. service/passwords.MAX_LENGTH is the same number.
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class UserCreateRequest(BaseModel):
    """No password field, deliberately.

    The server generates it. An admin who picks someone's password knows it, and
    every audit column this service exists to make trustworthy — jobs.requested_by,
    config_proposals.proposed_by, .decided_by — is only evidence if impersonating a
    colleague is hard.
    """

    username: str = Field(min_length=3, max_length=200)
    role: Literal["recon.viewer", "recon.user", "recon.admin"]
    display_name: str | None = Field(default=None, max_length=200)


class RoleChangeRequest(BaseModel):
    role: Literal["recon.viewer", "recon.user", "recon.admin"]


class RejectRequest(BaseModel):
    # Long enough to be a sentence. "wrong" tells the next person nothing, and
    # this row is the only record of why a window does not contain a file
    # somebody remembers uploading.
    reason: str = Field(min_length=8, max_length=500)


class RosterDeclarationRequest(BaseModel):
    """State, once per window, that an incomplete roster is expected.

    Replaces the per-run `partial_roster` checkbox. `reason` is mandatory when
    `partial` is true and is enforced by the database as well
    (`windows_partial_needs_reason`), because the whole difference between this
    and a checkbox is that somebody had to write down why.
    """

    platform: Literal["tiktok", "shopee", "lazada"]
    period: str = Field(min_length=1, max_length=64)
    partial: bool
    reason: str | None = Field(default=None, max_length=500)
    # WHICH expected stores are declared absent (D3). None is the blanket —
    # every expected store optional — kept expressible for declarations made
    # before anyone knows which stores will be missing. Membership in the
    # roster is deliberately NOT checked here: the window may be pinned to an
    # older config, so `apply_partial_roster` validates against the roster the
    # run actually uses and hard-stops on a name it does not know.
    stores: list[str] | None = Field(default=None, max_length=200)

    @field_validator("period")
    @classmethod
    def _check_period(cls, v: str) -> str:
        return _safe_period(v)


class StorePreviewRequest(BaseModel):
    """Which store each of these filenames belongs to, before any bytes are sent.

    **Names only, deliberately.** The whole point is to ask the question while the
    files are still sitting in a folder-picker: a 184 MB export uploaded to the
    wrong storefront costs a reject, a re-upload and an explanation, and the
    operator's only recourse before this existed was to rename the file on disk
    (register D7). Sending the bytes to find out would make the preview cost the
    thing it exists to save.
    """

    platform: Literal["tiktok", "shopee", "lazada"]
    period: str = Field(min_length=1, max_length=64)
    kind: Literal["orders", "income", "weekly", "daily"]
    # `MAX_FILES` per window is the pipeline's own bound; a real window is 3-39
    # files and the batch upload form posts them together.
    filenames: list[str] = Field(min_length=1, max_length=200)

    @field_validator("period")
    @classmethod
    def _check_period(cls, v: str) -> str:
        return _safe_period(v)


class ReferencesRequest(BaseModel):
    """The team's own totals for a window, as named fields (A3).

    Deliberately not a free `refs` blob even though that is the shape the pipeline
    reads. The keys are a small closed set per platform defined in
    `service/references.py`, and `references.parse` refuses a name nothing compares
    against — a figure someone typed in believing it was checked, and which was
    silently ignored, is worse than no figure at all.

    `supplied_by` is NOT here: it comes from the session, like every other author
    field in this api.
    """

    values: dict = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=500)


class ProposalRequest(BaseModel):
    """Structured row edits, not a document.

    Accepting a whole YAML body would make this endpoint a way to replace the
    domain contract wholesale, and no diff review reliably catches a subtle change
    in a 400-line file. So: named operations on named rows of named tables, with a
    stated reason.

    **A list since M6, and that is not a convenience.** With one edit per proposal,
    adding a store to the roster and its alias in the same breath was two proposals,
    two approvals and two commits — so people would do it in one hand-edit instead
    and the audit trail would record nothing. A form that shows a section has to be
    able to submit a section.

    Since M8/1.6 an edit names a TABLE and a ROW rather than a dotted path into a
    file. `service/config_rows.py` says why; the short version is that a row can
    carry its own evidence and its own "this can move a cell" flag, and a comment
    above a line cannot.
    """

    edits: list[dict] = Field(min_length=1)
    summary: str = Field(min_length=8, max_length=500)


class DecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class PinRequest(BaseModel):
    platform: Literal["tiktok", "shopee", "lazada"]
    period: str = Field(min_length=1, max_length=64)
    config_version_id: int
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("period")
    @classmethod
    def _check_period(cls, v: str) -> str:
        return _safe_period(v)


class UnpinRequest(BaseModel):
    """A reason, and it is not optional.

    `PinRequest.reason` is nullable because an automatic pin by the worker carries
    its own generated string. Unpinning is only ever a person's deliberate act, and
    it is the one that needs explaining: afterwards the window's rules are whatever
    today's config says, so a re-run may not reproduce the invoice.
    """

    reason: str = Field(min_length=1, max_length=500)


class DispositionRequest(BaseModel):
    """A decision on a recurring exception (D1).

    `reason` has the roster declaration's floor, not `UnpinRequest`'s one
    character: the disposition badge IS the record the next reader gets, and a
    one-word "ok" defeats the purpose of writing anything down.
    """

    disposition: Literal["reviewed", "expected"]
    reason: str = Field(min_length=8, max_length=500)


class DispositionClearRequest(BaseModel):
    # Re-opening a previously "expected" exception is as consequential as the
    # mark was — same rule as unpinning: say why.
    reason: str = Field(min_length=8, max_length=500)


def _verify_artifact(art, actual: str) -> None:
    """The bytes about to be served ARE the bytes the run wrote.

    The mirror of `materialize.verify_digest`, in the opposite direction. M8/2.5
    closed the inbound half — an object the pipeline reads is checked against
    `object_sha256` before anything parses it — and left this one open: the worker
    records a `sha256` per artifact and nothing ever compared it, so a truncated or
    replaced workbook would reach a finance user looking authoritative. Same failure
    shape, same digest already stored, opposite direction of travel (defect 2.4).

    **`502`, not `500`.** The api did its job; the storage layer returned something
    other than what was recorded. And **no warning tier**: a differing digest has no
    benign cause, and the artifact in question is the file the team invoices from.

    A NULL or empty recorded digest is REFUSED rather than trusted. Recomputing it
    now would certify the store against itself and pass even if the bytes had already
    been replaced — the [D26](../docs/06-DECISIONS.md#d26) argument that
    `010_object_digest.sql` made for uploads, applied here. The consequence is
    deliberate and stated: artifacts from runs predating the digest column stop being
    downloadable, and a re-run regenerates them.
    """
    expected = (getattr(art, "bytes_sha256", None) or "").strip().lower()
    if not expected:
        raise HTTPException(
            502, f"{art.name!r} was recorded before artifact digests existed, so "
                 f"nothing can establish that the stored bytes are the bytes this run "
                 f"produced. Re-run the window to regenerate it. (Hashing the file "
                 f"now would only prove the store agrees with itself.)")
    if actual.lower() != expected:
        raise HTTPException(
            502, f"{art.name!r} does NOT match what this run wrote: recorded "
                 f"{expected[:12]}…, found {actual[:12]}… in the artifact store. The "
                 f"file is not what was produced, so it must not be invoiced from.")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _job_payload(repo: Repository, job: Job) -> dict:
    out = payload(job)
    run = repo.run_for_job(job.id)
    out["run"] = _run_payload(run) if run else None
    return out


def _run_payload(run: Run) -> dict:
    out = payload(run)
    out["in_flight"] = run.in_flight
    out["variances"] = run.variances
    out["unverified"] = run.unverified
    return out


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

def create_app(repo: Repository, store: ArtifactStore, *,
               settings: ServiceSettings | None = None,
               policy: AuthPolicy | None = None,
               title: str = "recon",
               throttle: ratelimit.Throttle | None = None) -> FastAPI:
    policy = policy or AuthPolicy(enabled=True)
    # `ApprovalPolicy` is gone (M6). It existed only because open question 13 — who
    # owns configuration and who signs off a rate change — was unanswered, so the
    # policy was made configurable rather than assumed. It is answered now
    # (docs/11-OPEN-QUESTIONS.md #13, closing defect 2.7): user and admin propose,
    # viewer cannot, only admin decides, and self-approval is allowed and RECORDED
    # via the generated `config_proposals.self_approved` column. Forbidding
    # self-approval would deadlock a single-admin deployment and push the edit back
    # to hand-editing settings.yaml, which has no audit trail at all.

    # Per app, never a module global: a module-level throttle would make the test
    # suite order-dependent, because one test's deliberate failed logins would
    # throttle another's. See service/ratelimit.py's header.
    idle_seconds = (settings.session_idle_minutes if settings else 60) * 60
    absolute_hours = settings.session_absolute_hours if settings else 12
    throttle = throttle or ratelimit.Throttle(
        limit=settings.login_attempts if settings else 10,
        window_s=300, cooloff_s=900)

    # Where uploaded exports go. A bucket when this deployment has one, a
    # directory when it does not — and never `ArtifactStore`, which is what the
    # upload path borrowed before M6 and is the root of defect 2.4.
    objects_prefix = object_lib.UPLOAD_PREFIX
    uploads_objects = (object_lib.upload_store(settings) if settings is not None
                       else object_lib.LocalDirObjects(Path(".uploads/objects")))

    app = FastAPI(title=title, version="0.2.0",
                  summary="Settlement reconciliation — runs, exceptions and rules")

    # -- errors -------------------------------------------------------------

    @app.exception_handler(NotFound)
    def _not_found(_r, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Unauthenticated)
    def _unauth(_r, exc: Unauthenticated) -> JSONResponse:
        # 401 and 403 are kept apart deliberately: "I don't know who you are" and
        # "I know, and no" are diagnosed completely differently.
        return JSONResponse({"detail": str(exc)}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})

    # Registered BEFORE Forbidden so the more specific class wins: FastAPI
    # dispatches on exact class first, then the MRO. The machine-readable `code`
    # is what lets the BFF redirect to the change-password page instead of
    # rendering a dead end.
    @app.exception_handler(PasswordChangeRequired)
    def _must_change(_r, exc: PasswordChangeRequired) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "password_change_required"},
                            status_code=403)

    @app.exception_handler(Forbidden)
    def _forbidden(_r, exc: Forbidden) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(DuplicateUser)
    def _dupe_user(_r, exc: DuplicateUser) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(LastAdminProtected)
    def _last_admin(_r, exc: LastAdminProtected) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(passwords.PasswordError)
    def _password_policy(_r, exc: passwords.PasswordError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(config_store.ConfigEditError)
    def _config_error(_r, exc: config_store.ConfigEditError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(upload_lib.UploadRejected)
    def _rejected(_r, exc: upload_lib.UploadRejected) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # -- authentication -----------------------------------------------------

    # Fail at construction, not per-request. A repository that cannot look a
    # session up would otherwise report EVERY credential as "session is not
    # valid" — a wiring mistake wearing a credential error's clothes, and
    # undiagnosable from the outside. The 401 message stays deliberately vague
    # about unknown vs revoked vs expired; a misconfigured server is a different
    # thing and should be loud.
    if policy.enabled and not hasattr(repo, "principal_for_session"):
        raise TypeError(
            f"{type(repo).__name__} cannot authenticate sessions — authentication "
            f"needs M5Repository (it owns the users and user_sessions tables). "
            f"Pass AuthPolicy(enabled=False) only for local development.")

    def current(request: Request) -> Principal:
        return auth.authenticate(
            policy, request.headers.get("authorization"),
            lambda digest: repo.principal_for_session(digest,
                                                      idle_seconds=idle_seconds))

    # NOTE: this shape is mandatory, not stylistic. The router-walking tests in
    # tests/service/test_auth.py read the required role out of
    # `dependency.__closure__`, filtering on `isinstance(c.cell_contents, Role)`.
    # `role` must stay a closed-over `Role`; the extra bool is skipped by that
    # filter. Rewriting this as `Annotated[...]` or as decorator-level
    # `dependencies=[...]` makes those tests pass vacuously.
    def requires(role: Role, *, when_password_change_pending: bool = False):
        def dependency(principal: Principal = Depends(current)) -> Principal:
            return auth.require(
                principal, role,
                allow_password_change_pending=when_password_change_pending)
        return dependency

    viewer = Depends(requires(Role.VIEWER))
    user = Depends(requires(Role.USER))
    admin = Depends(requires(Role.ADMIN))
    # The only routes reachable while a temp password is still outstanding.
    viewer_pending = Depends(requires(Role.VIEWER, when_password_change_pending=True))

    # -- health and identity ------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict:
        """Unauthenticated on purpose — a load balancer has no token, and this
        reveals only whether the database answers."""
        try:
            return {"status": "ok", "auth": "enabled" if policy.enabled else "DISABLED",
                    **repo.healthcheck()}
        except Exception as exc:                                     # noqa: BLE001
            raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc

    @app.get("/metrics")
    def metrics(principal: Principal = viewer) -> dict:
        """Service telemetry (C5): queue depth by state, run outcomes over 24h,
        worker liveness, oldest queued age. Counts and ages only — never a store
        name, never a figure. Viewer role rather than open: unlike /healthz this
        reveals operational tempo, and every route here names a role.
        """
        return repo.metrics()

    @app.get("/meta")
    def meta() -> dict:
        return {"platforms": list(PLATFORMS), "kinds": list(KINDS),
                "exit_codes": {s.value: c for s, c in EXIT_CODES.items()},
                "run_statuses": [s.value for s in RunStatus],
                "job_states": [s.value for s in JobState],
                "roles": [r.value for r in Role],
                # Fixed since M6, not a deployment setting. See create_app.
                "config_approval": "user-or-admin proposes, admin decides",
                "verification_states": [
                    verification.State.VERIFIED, verification.State.MOVED,
                    verification.State.UNAVAILABLE, verification.State.FAILED,
                    verification.State.NOT_APPLICABLE]}

    @app.get("/me")
    def me(principal: Principal = viewer_pending) -> dict:
        """Reachable while a temp password is outstanding, or the BFF cannot
        render the change-password page it is about to redirect to."""
        return {"subject": principal.subject, "role": principal.role.value,
                "method": principal.method,
                "must_change_password": principal.must_change_password,
                "display_name": principal.display_name}

    # -- sessions -----------------------------------------------------------

    @app.post("/sessions", status_code=201)
    def sign_in(req: LoginRequest, request: Request) -> JSONResponse:
        """Exchange a username and password for an opaque session token.

        Unauthenticated by necessity — this IS the authentication. Every step
        below is in this order for a reason, and the ordering is the security
        property:

        The failure is uniform. Unknown username, wrong password and disabled
        account produce an identical status and body, AND an identical wall clock
        — the `verify_dummy()` call on the unknown-user branch is what makes the
        identical message worth anything. Without it an unknown account answers in
        ~2 ms and a wrong password in ~60 ms, and the difference is the oracle.

        Throttling answers 429 rather than the uniform 401, which is a mild
        username oracle and a deliberate trade: silently rejecting a CORRECT
        password with "invalid username or password" produces exactly the
        support-ticket class `AuthError`'s docstring complains about. The phrasing
        does not confirm the account exists.
        """
        def bad() -> JSONResponse:
            return JSONResponse({"detail": _BAD_CREDENTIALS}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})

        def throttled(wait: float) -> JSONResponse:
            return JSONResponse(
                {"detail": "too many sign-in attempts; try again shortly"},
                status_code=429, headers={"Retry-After": str(int(wait))})

        # A malformed username is also a uniform 401, not a 422 — otherwise this
        # endpoint becomes a username-format oracle.
        try:
            username = passwords.normalize_username(req.username)
        except passwords.PasswordError:
            passwords.verify_dummy()
            return bad()

        wait = throttle.check(username)
        if wait is not None:
            return throttled(wait)

        row = repo.user_for_login(username)
        if row is None:
            passwords.verify_dummy()
            throttle.record_failure(username)
            return bad()

        locked_until = row.get("locked_until")
        if locked_until is not None:
            now = datetime.now(timezone.utc)
            if locked_until > now:
                return throttled((locked_until - now).total_seconds())

        if row.get("disabled_at") is not None:
            # Indistinguishable from a wrong password, matching M5's decision that
            # unknown/revoked/expired share one message.
            passwords.verify_dummy()
            return bad()

        if not passwords.verify_password(row["password_hash"], req.password):
            repo.note_login_failure(row["id"], limit=throttle.limit,
                                    cooloff_s=int(throttle.cooloff_s))
            throttle.record_failure(username)
            return bad()

        # The ONLY moment the plaintext is available, hence the only place a
        # parameter bump can be applied. This is why argon2's self-describing PHC
        # string was chosen over a hand-rolled scheme with the cost in a column.
        if passwords.needs_rehash(row["password_hash"]):
            repo.touch_password(
                row["id"], passwords.hash_password(req.password, username=username),
                must_change_password=bool(row["must_change_password"]))

        throttle.clear(username)
        repo.note_login_success(row["id"])

        raw = auth.new_session_token()
        expires = datetime.now(timezone.utc) + timedelta(hours=absolute_hours)
        record = repo.create_session(
            user_id=row["id"], digest=auth.credential_digest(raw),
            absolute_expires_at=expires,
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            client_ip=_client_ip(request))
        return JSONResponse({
            "token": raw,
            "expires_at": expires.isoformat(),
            "subject": row["username"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
            "display_name": row.get("display_name"),
            "session_id": record.id,
            "warning": "this value is shown once and is not recoverable",
        }, status_code=201)

    @app.delete("/sessions/current")
    def sign_out(principal: Principal = viewer_pending) -> dict:
        """Revoke server-side, not just client-side.

        The BFF also drops its cookie, but a cookie-only sign-out leaves a valid
        session alive for up to the absolute timeout — a sign-out button that does
        not sign you out. Idempotent: signing out twice is not an error.
        """
        revoked = (repo.revoke_session(principal.session_id, reason="signout")
                   if principal.session_id is not None else False)
        return {"signed_out": True, "session_revoked": revoked}

    @app.post("/me/password")
    def change_own_password(req: PasswordChangeRequest,
                            principal: Principal = viewer_pending) -> dict:
        """Change your own password.

        `current_password` is re-verified ALWAYS, including when
        `must_change_password` is set. That is the control which makes a stolen
        session non-permanent: whoever holds the cookie cannot lock the owner out
        without also knowing the password.
        """
        row = repo.user_for_login(principal.subject)
        if row is None:
            raise NotFound(f"user {principal.subject!r}")
        if not passwords.verify_password(row["password_hash"], req.current_password):
            raise Forbidden("current password is incorrect")
        if req.new_password == req.current_password:
            raise HTTPException(422, "the new password must differ from the current one")

        new_hash = passwords.hash_password(req.new_password, username=principal.subject)
        repo.touch_password(row["id"], new_hash, must_change_password=False)
        # Every other session, but not this one — changing your password should not
        # sign you out of the tab you did it in.
        others = repo.revoke_sessions_for_user(
            row["id"], reason="password_change",
            except_session_id=principal.session_id)
        return {"changed": True, "other_sessions_signed_out": others}

    # -- accounts -----------------------------------------------------------

    @app.get("/users")
    def list_users(include_disabled: bool = True,
                   principal: Principal = admin) -> dict:
        return {"users": [payload(u) for u in
                          repo.list_users(include_disabled=include_disabled)]}

    @app.post("/users", status_code=201)
    def create_user(req: UserCreateRequest, principal: Principal = admin) -> dict:
        """Create an account. **The initial password is returned exactly once.**

        Generated here, not chosen by the admin, and paired with
        `must_change_password`. An admin who picks someone's password knows it —
        and every audit column in this system (jobs.requested_by,
        config_proposals.proposed_by, .decided_by) is only evidence if
        impersonating a colleague is hard. The first thing the new user does is
        make their password unknown to the person who created the account.
        """
        username = passwords.normalize_username(req.username)
        raw = passwords.generate_password()
        record = repo.create_user(
            username=username, password_hash=passwords.hash_password(raw, username=username),
            role=Role(req.role), display_name=req.display_name,
            created_by=principal.subject, must_change_password=True)
        return {**payload(record), "password": raw,
                "warning": "this value is shown once and is not recoverable"}

    @app.post("/users/{user_id}/password")
    def reset_user_password(user_id: int, principal: Principal = admin) -> dict:
        """Reset someone's password. Returns the new one exactly once, and signs
        them out everywhere — a reset they did not ask for should not leave a live
        session behind."""
        target = repo.user(user_id)
        raw = passwords.generate_password()
        repo.touch_password(
            user_id, passwords.hash_password(raw, username=target.username),
            must_change_password=True)
        revoked = repo.revoke_sessions_for_user(user_id, reason="password_change")
        return {**payload(repo.user(user_id)), "password": raw,
                "sessions_signed_out": revoked,
                "warning": "this value is shown once and is not recoverable"}

    @app.post("/users/{user_id}/disable")
    def disable_user(user_id: int, principal: Principal = admin) -> dict:
        target = repo.user(user_id)
        if target.username == principal.subject:
            raise HTTPException(409, "you cannot disable your own account")
        record = repo.set_user_disabled(user_id, disabled=True, by=principal.subject)
        revoked = repo.revoke_sessions_for_user(user_id, reason="disabled")
        return {**payload(record), "sessions_signed_out": revoked}

    @app.post("/users/{user_id}/enable")
    def enable_user(user_id: int, principal: Principal = admin) -> dict:
        """Enable does not touch the password — their old one still works, which
        is the right behaviour for "back from leave"."""
        return payload(repo.set_user_disabled(user_id, disabled=False,
                                              by=principal.subject))

    @app.post("/users/{user_id}/role")
    def set_user_role(user_id: int, req: RoleChangeRequest,
                      principal: Principal = admin) -> dict:
        record = repo.set_user_role(user_id, Role(req.role), by=principal.subject)
        revoked = 0
        if Role(req.role) is not Role.ADMIN:
            # Belt and braces: the role is resolved by join on every request, so a
            # demotion already takes effect immediately. But being ASKED to sign in
            # again is clearer than an admin nav quietly vanishing mid-click.
            revoked = repo.revoke_sessions_for_user(user_id, reason="role_change")
        return {**payload(record), "sessions_signed_out": revoked}

    @app.get("/users/{user_id}/sessions")
    def list_user_sessions(user_id: int, include_revoked: bool = False,
                           principal: Principal = admin) -> dict:
        repo.user(user_id)
        return {"sessions": [payload(s) for s in repo.list_sessions_for_user(
            user_id, include_revoked=include_revoked)]}

    @app.delete("/users/{user_id}/sessions")
    def revoke_user_sessions(user_id: int, principal: Principal = admin) -> dict:
        """"This person has left" — the event docs/09-OPERATIONS.md recorded as
        missing."""
        repo.user(user_id)
        return {"sessions_signed_out":
                repo.revoke_sessions_for_user(user_id, reason="admin")}

    # There is deliberately no DELETE /users/{id}. requested_by / proposed_by /
    # uploaded_by are free text and must keep resolving to a name a human
    # recognises; a delete would leave the audit trail pointing at nobody.

    # -- the month board ----------------------------------------------------

    @app.get("/board")
    def board(month: str | None = Query(default=None, max_length=16),
              principal: Principal = viewer) -> dict:
        """One row per settlement window — its latest job, run and verdict.

        The month-end master is a job like any other and comes back from the same
        query, but it is not a window and must not be rendered as one: its
        `platform` is 'all' and its `period` is the month. Split here rather than
        in the browser, so every client gets the same answer.
        """
        if month is not None:
            _safe_period(month)
        rows = repo.board(month)
        windows = [r for r in rows if (r.get("kind") or "window") == "window"]
        masters = [r for r in rows if (r.get("kind") or "window") == "month_master"]
        return {"month": month, "windows": windows, "month_masters": masters}

    @app.get("/months")
    def list_months(principal: Principal = viewer) -> dict:
        """Every month the system knows about, newest first (D2).

        Derived from the same evidence the board unions — jobs, roster
        declarations and live uploads — so the month picker can only offer
        months that actually exist, instead of a free-text box.
        """
        return {"months": repo.months()}

    @app.post("/months/{month}/master", status_code=201)
    def enqueue_month_master(month: str, principal: Principal = user) -> JSONResponse:
        """Queue the month-end master by hand (A4).

        The only other creation path is the automatic chain after a window run
        (`worker._chain_month_master`) — which cannot help when a master needs
        rebuilding WITHOUT a fresh window run: a late-arriving reference total, a
        repin, a correction to an already-run window. Re-running a window as a
        side effect would be a second settlement run of that window, which is
        exactly the shape D9/D30 exist to prevent.

        Same guards as the chain: `platform='all'`, the month as the period, so
        "at most one master in flight per month" comes from the active-window
        index (migration 013). `ActiveJobExists` is a 409 like any double-queue.
        """
        _safe_month(month)
        try:
            job, created = repo.enqueue(
                ALL_PLATFORMS, month, kind=JobKind.MONTH_MASTER.value,
                # From the session, never the body — the audit trail.
                requested_by=principal.subject,
                priority=-1)          # behind settlement work: it is a summary
        except ActiveJobExists as exc:
            return JSONResponse(
                {"detail": str(exc), "existing": _job_payload(repo, exc.existing)},
                status_code=409)
        return JSONResponse(_job_payload(repo, job), status_code=201 if created else 200)

    # -- jobs ---------------------------------------------------------------

    @app.post("/jobs", status_code=201)
    def enqueue(req: EnqueueRequest, principal: Principal = user) -> JSONResponse:
        try:
            job, created = repo.enqueue(
                req.platform, req.period, refs=req.refs,
                priority=req.priority, max_attempts=req.max_attempts,
                # From the token, never the body — this is the audit trail.
                requested_by=principal.subject,
                idempotency_key=req.idempotency_key)
        except ActiveJobExists as exc:
            return JSONResponse(
                {"detail": str(exc), "existing": _job_payload(repo, exc.existing)},
                status_code=409)
        return JSONResponse(_job_payload(repo, job), status_code=201 if created else 200)

    @app.get("/jobs")
    def list_jobs(state: str | None = None, platform: str | None = None,
                  period: str | None = None, limit: int = Query(default=50, ge=1, le=500),
                  principal: Principal = viewer) -> dict:
        try:
            parsed = JobState(state) if state else None
        except ValueError as exc:
            raise HTTPException(422, f"unknown state {state!r}; expected one of "
                                     f"{[s.value for s in JobState]}") from exc
        jobs = repo.list_jobs(state=parsed, platform=platform, period=period, limit=limit)
        return {"jobs": [payload(j) for j in jobs], "count": len(jobs)}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: int, principal: Principal = viewer) -> dict:
        return _job_payload(repo, repo.get_job(job_id))

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, principal: Principal = user) -> dict:
        try:
            return _job_payload(repo, repo.cancel_job(job_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/jobs/reclaim")
    def reclaim_jobs(principal: Principal = admin) -> dict:
        """Close out jobs whose worker died (**C1**).

        The sweep runs at the top of every worker loop turn already. The hole it
        cannot cover is the one that matters: when the worker that died is the only
        worker, nothing sweeps, the job sits `leased` forever and the board shows it
        as running. This is the same call, reachable without a worker.

        **Admin, not user.** It closes out someone else's in-flight work, and if a
        lease has expired while the worker is in fact alive but slow, this ends a
        run that was going to finish. `service/admin.py job list` shows the leases
        first for exactly that reason.

        Requeues only while attempts remain, which with the default
        `max_attempts=1` means never: an automatic retry of a settlement run is a
        second write of the same money ([D30](../docs/06-DECISIONS.md#d30)).
        """
        result = repo.reclaim_expired()
        requeued = result.get("requeued", [])
        # The repository calls these `dead`. Renamed once, here, on the way out:
        # "dead" is the queue's word for the row and "failed" is what the person
        # reading the button's answer is asking about. Getting this key wrong is a
        # silent no-op — the call succeeds and reports nothing — which is exactly
        # what happened on the first write of this endpoint.
        failed = result.get("dead", [])
        return {
            "requeued": requeued, "failed": failed,
            "message": (
                "Nothing to reclaim — every lease is still live."
                if not requeued and not failed else
                f"Reclaimed {len(requeued) + len(failed)} job(s): "
                f"{len(requeued)} requeued, {len(failed)} marked failed. Their runs "
                f"are closed, so the board no longer shows them running."),
        }

    # -- runs ---------------------------------------------------------------

    @app.get("/runs/{run_id}")
    def get_run(run_id: int, principal: Principal = viewer) -> dict:
        run = repo.get_run(run_id)
        out = _run_payload(run)
        out["artifacts"] = [payload(a) for a in repo.artifacts(run_id)]
        if hasattr(repo, "exception_sheets"):
            out["exception_sheets"] = repo.exception_sheets(run_id)
        return out

    @app.get("/runs/{run_id}/log")
    def get_log(run_id: int, after_seq: int = Query(default=-1, ge=-1),
                limit: int = Query(default=1000, ge=1, le=5000),
                principal: Principal = viewer) -> dict:
        """Incremental run log. Poll with the `next_seq` from the last response.

        `seq` is producer-assigned and gapless, so a client can prove it lost
        nothing; `complete` says stop rather than leaving it to be inferred from
        an empty page.
        """
        lines, next_seq, complete = repo.log_lines(run_id, after_seq=after_seq, limit=limit)
        return {"run_id": run_id, "lines": [payload(l) for l in lines],
                "next_seq": next_seq, "complete": complete and len(lines) < limit}

    @app.get("/runs/{run_id}/artifacts")
    def list_artifacts(run_id: int, principal: Principal = viewer) -> dict:
        repo.get_run(run_id)
        return {"artifacts": [payload(a) for a in repo.artifacts(run_id)]}

    @app.get("/runs/{run_id}/artifacts/{name}")
    def download_artifact(run_id: int, name: str, principal: Principal = viewer) -> Any:
        """The finance workbook, through the authorization model.

        `open()` first, because a local file served by `FileResponse` gets sendfile
        and Range support for free. Otherwise the store streams it — which is what
        M6 added, and what stops this returning 501 in the deployment being
        targeted (defect 2.4). Never a presigned URL: that is a credential in a
        query string this function never sees, and it grants whoever holds the link
        a workbook containing every store's revenue.
        """
        art = repo.artifact(run_id, name)
        local = store.open(art.uri)
        if local is not None:
            _verify_artifact(art, artifacts.sha256_of(local))
            # C12: the read audit, after the digest check and before a byte moves.
            # In the request path deliberately — see migration 018.
            repo.record_artifact_download(run_id, name, principal.subject)
            return FileResponse(local, filename=art.name)

        probe = store.stream(art.uri)
        if probe is not None:
            _verify_artifact(art, artifacts.sha256_of_chunks(probe))
            repo.record_artifact_download(run_id, name, principal.subject)

        chunks = store.stream(art.uri)
        if chunks is None:
            raise HTTPException(
                404, f"the bytes for {art.name!r} are not in this deployment's "
                     f"artifact store ({art.uri!r}). A run made before this "
                     f"deployment moved to object storage keeps its old address, "
                     f"and those files stayed on the volume.")
        return StreamingResponse(
            chunks,
            media_type="application/vnd.openxmlformats-officedocument."
                       "spreadsheetml.sheet" if name.endswith(".xlsx")
                       else "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{art.name}"',
                     # From the row, not from the stream: the api never buffers a
                     # 30 MB workbook to find out how long it is.
                     "Content-Length": str(art.bytes)})

    # -- the exception queue ------------------------------------------------

    @app.get("/runs/{run_id}/exceptions")
    def run_exceptions(run_id: int, sheet: str | None = None,
                       limit: int = Query(default=200, ge=1, le=2000),
                       offset: int = Query(default=0, ge=0),
                       open_only: bool = Query(default=False),
                       principal: Principal = viewer) -> dict:
        """Exception rows, with the sheet totals alongside.

        `sheets` carries `total_rows` and `stored_rows` so a capped queue can
        never read as a complete one. Each row carries its standing disposition
        (D1); `open_only` is an explicit filter the screen offers — the default
        answer is always the whole queue.
        """
        repo.get_run(run_id)
        return {"run_id": run_id, "sheets": repo.exception_sheets(run_id),
                "exceptions": repo.exceptions(run_id, sheet=sheet, limit=limit,
                                              offset=offset, open_only=open_only)}

    @app.get("/exceptions/{fingerprint}/history")
    def exception_history(fingerprint: str, principal: Principal = viewer) -> dict:
        """Every run this same exception appeared in, and what was decided about it.

        An unmatched order recurring for six weeks is a different thing from one
        that appeared once, and no per-run view can tell them apart. The current
        disposition and its mark/clear history ride along (D1) — one payload,
        the way `/config/pins` returns both `pins` and `events`.
        """
        return {"fingerprint": fingerprint,
                "runs": repo.exception_history(fingerprint),
                "disposition": payload(repo.exception_disposition(fingerprint)),
                "events": [payload(e) for e in
                           repo.exception_disposition_events(fingerprint)]}

    @app.post("/exceptions/{fingerprint}/disposition")
    def set_disposition(fingerprint: str, req: DispositionRequest,
                        principal: Principal = user) -> dict:
        """Mark a recurring exception `reviewed` or `expected` (D1).

        **Annotates, never hides**: the queue still shows the row on every run,
        badged with this decision — the fingerprint hashes identity columns, not
        amounts, so an "expected" variance that has grown must still be seen.
        USER, not ADMIN, for the roster declaration's reason: the person working
        the queue is the one who knows why a row is expected, and the control is
        the recorded reason, not the rank. The actor comes from the session.
        """
        # A fingerprint nothing has ever produced would be an orphan decision —
        # most likely a typo'd URL, so refuse it rather than record it.
        if not repo.exception_history(fingerprint, limit=1):
            raise HTTPException(404, "no run has produced an exception with this "
                                     "fingerprint, so there is nothing to decide on")
        return payload(repo.set_exception_disposition(
            fingerprint, disposition=req.disposition, reason=req.reason,
            actor=principal.subject))

    @app.delete("/exceptions/{fingerprint}/disposition")
    def clear_disposition(fingerprint: str, req: DispositionClearRequest,
                          principal: Principal = user) -> dict:
        """Re-open a dispositioned exception. A reason is required — the clear
        event records what was released, the unpin pattern (migration 020)."""
        if not repo.clear_exception_disposition(
                fingerprint, actor=principal.subject, reason=req.reason):
            raise HTTPException(404, "no decision is recorded for this exception")
        return {"fingerprint": fingerprint, "disposition": None}

    # -- uploads and staging ------------------------------------------------

    def _domain_for_window(platform: str, period: str) -> dict:
        """The config **this window will actually run under**, not today's disk copy.

        A window that has run before is pinned to the config it ran under, and the
        demo window is pinned to a synthetic roster from the moment it is seeded. So
        checking an upload — or previewing roster coverage — against
        `config/settings.yaml` asks the wrong question: it reports the real roster's
        stores as missing from a window whose roster is a different list.

        Found by running the seeded demo through the containers: every plan screen
        said `ready: false` and listed 14–17 absent real stores, while the runs
        themselves completed with `roster_missing = 0` because the **worker** resolves
        the pinned config correctly. A preview that disagrees with the control is
        worse than no preview — an operator sees "not ready" and cannot act on it.

        Deliberately not `config_store.resolve_for_window`: that RECORDS a config
        version as a side effect, which a GET must not do.
        """
        if hasattr(repo, "pinned_config"):
            pinned = repo.pinned_config(platform, period)
            if pinned is not None:
                from src import config as src_config
                return src_config.parse_settings(pinned["content"])
        return _settings_dict(settings)

    def _check_window(platform: str, period: str, kind: str) -> None:
        """The three form fields, validated as a set rather than individually.

        `kind` is only meaningful for a platform: `lazada`/`orders` is not a
        typo in one field, it is an incoherent pair, and reporting it as
        "unknown kind" sends an operator to fix the wrong thing.
        """
        if platform not in PLATFORMS:
            raise HTTPException(422, f"unknown platform {platform!r}; expected one "
                                     f"of {list(PLATFORMS)}")
        valid = naming.KINDS_BY_PLATFORM[platform]
        if kind not in valid:
            raise HTTPException(422, f"{platform} has no {kind!r} files; expected one "
                                     f"of {list(valid)}")
        try:
            # Form fields are not a pydantic model, so the validator's ValueError
            # would surface as a 500 rather than the 422 it is.
            _safe_period(period)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/uploads", status_code=201)
    def upload_export(file: UploadFile = File(...),
                      platform: str = Form(...), period: str = Form(...),
                      kind: str = Form(...),
                      store: str | None = Form(default=None),
                      principal: Principal = user) -> dict:
        """Accept a raw export, strip it, resolve its store, put it in the bucket.

        Four things happen here, and the order is the design:

        1. **The store is resolved at the door**, by the pipeline's own
           `store_from_filename` — not by a copy of it. A filename the pipeline
           cannot parse is refused now, while a human is looking at it, instead of
           hard-stopping a run at month end. `store` may be sent to *confirm or
           correct* what the regex found; both values are recorded.
        2. **A store nobody has confirmed is refused**, with the fix named: add it
           to the roster through a config proposal. `check_stores` still hard-stops
           on it inside the run, so this is a better first line, not a replacement
           for the control.
        3. **The strip uses the pipeline's own column map**, so there is no second
           PII list to maintain and go stale. The unstripped original never
           touches durable storage — it exists only as bytes in this request.
        4. **The sanitized copy goes to the object store, always as `.xlsx`.**
           There is no staging step any more: the bucket is the window, and the
           worker assembles the input tree itself (service/materialize.py).

        Byte-identical re-uploads are refused by the database — the double-pull
        class, one instance of which carried 5.97B VND of double-invoicing risk
        (docs/06-DECISIONS.md#d9).
        """
        if settings is None:
            raise HTTPException(501, "this deployment has no upload root configured")
        _check_window(platform, period, kind)

        filename = upload_lib.check_filename(file.filename or "")
        # C7: bounded, never `read()` the whole part unchecked. Starlette has
        # already spooled the multipart body to a temp file by now, so the memory
        # event this prevents is OUR read of it — one oversized request on the
        # threadpool used to cost its whole body in RAM. `read(limit + 1)` is how
        # the cap is proven rather than trusted: a body that fills the extra byte
        # is over, whatever its Content-Length claimed. 512 MB default is ~2.8x
        # the largest export any platform has actually produced (184 MB, measured
        # 2026-08-20); a legitimately bigger file raises RECON_MAX_UPLOAD_MB.
        limit = settings.max_upload_mb * 1024 * 1024
        raw = file.file.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(
                413, f"{filename} is larger than {settings.max_upload_mb} MB, the "
                     f"per-file limit (RECON_MAX_UPLOAD_MB). The largest real "
                     f"export ever staged is 184 MB — check the file before "
                     f"raising the limit.")
        if not raw:
            raise HTTPException(422, "empty file")
        digest = upload_lib.digest_bytes(raw)
        # The config this WINDOW runs under, not today's disk copy — see
        # _domain_for_window. A pinned window (a re-run, or the demo) has a
        # different roster, and refusing an upload against the wrong one is a dead
        # end an operator cannot act on.
        domain = _domain_for_window(platform, period)

        try:
            derived = naming.store_of(filename, platform, domain)
        except naming.NamingError as exc:
            raise HTTPException(
                422, f"{exc} Rename the file to match what the platform exports, "
                     f"or fix store_from_filename.{platform} through the config "
                     f"editor.") from exc

        declared = (store or "").strip() or derived
        canonical = materialize.canonical_store(domain, platform, declared)

        # The confirmed store must survive being written into a uniform name. This
        # matters specifically when a HUMAN supplied it: TikTok's pattern strips a
        # trailing bare 1-2 digit token, so a corrected store of `Unilever 2` would
        # be read back as `Unilever` and quietly invoice two storefronts as one.
        # Refused here rather than at materialisation, where it would fail the whole
        # window at month end instead of one upload while someone is watching.
        try:
            naming.validate_roundtrip(
                naming.uniform_name(platform, kind, 1, canonical),
                platform, canonical, domain)
        except naming.NamingError as exc:
            raise HTTPException(422, str(exc)) from exc

        expected = set((domain.get("expected_stores") or {}).get(platform) or [])
        if expected and canonical not in expected:
            raise HTTPException(
                422, f"{canonical!r} is not on the {platform} roster. A store the "
                     f"roster does not name would hard-stop the run anyway "
                     f"(check_stores), so it is refused here instead: propose "
                     f"adding it to expected_stores.{platform}, or add an alias if "
                     f"it is an existing store under a new spelling.")
        # No roster means nothing checked this storefront — not that it is known
        # to be right. `check_stores` self-skips on an empty roster too, so a run
        # will not catch it either, and Lazada has no roster at all
        # (docs/14-PRODUCTION-READINESS.md A6). Reported rather than refused: a
        # 422 here would make every Lazada upload impossible, and reported rather
        # than silent because "accepted" and "unchecked" must not look the same.
        roster_checked = bool(expected)

        # The uniform name cannot be computed yet — its ordinal is a property of
        # the whole window and is assigned per run (service/naming.py). The key is
        # content-addressed instead, which also makes a re-put idempotent.
        target_name = upload_lib.sanitized_name(filename)
        key = f"{objects_prefix}/{period}/{platform}/{kind}/{digest}.xlsx"

        scratch = settings.scratch_root / "incoming"
        scratch.mkdir(parents=True, exist_ok=True)
        incoming = scratch / f"{digest[:16]}-{filename}"
        sanitized_path = scratch / f"{digest[:16]}-sanitized-{target_name}"
        incoming.write_bytes(raw)
        try:
            result = upload_lib.sanitize(incoming, sanitized_path, settings=domain,
                                         platform=platform, kind=kind)

            # Does this file belong to the window it was addressed to? Until now
            # `period` was validated for character safety and nothing else, so a
            # mis-labelled export was discovered at the month-end tie rather than
            # at the door (defect 2.3's residual). Refusal happens BEFORE the
            # object store is written and before the row is inserted: a file this
            # window should not contain must leave no trace that it might.
            # The sibling query only runs for the kinds that can use it. A TikTok
            # window is mostly order files, and they are never date-checked.
            siblings = (repo.upload_spans(platform, period, kind)
                        if kind in upload_lib.WINDOW_DEFINING else [])
            refusal, span_warning = upload_lib.check_span(
                period, kind,
                settles_from=result.settles_from, settles_to=result.settles_to,
                sibling_starts=[r["settles_from"] for r in siblings])
            if refusal:
                raise HTTPException(422, f"{filename}: {refusal}")

            sanitized_bytes = sanitized_path.read_bytes()
            ref = uploads_objects.put(key, sanitized_bytes)
            record = repo.record_upload(
                filename=filename, sha256=digest, bytes_=len(raw),
                uploaded_by=principal.subject, platform=platform, period=period,
                kind=kind, pii_columns_dropped=result.dropped_columns,
                # The headers the contract DID recognise. With `pii_columns_dropped`
                # (which holds every dropped header, not only the PII ones) this is
                # the file's original header row — and the sanitized object in the
                # bucket no longer contains the dropped ones, so recording it here
                # is the only chance (register D5, migration `023`).
                kept_columns=result.kept_columns,
                sanitized=True, uri=ref.uri, object_key=key, state="stored",
                store=declared, store_canonical=canonical,
                # The digest of what went INTO the store, not of what arrived. The
                # run reads this file, so this is the value worth checking
                # (010_object_digest.sql).
                object_sha256=object_lib.digest_of(sanitized_bytes))
        except DuplicateUpload as exc:
            return JSONResponse(
                {"detail": str(exc), "existing": payload(exc.existing)}, status_code=409)
        finally:
            # Neither copy outlives the request. This is the whole point of
            # stripping at the boundary rather than at read time — the stripped
            # bytes are in the bucket, the unstripped bytes are nowhere.
            incoming.unlink(missing_ok=True)
            sanitized_path.unlink(missing_ok=True)

        # Which (store, order_id) pairs this file holds — the index that lets
        # "does some OTHER window's order file cover this order?" be asked at all
        # (defect 2.12; migration 015). Deliberately AFTER the upload is durable
        # and outside its failure path: the index is derived, rebuildable data
        # feeding a report, so losing a write here must degrade that report and
        # never fail an upload whose bytes are already safely stored. It is
        # reported rather than swallowed, because `service/order_index.py
        # --backfill` is how it gets fixed and nobody runs it for a silent gap.
        indexed: int | None = None
        index_note = ""
        try:
            indexed = repo.record_order_index(
                record["id"], canonical, result.order_ids,
                settles_from=result.settles_from, settles_to=result.settles_to)
        except Exception as exc:                      # noqa: BLE001 — see above
            index_note = (f"stored, but its order index was not written ({exc}). "
                          f"Cross-window order coverage will under-report this "
                          f"file until `service.order_index --backfill` runs.")

        return {**payload(record), "sheet": result.sheet, "rows": result.rows,
                "kept_columns": result.kept_columns,
                "dropped_known_pii": result.dropped_known_pii,
                # Register D5: headers this export carries that the contract does
                # not name, PII excluded. A renamed column looks exactly like this,
                # and until now the only place it surfaced was a hard stop ~200
                # seconds into a run, phrased for a developer. The window's own
                # required-field arithmetic is on `/uploads/plan`, because
                # `read_parts` checks the CONCATENATION of a kind's files — a part
                # file with fewer columns is legitimate and must not be refused here.
                "unrecognised_headers": result.unrecognised_headers,
                "sheets_read": result.sheets_read,
                "store_derived_from_filename": derived,
                "store_corrected": declared != derived,
                "roster_checked": roster_checked,
                "roster_note": (
                    "" if roster_checked else
                    f"No storefront roster is configured for {platform}, so nothing "
                    f"verified that {canonical!r} is a real storefront — and the run "
                    f"will not check it either. Until a roster exists, a typo here "
                    f"invoices under a storefront nobody expects."),
                # Greyed in the UI: the real ordinal is decided at run time.
                "uniform_name_preview": naming.preview_name(platform, kind, canonical),
                # The settlement span this file declares, and what was done with it.
                # `settles_checked: false` means the file carries no date column for
                # this platform/kind — reported, never guessed at.
                "settles_from": (result.settles_from.isoformat()
                                 if result.settles_from else None),
                "settles_to": (result.settles_to.isoformat()
                               if result.settles_to else None),
                "settles_checked": kind in upload_lib.WINDOW_DEFINING
                                   and result.settles_from is not None,
                "span_warning": span_warning or "",
                "order_ids_indexed": indexed,
                "index_note": index_note}

    @app.get("/uploads")
    def list_uploads(platform: str | None = None, period: str | None = None,
                     state: str | None = None, principal: Principal = viewer) -> dict:
        return {"uploads": [payload(u) for u in
                            repo.list_uploads(platform=platform, period=period, state=state)]}

    @app.post("/uploads/store-preview")
    def upload_store_preview(body: StorePreviewRequest,
                             principal: Principal = user) -> dict:
        """Which store each filename resolves to, before the bytes are sent (D7).

        `POST /uploads` has accepted a `store` field since M6 — to confirm or
        correct what the filename regex found — and `web/app/actions.ts` has posted
        it per file for just as long. **Nothing ever rendered an input**, so the
        documented affordance was unreachable and an operator whose filename parsed
        to the wrong storefront had one recourse: rename the file on disk. This is
        the missing half.

        Three properties worth stating, because each is a thing this route
        deliberately does not do:

        1. **It resolves through `naming.store_of`, the pipeline's own rule** — not
           a copy, and emphatically not a regex in the browser. Store identity comes
           from the filename (`06-DECISIONS.md#d6`), and a second implementation of
           it near the upload form is the most invasive drift this system could
           acquire.
        2. **It reads the config THIS WINDOW will run under** (`_domain_for_window`),
           so a pinned window previews against its own roster. A preview that
           disagrees with the control is worse than no preview.
        3. **It is a POST that changes nothing**, because the input is a list of
           filenames and a filename is not a query parameter you want to length-cap
           by URL. Role `user` rather than `viewer` all the same: it is a step in
           uploading, and a viewer cannot upload.

        A GET would also have put every storefront's filenames in the api access
        log. They are business identifiers rather than customer PII, but there is
        no reason to spend them.
        """
        if settings is None:
            raise HTTPException(501, "this deployment has no config directory")
        _check_window(body.platform, body.period, body.kind)

        domain = _domain_for_window(body.platform, body.period)
        expected = set((domain.get("expected_stores") or {}).get(body.platform) or [])
        out = []
        for raw in body.filenames:
            row: dict = {"filename": raw, "store": None, "canonical": None,
                         "on_roster": None, "uniform_name": None, "problem": None}
            try:
                name = upload_lib.check_filename(raw)
                derived = naming.store_of(name, body.platform, domain)
            except (ValueError, naming.NamingError) as exc:
                # The same sentence the upload itself would answer with, arrived at
                # before the operator waits for a 184 MB transfer to be refused.
                row["problem"] = str(exc)
                out.append(row)
                continue

            canonical = materialize.canonical_store(domain, body.platform, derived)
            row["store"], row["canonical"] = derived, canonical
            try:
                row["uniform_name"] = naming.preview_name(
                    body.platform, body.kind, canonical)
                naming.validate_roundtrip(
                    naming.uniform_name(body.platform, body.kind, 1, canonical),
                    body.platform, canonical, domain)
            except naming.NamingError as exc:
                row["problem"] = str(exc)
                out.append(row)
                continue

            # `on_roster` is None, not False, when nothing checked it: Lazada has no
            # roster at all (register A6) and the upload door reports rather than
            # refuses there. "Unchecked" and "wrong" must not render the same.
            row["on_roster"] = (canonical in expected) if expected else None
            out.append(row)

        return {"platform": body.platform, "period": body.period, "kind": body.kind,
                # The picklist's options. The window page already has this from
                # `/uploads/plan` (D3's roster form uses it), but a caller of this
                # route alone should not have to make a second request to know what
                # a valid correction would be.
                "expected_stores": sorted(expected),
                "roster_checked": bool(expected), "files": out}

    @app.get("/uploads/plan")
    def upload_plan(platform: str = Query(...), period: str = Query(...),
                    principal: Principal = viewer) -> dict:
        """What this window currently contains, and what it is still missing.

        The screen an operator works from before queueing. It answers three
        questions the old flow could only answer by starting a run and reading a
        hard stop: which files are here, what they will be named when the run
        reads them, and which expected stores have nothing.

        A GET, not the POST the plan sketched: it computes nothing durable and
        changes nothing, so a viewer can watch a window fill up without being able
        to alter it.
        """
        if settings is None:
            raise HTTPException(501, "this deployment has no config directory")
        if platform not in PLATFORMS:
            raise HTTPException(422, f"unknown platform {platform!r}")
        _safe_period(period)

        domain = _domain_for_window(platform, period)
        rows = repo.uploads_for_window(platform, period)
        kinds: dict[str, list[dict]] = {}
        problems: list[str] = []

        # Register D5: which canonical fields this window's files actually supply,
        # per kind, and which the run will hard-stop for. Computed here rather than
        # at the door because `ingest.read_parts` checks the CONCATENATION of a
        # kind's part files — a "part 2" export with fewer columns is legitimate and
        # refusing it per file would break a real window to catch a fault the union
        # does not have. This is the same arithmetic, one step earlier, in a
        # sentence rather than a traceback ~200 seconds into a run.
        drift: dict[str, dict] = {}

        for kind in naming.KINDS_BY_PLATFORM[platform]:
            group = {r["filename"]: r for r in rows if r["kind"] == kind}
            colmap = upload_lib.column_map_for(domain, platform, kind)
            unrecognised: dict[str, list[str]] = {}
            for name, row in sorted(group.items()):
                strange = sorted(h for h in (row.get("pii_columns_dropped") or ())
                                 if h not in upload_lib.KNOWN_PII)
                if strange:
                    unrecognised[name] = strange
            # `kept_columns` is empty for anything uploaded before migration `023`,
            # which would read as "this file supplies nothing" and invent a drift
            # report for a healthy window. Absent evidence is reported as absent.
            measured = any(row.get("kept_columns") for row in group.values())
            missing = (upload_lib.missing_fields(
                [list(row.get("kept_columns") or ()) for row in group.values()],
                colmap, kind) if measured else [])
            drift[kind] = {"missing_fields": missing,
                           "unrecognised_headers": unrecognised,
                           "checked": (measured
                                       and bool(upload_lib.required_fields(kind)))}
            if missing:
                problems.append(
                    f"{kind}: no file in this window supplies "
                    f"{', '.join(missing)}. The export's headers have most likely "
                    f"been renamed — map the new spelling in the rules, as a "
                    f"parallel entry beside the old one.")
            if not group:
                kinds[kind] = []
                continue
            try:
                planned = naming.plan_window(list(group), platform, kind, domain)
            except naming.NamingError as exc:
                problems.append(f"{kind}: {exc}")
                kinds[kind] = [{"filename": name, "upload_id": row["id"],
                                "store": row.get("store"), "uniform_name": None}
                               for name, row in sorted(group.items())]
                continue
            kinds[kind] = [
                {"filename": item.original, "upload_id": group[item.original]["id"],
                 "store": group[item.original].get("store_canonical") or item.store,
                 "uniform_name": item.name, "ordinal": item.ordinal,
                 "renamed": item.renamed,
                 "uploaded_by": group[item.original]["uploaded_by"],
                 "bytes": group[item.original]["bytes"],
                 "state": group[item.original]["state"]}
                for item in planned]

        found = {r.get("store_canonical") for r in rows if r.get("store_canonical")}
        missing, unexpected = materialize.roster_gap(domain, platform, found)
        declaration = repo.window_declaration(platform, period)

        # D3: a declaration that names stores covers exactly those; a missing
        # store it does not name will hard-stop, and `ready` must say so rather
        # than promising a run the pipeline will refuse. `stores` NULL is the
        # legacy blanket and covers everything, as before.
        declared = (declaration or {}).get("declared_absent_stores")
        declared_partial = bool(declaration and declaration["roster_declared_partial"])
        covered = declared_partial and (declared is None
                                        or set(missing) <= set(declared))
        return {
            "platform": platform, "period": period,
            "files": kinds,
            "stores_present": sorted(found),
            "missing_stores": missing,
            "unexpected_stores": unexpected,
            # Names, not only the count: the declaration form offers the roster
            # as a picklist instead of a memory test. Store names are business
            # identifiers that already render on this page — never customer PII.
            "expected_stores": sorted((domain.get("expected_stores") or {})
                                      .get(platform) or []),
            "expected_store_count": len((domain.get("expected_stores") or {})
                                        .get(platform) or []),
            "problems": problems,
            # Register D5, per kind: `missing_fields` is what the run will stop for,
            # `unrecognised_headers` is the evidence for fixing it (per filename),
            # and `checked` distinguishes "nothing is wrong" from "nothing was
            # measured" — Lazada has no required set at all, and a file uploaded
            # before migration `023` recorded no headers.
            "drift": drift,
            # The right-hand side of a column-map proposal: what an unrecognised
            # header may be mapped TO. Derived from the pipeline's own constants
            # (`config_rows.canonical_fields`), never a list kept here.
            "canonical_fields": (repo.canonical_fields()
                                 if hasattr(repo, "canonical_fields") else []),
            "roster_declaration": payload(declaration) if declaration else None,
            # D3's re-evaluation nudge: declared-absent stores that now HAVE
            # files. Their figures are included either way — this is the record
            # not matching the window any more, and the page renders it amber.
            "declared_absent_present": sorted(set(declared or []) & found),
            # What `POST /jobs` will do with this window as it stands.
            "ready": not problems and (not missing or covered),
        }

    @app.post("/uploads/{upload_id}/reject")
    def reject_upload(upload_id: int, req: RejectRequest,
                      principal: Principal = user) -> dict:
        """Take a file out of the window. Never a delete.

        An operator who uploaded the wrong export and then the right one leaves two
        rows, and which was rejected and why is the audit trail for a window whose
        numbers somebody later queries. A file a run has already read cannot be
        un-read and is refused with a 409.
        """
        try:
            return payload(repo.reject_upload(upload_id, req.reason))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    # `POST /uploads/{id}/stage` is deleted. The bucket is the window, so there is
    # nothing to move, and its two defects went with it: no collision guard on the
    # target filename, and reading an upload through `ArtifactStore.open` — the
    # conflation that made the api need an input volume the worker also had
    # (defect 2.4).

    # -- the demo window ----------------------------------------------------

    @app.post("/demo/seed", status_code=201)
    def seed_demo(principal: Principal = admin) -> dict:
        """Generate a believable window across all three platforms, from nothing.

        Admin-only, because it writes uploads and a config version. The files go
        through the object store exactly as a browser upload would, so the demo
        exercises the real materialisation path rather than a shortcut past it.

        The synthetic roster reaches a run through the **pin** mechanism, so
        `config/settings.yaml` is never touched and the demo's two invented stores
        cannot leak into a real window's store-count check.
        """
        if settings is None:
            raise HTTPException(501, "this deployment has no config directory")
        from . import sampledata
        return sampledata.seed(repo, settings, seeded_by=principal.subject)

    @app.delete("/demo/seed")
    def unseed_demo(principal: Principal = admin) -> dict:
        """Remove the demo window's uploads and its config pin.

        **Runs are left alone.** A run that happened is history; deleting the record
        of one because its input was synthetic would make the demo the only part of
        this system that can rewrite the past.
        """
        if settings is None:
            raise HTTPException(501, "this deployment has no config directory")
        from . import sampledata
        return sampledata.unseed(repo, settings)

    # -- windows: the roster declaration ------------------------------------

    @app.get("/windows/{platform}/{period}/order-coverage")
    def get_order_coverage(platform: str, period: str,
                           principal: Principal = viewer) -> dict:
        """Which settled orders this window's own order exports do not cover.

        Read-only and **counts only** — no amount is computed or returned here. The
        authoritative per-store coverage comes out of the run itself
        (`tieout.coverage_by_store`, from the frames it reads); this asks the same
        question of the *uploads*, so the answer exists before anything is queued.

        `cross_window` is the half that has no legitimate traffic and is therefore the
        one worth acting on: an order whose lines sit in an EARLIER window's export is
        defect 2.12, where the ~21% reconciling class has lines in no window at all.
        `indexed` says whether the question could be answered at all — a window whose
        uploads predate the index answers empty, which must not read as "all covered"
        (`service.order_index --backfill` is the fix).
        """
        if platform not in PLATFORMS:
            raise HTTPException(422, f"unknown platform {platform!r}")
        _safe_period(period)
        if not hasattr(repo, "order_coverage"):
            raise HTTPException(501, "this deployment has no order index")

        rows = repo.order_coverage(platform, period)
        cross = repo.cross_window_order_holders(platform, period)
        uploads = repo.uploads_for_window(platform, period)
        indexable = [u for u in uploads if u.get("state") in ("stored", "consumed")]
        unindexed = [u["filename"] for u in indexable if not u.get("indexed_at")]
        return {
            "platform": platform, "period": period,
            "stores": [{"store": r["store"], "income_orders": r["income_orders"],
                        "unmatched_orders": r["unmatched_orders"]} for r in rows],
            "cross_window": [{"store": r["store"], "holder_period": r["holder_period"],
                              "filename": r["filename"], "upload_id": r["upload_id"],
                              "orders": r["orders"]} for r in cross],
            "indexed": not unindexed and bool(indexable),
            "unindexed_files": unindexed,
        }

    @app.get("/windows/{platform}/{period}")
    def get_window(platform: str, period: str, principal: Principal = viewer) -> dict:
        if platform not in PLATFORMS:
            raise HTTPException(422, f"unknown platform {platform!r}")
        _safe_period(period)
        declaration = repo.window_declaration(platform, period)
        record = (repo.window_references(platform, period)
                  if hasattr(repo, "window_references") else None)
        refs = (record or {}).get("refs") or {}
        return {"platform": platform, "period": period,
                "roster_declaration": payload(declaration) if declaration else None,
                # A3: the team's own totals, and the fields that exist to hold them.
                # The spec is served rather than duplicated in TypeScript, so a key
                # the pipeline stopped reading cannot leave a form field collecting a
                # number nothing compares (service/references.py).
                "reference_fields": references.payload_for(platform),
                "references": payload(record) if record else None,
                # Both languages, because the api has no notion of who is reading and
                # a `?lang=` parameter would put a display concern in the wire format.
                # The BFF picks (M8/5.3).
                "references_summary": references.summarise(platform, refs),
                "references_summary_vi": references.summarise(platform, refs, "vi")}

    @app.post("/windows/roster", status_code=201)
    def declare_roster(req: RosterDeclarationRequest,
                       principal: Principal = user) -> dict:
        """Declare that this window legitimately holds a subset of the roster.

        Without a declaration an incomplete window **hard-stops**, which is
        today's behaviour and the control that caught a real Shopee window
        arriving with 16 of 17 stores absent. What M6 changes is only where the
        override comes from: not a checkbox ticked every run by whoever is in a
        hurry, but one statement per window, with a reason, attributed, and
        rendered on the board for whoever reviews the numbers.

        `declared_by` comes from the session, never the body.
        """
        reason = (req.reason or "").strip()
        if req.partial and len(reason) < 8:
            raise HTTPException(
                422, "a partial-roster declaration needs a reason of at least 8 "
                     "characters. The reason is the entire difference between this "
                     "and a checkbox.")
        stores = sorted({s.strip() for s in (req.stores or []) if s.strip()}) or None
        if stores and not req.partial:
            raise HTTPException(
                422, "a complete window has no absent stores to name — either "
                     "declare it partial or leave the store list empty")
        return payload(repo.declare_window_roster(
            req.platform, req.period, partial=req.partial,
            reason=reason or None, declared_by=principal.subject,
            stores=stores))

    @app.delete("/windows/{platform}/{period}/roster")
    def clear_roster_declaration(platform: str, period: str,
                                 principal: Principal = user) -> dict:
        """Withdraw a declaration, so the window hard-stops again if incomplete."""
        if platform not in PLATFORMS:
            raise HTTPException(422, f"unknown platform {platform!r}")
        _safe_period(period)
        if not repo.clear_window_declaration(platform, period):
            raise HTTPException(404, f"{platform} {period} has no roster declaration")
        return {"platform": platform, "period": period, "roster_declaration": None}

    @app.put("/windows/{platform}/{period}/references")
    def set_references(platform: str, period: str, req: ReferencesRequest,
                       principal: Principal = user) -> dict:
        """Record the team's own totals for this window.

        **What this buys.** A run with no references exits UNVERIFIED — it ran clean
        and nothing corroborated it. Since M6 that has been every browser-driven run,
        because the api accepted `refs` on a job and no screen ever sent any. This is
        the screen.

        Stored against the WINDOW, so a re-run compares against the same figures the
        first run did. `supplied_by` comes from the session, never the body.

        Nothing is recomputed and nothing is coerced to zero: a blank field means the
        team did not give us that number, and `_tie_grand` skips a key it does not
        find. A zero would compare the window against 0 VND and report all of it as a
        variance.
        """
        if platform not in PLATFORMS:
            raise HTTPException(422, f"unknown platform {platform!r}")
        _safe_period(period)
        if not hasattr(repo, "set_window_references"):
            raise HTTPException(501, "this deployment cannot record reference totals")
        try:
            refs = references.parse(platform, req.values)
        except references.ReferenceError as exc:
            raise HTTPException(422, str(exc)) from exc
        record = repo.set_window_references(
            platform, period, refs=refs, supplied_by=principal.subject,
            note=(req.note or "").strip() or None)
        return {"platform": platform, "period": period,
                "references": payload(record),
                "references_summary": references.summarise(platform, refs),
                "references_summary_vi": references.summarise(platform, refs, "vi")}

    @app.delete("/windows/{platform}/{period}/references")
    def clear_references(platform: str, period: str,
                         principal: Principal = user) -> dict:
        """Withdraw the figures, so runs of this window report UNVERIFIED again."""
        if platform not in PLATFORMS:
            raise HTTPException(422, f"unknown platform {platform!r}")
        _safe_period(period)
        if not repo.clear_window_references(platform, period):
            raise HTTPException(404, f"{platform} {period} has no reference totals")
        return {"platform": platform, "period": period, "references": None,
                "references_summary": references.summarise(platform, {}),
                "references_summary_vi": references.summarise(platform, {}, "vi")}

    # -- config -------------------------------------------------------------

    def _current_contract() -> str:
        """The contract as it stands, rendered from the config tables.

        Every config route reads through here rather than off this process's
        filesystem. That is the A1 fix restated at the editor: the api and the
        worker are separate containers with their own baked copies of `config/`,
        so a route that diffed against its own disk copy would show an operator a
        change against a file the worker never reads.
        """
        rendered = repo.render_config() if hasattr(repo, "render_config") else None
        if rendered is not None:
            return rendered
        if settings is None:
            raise HTTPException(501, "this deployment has no config directory")
        # Empty tables: a deployment that has never been seeded. Falling back to
        # the file keeps the page readable, and `POST /config/proposals` refuses
        # rather than editing something that is not the source of truth.
        return config_store.read_text(settings.config_dir)

    @app.get("/config")
    def get_config(principal: Principal = viewer) -> dict:
        """The contract as a whole file, verbatim — comments and all.

        Still verbatim and still returned in full: it is what a run is pinned to,
        and the page shows it underneath the form. Since M8 it is RENDERED from the
        config tables rather than read off disk, which is what makes it the same
        bytes the worker will compute under.
        """
        content = _current_contract()
        return {"content": content,
                "sha256": repo.content_digest(content),
                "git_commit": (config_store.git_commit_of(settings.config_dir)
                               if settings is not None else None)}

    @app.get("/config/tables")
    def get_config_tables(principal: Principal = viewer) -> dict:
        """The contract as editable tables, each ROW carrying its own evidence.

        **This is the answer to the objection the old config page raised against
        itself** — "a form would show values stripped of the evidence for them".
        Correct, and so evidence is a column: one alias's justification travels with
        that alias and is deleted with it, where a comment block above a line could
        only ever caption the top-level key and would be left describing its
        neighbour when the entry it documented was removed.

        No table name, column name or dotted path is ever rendered — those exist in
        the wire format because the API has to name what is changing. `kind` tells
        the UI which purpose-built control to draw, because a bare text input is the
        wrong affordance for two thirds of this contract.
        """
        if not hasattr(repo, "config_tables_payload"):
            raise HTTPException(501, "this deployment has no configuration tables")
        content = _current_contract()
        return {
            "tables": repo.config_tables_payload(),
            "sha256": repo.content_digest(content),
            "operations": list(config_rows.OPS),
            # A2: whether this deployment can verify a goldens-affecting change at
            # all, answered BEFORE anyone makes one. In a container the answer is
            # always no — no image ships tests/goldens/manifest.json — and the editor
            # used to present the gate as working right up until it silently could
            # not run. Told here rather than discovered afterwards.
            "verification": (
                verification.capability(repo, _repo_root(settings))
                if settings is not None else
                {"can_verify": False, "reason": "no_digests",
                 "detail": "This deployment has no config directory, so no canary "
                           "window can be run and no config change can be checked "
                           "against a known-good workbook."}),
        }

    @app.post("/config/preview")
    def preview_config_edits(req: ProposalRequest,
                             principal: Principal = user) -> dict:
        """The diff a set of edits would produce, committing nothing.

        Exists because the whole justification for a form over a text box is that
        the operator sees the change in the contract's own terms before proposing
        it. It is produced by APPLYING the edits and rolling back, so the preview
        cannot differ from what would be proposed — a simulation would be a second
        implementation of the write, free to disagree with the real one.
        """
        edits = config_rows.parse_all(req.edits)
        before = _rendered_or_refuse()
        after = repo.render_config_after(edits)
        return {
            "diff": config_store.diff(before, after),
            "changed": after != before,
            "summary": config_rows.summarise(edits),
            "invalidates_goldens": repo.config_invalidating(edits),
        }

    def _rendered_or_refuse() -> str:
        rendered = repo.render_config() if hasattr(repo, "render_config") else None
        if rendered is None:
            raise HTTPException(
                503, "the configuration tables are empty, so there is nothing to "
                     "edit yet. Seed them from the committed contract first: "
                     "`python -m service.config_import`.")
        return rendered

    @app.get("/config/versions")
    def config_versions(principal: Principal = viewer) -> dict:
        return {"versions": [payload(v) for v in repo.list_config_versions()]}

    @app.get("/config/versions/{version_id}")
    def config_version(version_id: int, principal: Principal = viewer) -> dict:
        return payload(repo.config_version(version_id))

    @app.get("/config/pins")
    def config_pins(principal: Principal = viewer) -> dict:
        """Which windows are frozen to which config, and the pin/unpin history.

        A pinned window re-runs under the rules it originally ran under, so an
        August rate change cannot alter a re-run of May.

        `events` is the append-only history and is deliberately part of the same
        response rather than a second endpoint: an unpinned window has no `pins` row
        at all, so a caller reading only current state cannot tell "never pinned"
        from "pinned and released" — which is exactly the question the history exists
        to answer (migration `014`).
        """
        return {"pins": [payload(p) for p in repo.list_pins()],
                "events": [payload(e) for e in repo.pin_events()]}

    @app.post("/config/pins", status_code=201)
    def pin_config(req: PinRequest, principal: Principal = admin) -> dict:
        repo.config_version(req.config_version_id)          # 404 if unknown
        return payload(repo.pin_period_config(
            req.platform, req.period, req.config_version_id,
            pinned_by=principal.subject, reason=req.reason))

    @app.delete("/config/pins/{platform}/{period}")
    def unpin_config(platform: str, period: str, req: UnpinRequest,
                     principal: Principal = admin) -> dict:
        """Unpin, so the next run reads today's config again.

        Rare and deliberate: the next re-run of this window may then produce
        different numbers than the run it was invoiced from. **A reason is required**
        — this was a bare delete until 2026-08-19, which left no record that the
        window had ever been pinned (defect 2.5). The reason and the released version
        go to `config_pin_events`, and the actor comes from the session, never the
        body.
        """
        if not repo.unpin_period_config(platform, period,
                                        actor=principal.subject, reason=req.reason):
            raise HTTPException(404, f"{platform} {period} is not pinned")
        return {"platform": platform, "period": period, "pinned": False}

    @app.post("/config/proposals", status_code=201)
    def propose_config(req: ProposalRequest, principal: Principal = user) -> dict:
        """Propose a set of changes. Computes the diff; commits nothing.

        `recon.user` and `recon.admin` propose; `recon.viewer` cannot; only
        `recon.admin` decides. That is the shape of the real disagreement — finance
        owns the rates, engineering owns the file — and it is now a DECISION rather
        than a configurable placeholder: open question 13 is answered
        (docs/11-OPEN-QUESTIONS.md), which closes defect 2.7.

        The edits themselves are stored, not just the resulting contract. That is
        what lets a proposal made against a contract which has since moved be
        REBASED — replayed against the current rows — instead of retyped from
        memory.
        """
        edits = config_rows.parse_all(req.edits)
        before = _rendered_or_refuse()
        after = repo.render_config_after(edits)
        if after == before:
            raise HTTPException(
                422, "that change is already the current value, so there is nothing "
                     "to propose")
        return payload(repo.create_proposal(
            base_sha256=repo.content_digest(before), content=after,
            summary=req.summary, diff=config_store.diff(before, after),
            proposed_by=principal.subject,
            edits=[e.as_json() for e in edits], edit_model="row"))

    @app.get("/config/proposals")
    def list_proposals(state: str | None = None, principal: Principal = viewer) -> dict:
        return {"proposals": [payload(p) for p in repo.list_proposals(state=state)]}

    @app.get("/config/proposals/{proposal_id}")
    def get_proposal(proposal_id: int, principal: Principal = viewer) -> dict:
        return payload(repo.proposal(proposal_id))

    @app.post("/config/proposals/{proposal_id}/approve")
    def approve_proposal(proposal_id: int, req: DecisionRequest,
                         principal: Principal = admin) -> dict:
        proposal = repo.proposal(proposal_id)
        # Self-approval is ALLOWED and recorded, not forbidden. Forbidding it
        # deadlocks a single-admin deployment and pushes the edit back to
        # hand-editing settings.yaml, which has no audit trail at all.
        # `config_proposals.self_approved` is a GENERATED column, so it cannot be
        # set to a convenient value and a reviewer counting them reads a fact.
        try:
            return payload(repo.decide_proposal(
                proposal_id, state="approved", decided_by=principal.subject, note=req.note))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/config/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: int, req: DecisionRequest,
                        principal: Principal = admin) -> dict:
        try:
            return payload(repo.decide_proposal(
                proposal_id, state="rejected", decided_by=principal.subject, note=req.note))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/config/proposals/{proposal_id}/apply")
    def apply_proposal(proposal_id: int, principal: Principal = admin) -> dict:
        """Write an approved change into the config tables.

        Refuses if the contract moved since the proposal was made. A three-way
        merge of a contract whose evidence is part of it would produce something
        nobody wrote and everybody would later have to defend.

        The rows are the source of truth, so this applies the recorded EDITS rather
        than the recorded text: replaying the intent against the rows is what makes
        the tables and the rendered contract the same thing. The rendered result is
        checked against the text the proposal was reviewed as — a difference there
        means the rows moved in a way the concurrency check did not catch, and it
        stops rather than applying something nobody read.
        """
        proposal = repo.proposal(proposal_id)
        if proposal.get("edit_model") != "row":
            raise HTTPException(
                422, f"proposal {proposal_id} was made by an earlier editor that "
                     f"changed the settings file directly. The contract now lives "
                     f"in the configuration tables, so this cannot be applied — "
                     f"make the change again.")
        edits = config_rows.parse_all(list(proposal["edits"] or []))

        current = _rendered_or_refuse()
        if repo.content_digest(current) != proposal["base_sha256"]:
            raise HTTPException(
                409, "the configuration has changed since this proposal was made. "
                     "Withdraw it and propose the change again against the current "
                     "contract — this will not merge a change nobody reviewed.")

        # Measured BEFORE the write: a delete removes the row that carries the
        # flag, and re-reading afterwards would fall back to the table's default
        # for exactly the edit whose answer was most specific.
        invalidating = repo.config_invalidating(edits)

        # `expect` makes the write conditional on producing the text that was
        # reviewed. Checking afterwards would report a conflict for a change that
        # had already landed — the one message that must never be wrong.
        try:
            content = repo.apply_config_rows(
                edits, who=principal.subject, expect=proposal["content"])
        except ProposalConflict as exc:                             # pragma: no cover
            raise HTTPException(409, str(exc)) from exc

        # Still written to disk and committed where a git checkout exists. That is
        # not how the change reaches the worker any more — the tables are — but it
        # is what keeps `config/settings.yaml` a usable seed and keeps the CLI and
        # the golden gate runnable with the service switched off (D24). In a
        # container there is no `.git` and this quietly writes only the file; the
        # database row is the audit record that matters (C11 decides the rest).
        commit = None
        if settings is not None:
            commit = config_store.write_and_commit(
                settings.config_dir, content,
                message=f"config: {proposal['summary']}\n\n"
                        f"Proposed by {proposal['proposed_by']}, approved by "
                        f"{principal.subject}.\n"
                        f"Applied through the recon config editor "
                        f"(proposal {proposal_id}).",
                author=principal.subject)
        version = repo.record_config_version(
            content, source="rendered", git_commit=commit,
            created_by=principal.subject)

        try:
            applied = repo.mark_proposal_applied(proposal_id, version["id"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

        # Did that actually move a number? Measured, not assumed — the whole point
        # of the D26 replacement. Runs a canary window under the NEW config and
        # compares it to a committed golden at zero tolerance. Nothing is blocked:
        # the change has already landed, and this reports what it did.
        verdict = verification.Verdict(state=verification.State.NOT_APPLICABLE)
        try:
            if settings is None:
                raise RuntimeError("this deployment has no config directory, so no "
                                   "canary window can be run")
            verdict = verification.verify(
                repo, settings, settings_text=content,
                # Read off the rows, not inferred from a path. An empty list means
                # every row touched DECLARES that it cannot move a cell — a claim
                # the row makes, which is the point of migration 008.
                invalidating=invalidating,
                root=_repo_root(settings))
            repo.record_config_verification(version["id"], verdict)
        except Exception as exc:                                    # noqa: BLE001
            # A verification failure must never undo an applied change. The change
            # is in the tables by now; claiming success is the one unacceptable
            # outcome, so the failure is RECORDED and returned.
            verdict = verification.Verdict(
                state=verification.State.FAILED,
                detail=f"{type(exc).__name__}: {exc}")
            try:
                repo.record_config_verification(version["id"], verdict)
            except Exception:                                       # pragma: no cover
                pass

        return {**payload(applied), "git_commit": commit,
                "config_version_id": version["id"],
                "committed": commit is not None,
                # D60 (C11): the DATABASE is the config audit record — a
                # content-addressed, append-only version row naming who applied
                # what. Git is the reviewable convenience of a developer checkout;
                # a container has no `.git` by design, and that absence is stated
                # here rather than left as an unexplained `committed: false`.
                "audit_record": f"config_versions #{version['id']} "
                                f"(sha256 {version['sha256'][:12]})",
                "git_note": ("" if commit is not None else
                             "no git checkout in this deployment; the database "
                             "version above is the audit record (D60)"),
                "verification": {"state": verdict.state, "window": verdict.window,
                                 "cells_moved": verdict.cells_moved,
                                 "strong": verdict.strong,
                                 "message": verdict.message()}}

    @app.post("/config/proposals/{proposal_id}/rebase", status_code=201)
    def rebase_proposal(proposal_id: int, principal: Principal = user) -> dict:
        """Replay a stale proposal's edits against the current file.

        **Not a merge.** [D38](docs/06-DECISIONS.md#d38) refuses a three-way merge of
        a contract whose evidence is part of it, and it is right: a merge produces
        something nobody wrote and everybody would later have to defend. This re-runs
        the stated INTENT and produces a fresh diff for a fresh review — which is
        only possible because `config_proposals.edits` records what was asked for
        rather than only what the contract became.
        """
        stale = repo.proposal(proposal_id)
        if not stale.get("edits"):
            raise HTTPException(
                422, f"proposal {proposal_id} records only its resulting file, not "
                     f"the edits that produced it. There is nothing to replay — "
                     f"propose the change again.")
        if stale.get("edit_model") != "row":
            raise HTTPException(
                422, f"proposal {proposal_id} was made by an earlier editor that "
                     f"changed the settings file directly. Its operations do not "
                     f"mean anything against the configuration tables, so it cannot "
                     f"be replayed — make the change again.")
        if (stale["proposed_by"] != principal.subject
                and not principal.can(Role.ADMIN)):
            raise Forbidden(f"proposal {proposal_id} belongs to {stale['proposed_by']}")

        edits = config_rows.parse_all(list(stale["edits"]))
        before = _rendered_or_refuse()
        after = repo.render_config_after(edits)
        if after == before:
            raise HTTPException(
                422, "replaying those edits against the current contract changes "
                     "nothing — the change has already been made another way.")
        created = repo.create_proposal(
            base_sha256=repo.content_digest(before), content=after,
            summary=f"{stale['summary']} (rebased from proposal {proposal_id})",
            diff=config_store.diff(before, after), proposed_by=principal.subject,
            edits=list(stale["edits"]), rebased_from=proposal_id, edit_model="row")
        return payload(created)

    @app.post("/config/proposals/{proposal_id}/withdraw")
    def withdraw_proposal(proposal_id: int, principal: Principal = user) -> dict:
        """Withdraw your own proposal. An admin may withdraw anyone's.

        The authorship check is here rather than in the route's role because the
        role cannot express "the person who made it". Before M6 this was
        role-gated only, so ANY operator could withdraw anyone's pending change —
        a real hole, and one that reads as intentional once it has shipped twice.
        """
        existing = repo.proposal(proposal_id)
        if (existing["proposed_by"] != principal.subject
                and not principal.can(Role.ADMIN)):
            raise Forbidden(
                f"proposal {proposal_id} belongs to {existing['proposed_by']}")
        try:
            return payload(repo.withdraw_proposal(proposal_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return app


def seed_config_tables(repo, settings: ServiceSettings) -> int:
    """Seed the config tables from `config/settings.yaml`, if they are empty.

    A deployment's first boot. Since 1.6 the tables ARE the editable contract, so a
    deployment that has never run `python -m service.config_import` would have an
    editor with nothing in it — and `resolve_for_window` would go on falling back to
    each container's own baked copy of `config/`, which is defect A1 exactly.
    Seeding here makes the committed file do what it says it does: be the seed.

    Only ever runs against empty tables. It is not a re-import and it never
    overwrites edited rows — the file in the image is older than any edit made
    through the browser, and losing an applied change on a restart is the failure
    this whole phase exists to remove.
    """
    from . import config_import

    if not hasattr(repo, "render_config") or repo.render_config() is not None:
        return 0
    with repo._conn() as conn:                                      # noqa: SLF001
        counts = config_import.import_settings(
            conn, settings.config_dir, changed_by="seed", source="seed")
        conn.commit()
    return sum(counts.values())


def _repo_root(settings: ServiceSettings) -> Path:
    """Where `tests/goldens/manifest.json` would be, if this deployment has one.

    Derived from `config_dir` rather than from this module's own location, because
    `config_dir` is what a test points at a sandbox — and a verification run that
    read the REAL manifest while editing a sandboxed config would compare the wrong
    two things. Absent (a container ships no `tests/`) is the `unavailable` state,
    which is reported rather than guessed at.
    """
    return Path(settings.config_dir).parent


def _settings_dict(settings: ServiceSettings) -> dict:
    """The pipeline's settings dict, for the upload sanitizer's column maps.

    Read fresh rather than cached: an operator who has just applied a column-map
    change through the editor expects the very next upload to use it.
    """
    from src import config as src_config
    return src_config.load_settings(settings.config_dir)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_app() -> FastAPI:
    from . import db
    from .artifacts import build_artifact_store
    from .repository_m5 import M5Repository

    settings = ServiceSettings.from_env()
    settings.check_safe_to_serve()
    pool = db.make_pool(settings.database_url)
    with pool.connection() as conn:
        db.migrate(conn)
    repo = M5Repository(pool)
    # First boot: the committed contract becomes rows, because since M8/1.6 the
    # rows are what is edited and what an unpinned run renders from. A no-op on
    # every boot after the first.
    seed_config_tables(repo, settings)
    # `build_artifact_store`, not a literal choice here: the worker calls the same
    # function, so the two cannot end up writing and reading different stores —
    # which is exactly the failure the shared-volume assumption produced.
    return create_app(repo, build_artifact_store(settings), settings=settings,
                      policy=AuthPolicy(enabled=settings.auth_enabled))


def main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    # C5: one JSON object per line on stdout, uvicorn's access/error lines
    # included, so a collector sees the api in the same shape as the worker.
    from .obs import setup_logging
    setup_logging("api")

    settings = ServiceSettings.from_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=settings.api_host)
    ap.add_argument("--port", type=int, default=settings.api_port)
    args = ap.parse_args(argv)

    from dataclasses import replace
    # Re-check against the host actually being bound, not just the configured
    # one: `--host 0.0.0.0` with auth off must fail here too, or the guard is
    # only as good as the environment variable.
    replace(settings, api_host=args.host).check_safe_to_serve()

    uvicorn.run(build_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
