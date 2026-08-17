"""Service configuration — environment only, and deliberately not YAML.

`config/settings.yaml` is the *domain* contract: column maps, rosters,
tolerances, VAT. Its in-line comments are the audit trail (docs/06-DECISIONS.md#d2)
and it stays git-backed and canonical. None of that describes where a database
lives, so none of it belongs here.

Deployment facts — a connection string, a scratch directory, a lease length —
are environment variables instead: they differ per machine, they contain
credentials, and they must never be committed next to an audit trail.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

# The repo root, found from this file. `service/` sits one level below it, and
# tests/test_io_boundary.py checks that this depth matches the file's own
# nesting — M2.5 lost an afternoon to a `parents[N]` that stopped matching after
# a file moved.
ROOT = Path(__file__).resolve().parents[1]

ENV_PREFIX = "RECON_"


class ConfigError(RuntimeError):
    """A deployment fact is missing or unusable. Never a data problem."""


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(ENV_PREFIX + name, default)


@dataclass(frozen=True)
class S3Settings:
    """Where uploaded exports and finished artifacts live, when they live in a
    bucket rather than on a volume.

    Present only when `RECON_S3_ENDPOINT` is set. Absence is a *supported* mode,
    not a broken one: a single-machine deployment keeps objects in a directory,
    which is what lets the whole upload → run → download path be exercised
    without a container (service/objects.py).

    Two buckets rather than two prefixes in one. They have opposite retention:
    uploads are raw client exports that must expire, artifacts are the deliverable
    the team invoiced from and must not. A lifecycle rule is per-bucket, so one
    bucket would mean either keeping PII forever or deleting the evidence.
    """

    endpoint_url: str
    access_key: str
    secret_key: str
    uploads_bucket: str = "recon-uploads"
    artifacts_bucket: str = "recon-artifacts"
    region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> "S3Settings | None":
        endpoint = _env("S3_ENDPOINT")
        if not endpoint:
            return None
        access, secret = _env("S3_ACCESS_KEY"), _env("S3_SECRET_KEY")
        if not access or not secret:
            raise ConfigError(
                f"{ENV_PREFIX}S3_ENDPOINT is set but {ENV_PREFIX}S3_ACCESS_KEY / "
                f"{ENV_PREFIX}S3_SECRET_KEY are not. Refusing to fall back to the "
                f"local-directory mode: that would silently write client exports "
                f"somewhere other than where this deployment expects to find them.")
        return cls(
            endpoint_url=endpoint, access_key=access, secret_key=secret,
            uploads_bucket=_env("S3_UPLOADS_BUCKET", "recon-uploads") or "recon-uploads",
            artifacts_bucket=(_env("S3_ARTIFACTS_BUCKET", "recon-artifacts")
                              or "recon-artifacts"),
            region=_env("S3_REGION", "us-east-1") or "us-east-1")


@dataclass(frozen=True)
class ServiceSettings:
    """Everything the api and the worker need to know about their surroundings.

    Frozen, and passed explicitly to `Repository` / `Worker` / `create_app`, for
    the same reason `RunContext` is: a module-level singleton read at import
    time cannot be pointed at a temporary database by a test.
    """

    database_url: str

    # Where config/ and input/ live. The worker reads staged exports from
    # input_root and the team-owned masters from config_dir — both are inputs it
    # shares with the CLI, because a divergence there would silently change
    # numbers (see pipeline.build_context).
    config_dir: Path
    input_root: Path

    # Where finished artifacts are kept, and where a run writes them first.
    # These are separate because the second is throwaway and the first is not:
    # the worker runs into scratch, then hands the files to the artifact store.
    artifact_root: Path
    scratch_root: Path

    worker_id: str

    # A lease must outlast the longest SILENT stretch of a run, not the run.
    # QueueRunLog extends it on every flush, and the quietest measured stage is
    # openpyxl workbook materialization at 30-39s (docs/10-ROADMAP.md). 900s is
    # ~20x that, which is slack for a bad month rather than a guess.
    lease_seconds: int = 900

    poll_interval_s: float = 2.0

    # Log flush cadence. The point of flushing mid-run is that an operator can
    # watch a 171-second run progress; a run whose log lands only at the end is
    # a batch job with extra steps.
    log_flush_lines: int = 25
    log_flush_seconds: float = 1.0

    api_host: str = "127.0.0.1"
    api_port: int = 8080

    # -- M5 ------------------------------------------------------------------

    # Authentication is ON unless explicitly disabled. The inversion matters: an
    # opt-IN flag means every forgotten environment is unauthenticated, which is
    # exactly the defect this milestone exists to close.
    auth_enabled: bool = True

    # NOT here any more: `config_approval`. It existed only because open question
    # 13 — who owns configuration and who signs off a rate change — was unanswered,
    # so the policy was made configurable rather than assumed. It is answered
    # (docs/11-OPEN-QUESTIONS.md #13, closing defect 2.7): `recon.user` and
    # `recon.admin` propose, `recon.viewer` cannot, only `recon.admin` decides, and
    # self-approval is permitted and RECORDED rather than forbidden. A deployment
    # cannot weaken that by setting an environment variable.

    # -- sessions (M6) ------------------------------------------------------
    #
    # A session credential that can queue settlement runs and read every store's
    # revenue should not sit live in a cookie all week.
    #
    # 60 minutes idle is short, and is affordable because the run page's own log
    # polling refreshes it: an operator watching a 171-second run never sees a
    # logout, while someone who walked away at 5pm is signed out.
    session_idle_minutes: int = 60
    # 12 hours absolute, matching the cookie lifetime the BFF already used. An idle
    # timeout ALONE means a stolen token that is being actively used never expires.
    session_absolute_hours: int = 12
    # Failed sign-ins per username inside a five-minute window before a 15-minute
    # cool-off. NOT a lockout an admin has to clear — see the comment on
    # users.locked_until in migrations/003_password_auth.sql.
    login_attempts: int = 10

    # NOT here, deliberately: the argon2 parameters. They are module constants in
    # service/passwords.py, because an environment that lowered the cost would not
    # merely weaken new hashes — `check_needs_rehash` would then classify the
    # strong existing ones as stale and DOWNGRADE them on their owners' next login.

    # Uploaded exports are quarantined here before they are staged. Separate
    # from input_root on purpose — a file that has not been sanitized and
    # classified must not be visible to a run.
    upload_root: Path | None = None

    # None means "objects are files under upload_root / artifact_root". Set,
    # they live in buckets and neither container needs a shared volume. See
    # S3Settings above and service/objects.py.
    s3: S3Settings | None = None

    # A hard cap on how many rows of one exception sheet reach the database.
    # Never silent: run_exception_sheets records total vs stored.
    exception_row_cap: int = 500

    @property
    def loopback_only(self) -> bool:
        return self.api_host in ("127.0.0.1", "localhost", "::1")

    def check_safe_to_serve(self) -> None:
        """Refuse the one combination that is never a mistake worth allowing.

        Binding a routable interface with authentication off publishes an api
        that can queue settlement runs, read every store's revenue and rewrite
        the config the money math uses. A warning would be the wrong response —
        warnings are for things you might legitimately want.
        """
        if not self.auth_enabled and not self.loopback_only:
            raise ConfigError(
                f"refusing to bind {self.api_host} with authentication disabled. "
                f"Either drop {ENV_PREFIX}AUTH_DISABLED, or bind 127.0.0.1. "
                f"An unauthenticated api on a routable address can queue runs, "
                f"read client revenue and rewrite config.")

    @classmethod
    def from_env(cls, *, root: Path | None = None) -> "ServiceSettings":
        root = Path(root) if root is not None else ROOT
        url = _env("DATABASE_URL")
        if not url:
            raise ConfigError(
                f"{ENV_PREFIX}DATABASE_URL is not set. The api and the worker both "
                f"need it; there is no default, because a default would silently "
                f"point production at a developer's database.")

        def path_of(name: str, fallback: Path) -> Path:
            raw = _env(name)
            return Path(raw).expanduser() if raw else fallback

        # RECON_CONFIG_APPROVAL is refused rather than ignored. A deployment that
        # set it was expressing an intent about who may approve a rate change, and
        # silently dropping that on upgrade would be the worst kind of quiet.
        if _env("CONFIG_APPROVAL") is not None:
            raise ConfigError(
                f"{ENV_PREFIX}CONFIG_APPROVAL no longer exists. The approval model is "
                f"fixed as of M6: user or admin proposes, only admin approves, and "
                f"self-approval is recorded rather than forbidden "
                f"(docs/11-OPEN-QUESTIONS.md #13). Unset the variable.")

        settings = cls(
            database_url=url,
            config_dir=path_of("CONFIG_DIR", root / "config"),
            input_root=path_of("INPUT_ROOT", root / "input"),
            artifact_root=path_of("ARTIFACT_ROOT", root / "artifacts"),
            scratch_root=path_of("SCRATCH_ROOT", root / ".scratch"),
            upload_root=path_of("UPLOAD_ROOT", root / ".uploads"),
            worker_id=_env("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}",
            lease_seconds=int(_env("LEASE_SECONDS", "900") or 900),
            poll_interval_s=float(_env("POLL_INTERVAL_S", "2.0") or 2.0),
            log_flush_lines=int(_env("LOG_FLUSH_LINES", "25") or 25),
            log_flush_seconds=float(_env("LOG_FLUSH_SECONDS", "1.0") or 1.0),
            api_host=_env("API_HOST", "127.0.0.1") or "127.0.0.1",
            api_port=int(_env("API_PORT", "8080") or 8080),
            # Presence, not truthiness: RECON_AUTH_DISABLED=false disabling auth
            # would be a trap, so any value at all counts and the variable's name
            # is the whole statement.
            auth_enabled=_env("AUTH_DISABLED") is None,
            session_idle_minutes=int(_env("SESSION_IDLE_MINUTES", "60") or 60),
            session_absolute_hours=int(_env("SESSION_ABSOLUTE_HOURS", "12") or 12),
            login_attempts=int(_env("LOGIN_ATTEMPTS", "10") or 10),
            exception_row_cap=int(_env("EXCEPTION_ROW_CAP", "500") or 500),
            s3=S3Settings.from_env(),
        )
        settings.check_safe_to_serve()
        return settings
