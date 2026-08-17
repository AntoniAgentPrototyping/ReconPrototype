"""Operator CLI for the service — and the answer to the bootstrap problem.

    python -m service.admin user create --username antoni@ada --role admin
    python -m service.admin user list
    python -m service.admin user reset-password --username antoni@ada
    python -m service.admin user disable --username someone@ada
    python -m service.admin user enable  --username someone@ada
    python -m service.admin config pins
    python -m service.admin config unpin --platform lazada --period 2026-05_l1

`POST /users` requires an admin session, so the first account cannot come from the
api. It comes from here instead, which requires the database URL — i.e. from
someone who already has the deployment's credentials. That is the right shape:
creating the first identity should need *more* access than using it, not a special
unauthenticated endpoint that then exists forever.

**It is not a backdoor, because it is not an API surface.** Nothing on the network
can reach it, there is no unauthenticated endpoint to forget to remove, and no code
path that stops being needed and starts being a liability. Anyone holding
RECON_DATABASE_URL could already `insert into users` by hand; this just does the
argon2 hashing correctly.

**Do not delete this once the admin UI exists**, even though it will look
redundant. `user reset-password` is the only way back in when the sole admin has
forgotten their password or throttled themselves out. That cleanup is otherwise
obviously correct, which is exactly why the warning is here.

Rejected alternatives, both worse:

* An env-var seeded admin on migrate. Puts a working admin password in
  `deploy/.env` in plaintext forever, and re-seeding each boot either fights the
  user's own password change or needs a guard flag. This repo already documents how
  such a variable outlives its purpose — RECON_AUTH_DISABLED is checked for
  *presence, not truthiness* precisely because leftover env vars are the observed
  failure mode (service/config.py).
* A first-run `/setup` page. Its correctness depends on `count(*) from users = 0`,
  so there is a window between `docker compose up` and the operator's first browser
  tab in which whoever reaches the app becomes admin. That window cannot be closed
  from inside the process, and an endpoint that is only safe because of a race is
  not safe.
"""

from __future__ import annotations

import argparse
import sys

from . import db, passwords
from .auth import Role
from .config import ServiceSettings
from .repository_m5 import M5Repository

ROLE_ALIASES = {"viewer": Role.VIEWER, "user": Role.USER, "admin": Role.ADMIN}


def _repo(settings: ServiceSettings) -> tuple[M5Repository, object]:
    pool = db.make_pool(settings.database_url, min_size=1, max_size=2)
    with pool.connection() as conn:
        db.migrate(conn)
    return M5Repository(pool), pool


def _role(value: str) -> Role:
    if value in ROLE_ALIASES:
        return ROLE_ALIASES[value]
    return Role(value)


def cmd_user_create(repo: M5Repository, args) -> int:
    username = passwords.normalize_username(args.username)
    role = _role(args.role)
    raw = passwords.generate_password()
    record = repo.create_user(
        username=username,
        password_hash=passwords.hash_password(raw, username=username),
        role=role, display_name=args.display_name, created_by="service.admin",
        must_change_password=True)

    # Creating a SECOND admin is legitimate; doing it by accident is not.
    admins = repo.count_active_admins()
    if role is Role.ADMIN and admins > 1:
        print(f"note: this is now one of {admins} enabled admins.")

    print(f"user {record.id} created: {record.username} ({record.role.value})")
    print()
    print(raw)
    print()
    print("Copy it now — only its argon2 hash is stored, so it cannot be shown again.")
    print("They must change it at first sign-in; until they do, every route except")
    print("/me and the change-password form refuses.")
    return 0


def cmd_user_list(repo: M5Repository, args) -> int:
    users = repo.list_users()
    if not users:
        print("no users. Create one with: python -m service.admin user create ...")
        return 0
    print(f"{'id':>4}  {'role':<15} {'username':<28} {'last login':<20} status")
    for u in users:
        last = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "never"
        flags = []
        if not u.enabled:
            flags.append("DISABLED")
        if u.must_change_password:
            flags.append("must change password")
        print(f"{u.id:>4}  {u.role.value:<15} {u.username:<28} {last:<20} "
              f"{', '.join(flags)}")
    return 0


def cmd_user_reset_password(repo: M5Repository, args) -> int:
    """The break-glass path. This is why the CLI cannot be deleted."""
    username = passwords.normalize_username(args.username)
    record = repo.user_by_username(username)
    raw = passwords.generate_password()
    repo.touch_password(record.id,
                        passwords.hash_password(raw, username=username),
                        must_change_password=True)
    revoked = repo.revoke_sessions_for_user(record.id, reason="password_change")
    print(f"reset password for {record.username}; {revoked} session(s) signed out.")
    print()
    print(raw)
    print()
    print("Copy it now. They must change it at first sign-in.")
    return 0


def cmd_user_disable(repo: M5Repository, args) -> int:
    username = passwords.normalize_username(args.username)
    record = repo.user_by_username(username)
    repo.set_user_disabled(record.id, disabled=True, by="service.admin")
    revoked = repo.revoke_sessions_for_user(record.id, reason="disabled")
    print(f"disabled {record.username}; {revoked} session(s) signed out. The account "
          f"is kept, not deleted, so the audit trail still resolves to a name.")
    return 0


def cmd_user_enable(repo: M5Repository, args) -> int:
    username = passwords.normalize_username(args.username)
    record = repo.user_by_username(username)
    repo.set_user_disabled(record.id, disabled=False, by="service.admin")
    print(f"enabled {record.username}. Their previous password still works.")
    return 0


def cmd_config_pins(repo: M5Repository, args) -> int:
    pins = repo.list_pins()
    if not pins:
        print("no windows are pinned. A window is pinned by its first run that "
              "produces a workbook.")
        return 0
    print(f"{'platform':<10} {'period':<16} {'version':>7}  {'config':<14} pinned by")
    for p in pins:
        print(f"{p['platform']:<10} {p['period']:<16} {p['config_version_id']:>7}  "
              f"{p['sha256'][:12]:<14} {p['pinned_by'] or ''}")
    return 0


def cmd_config_unpin(repo: M5Repository, args) -> int:
    if not repo.unpin_period_config(args.platform, args.period):
        print(f"{args.platform} {args.period} is not pinned", file=sys.stderr)
        return 1
    print(f"unpinned {args.platform} {args.period}.")
    print("WARNING: the next run of this window will read today's config, so it may "
          "produce different numbers than the run it was invoiced from.")
    return 0


def cmd_config_versions(repo: M5Repository, args) -> int:
    for v in repo.list_config_versions():
        commit = (v["git_commit"] or "")[:12]
        print(f"{v['id']:>4}  {v['sha256'][:12]}  {v['source']:<9} {commit:<14} "
              f"{v['created_at']:%Y-%m-%d %H:%M}  {v['created_by'] or ''}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="service.admin", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="group", required=True)

    user = sub.add_parser("user", help="create, list, reset and disable accounts")
    usub = user.add_subparsers(dest="action", required=True)

    create = usub.add_parser("create", help="create an account (password shown once)")
    create.add_argument("--username", required=True,
                        help="the login name; also lands in jobs.requested_by")
    create.add_argument("--role", required=True, choices=sorted(ROLE_ALIASES))
    create.add_argument("--display-name", default=None)
    create.set_defaults(func=cmd_user_create)

    listing = usub.add_parser("list", help="list accounts")
    listing.set_defaults(func=cmd_user_list)

    reset = usub.add_parser("reset-password",
                            help="generate a new password (break-glass)")
    reset.add_argument("--username", required=True)
    reset.set_defaults(func=cmd_user_reset_password)

    dis = usub.add_parser("disable", help="disable an account and sign it out")
    dis.add_argument("--username", required=True)
    dis.set_defaults(func=cmd_user_disable)

    ena = usub.add_parser("enable", help="re-enable a disabled account")
    ena.add_argument("--username", required=True)
    ena.set_defaults(func=cmd_user_enable)

    config = sub.add_parser("config", help="inspect config versions and pins")
    csub = config.add_subparsers(dest="action", required=True)
    csub.add_parser("pins", help="which windows are frozen to which config").set_defaults(
        func=cmd_config_pins)
    csub.add_parser("versions", help="recorded config versions").set_defaults(
        func=cmd_config_versions)

    unpin = csub.add_parser("unpin", help="let a window read today's config again")
    unpin.add_argument("--platform", required=True, choices=["tiktok", "shopee", "lazada"])
    unpin.add_argument("--period", required=True)
    unpin.set_defaults(func=cmd_config_unpin)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = ServiceSettings.from_env()
    repo, pool = _repo(settings)
    try:
        return args.func(repo, args)
    finally:
        pool.close()


if __name__ == "__main__":
    sys.exit(main())
