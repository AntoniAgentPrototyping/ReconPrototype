-- M6 — password authentication. Replaces bearer tokens (002_m5.sql).
--
-- TWO KINDS OF SECRET, TWO KINDS OF HASH, and the difference is the whole reason
-- this comment exists. 002_m5.sql:14-18 argues that sha256 is the RIGHT choice for
-- api_tokens, and it is still right for what it described. But it is right for a
-- reason that a password does not share, and both hashes now live in this schema:
--
--   users.password_hash          Argon2id. A password is CHOSEN BY A HUMAN, so its
--                                entropy is low and guessing IS on the table. A
--                                slow, memory-hard KDF is the only thing standing
--                                between a leaked table and every account.
--
--   user_sessions.token_sha256   sha256, for exactly the reason 002_m5.sql gave: a
--                                session token is 32 bytes from os.urandom, never
--                                user-chosen, and a slow KDF on the credential
--                                presented with EVERY request would add latency for
--                                no defence. That premise still holds here.
--
-- The rule 002 depended on — "tokens are GENERATED, never user-chosen" — is exactly
-- what a password violates. Do not reach for one file's answer while holding the
-- other file's secret.
--
-- SUPERSEDES, in 002_m5.sql, which cannot be edited (service/db.py:68-73 raises if
-- an applied file's sha256 changes):
--   * the api_tokens table and its comment block at 002_m5.sql:14-43;
--   * the three-value role check `('recon.viewer','recon.operator','recon.admin')`.
--     The tiers are unchanged; `recon.operator` is renamed `recon.user` because the
--     app now says "user" everywhere a person is meant. Read-only `recon.viewer`
--     stays: a finance reviewer or auditor who should read the invoicing numbers
--     and never queue a run is a real person, not a hypothetical.

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------

create table if not exists users (
    id                bigserial   primary key,

    -- The login name AND the subject that lands in jobs.requested_by. Stored
    -- normalized (NFKC, casefolded, trimmed) by service/passwords.py so that
    -- "Antoni@ADA" and "antoni@ada" are one account and one audit identity — an
    -- audit trail whose names differ by how someone held Shift is not an audit
    -- trail. This check is a BACKSTOP against a write that skipped that module,
    -- not the normalization itself.
    username          text        not null unique
                                  check (username = lower(username)
                                         and username = btrim(username)
                                         and length(username) between 3 and 200
                                         and position(' ' in username) = 0),
    display_name      text,

    -- Argon2id PHC string: "$argon2id$v=19$m=19456,t=2,p=1$<salt>$<hash>".
    -- The parameters live INSIDE the value, which is what lets a login detect a
    -- hash made under weaker settings and upgrade it in place.
    password_hash     text        not null,

    role              text        not null
                                  check (role in ('recon.viewer', 'recon.user',
                                                  'recon.admin')),

    -- A bootstrap or admin-reset password is known to somebody other than its
    -- owner, so it must not survive as a working credential. While this is true the
    -- api refuses every route except GET /me, POST /me/password and sign-out.
    must_change_password boolean  not null default false,
    password_changed_at  timestamptz not null default now(),

    -- Disabled, never deleted, and there is deliberately no DELETE route.
    -- requested_by / proposed_by / uploaded_by are free text and must keep
    -- resolving to a name a human recognises; a DELETE would leave the audit trail
    -- pointing at nobody.
    disabled_at       timestamptz,
    disabled_by       text,

    created_at        timestamptz not null default now(),
    created_by        text,
    last_login_at     timestamptz,

    -- Throttling state, NOT a permanent lockout. A permanent lock is a denial of
    -- service anyone can trigger against a known username, and the month a
    -- settlement window has to close is exactly when that costs the most. It has a
    -- worse mode too: lock out the only admin and account management is bricked
    -- until someone with the database URL intervenes. So: a short cool-off that
    -- expires on its own. The real brute-force defence is Argon2id's cost; this
    -- column exists so that a slow attack is also a visible one.
    failed_attempts   int         not null default 0 check (failed_attempts >= 0),
    last_failed_at    timestamptz,
    locked_until      timestamptz
);

-- ---------------------------------------------------------------------------
-- user_sessions
-- ---------------------------------------------------------------------------

create table if not exists user_sessions (
    id                  bigserial   primary key,

    -- The role is NOT copied onto this row, deliberately. 002_m5.sql checked
    -- revocation and expiry in the WHERE clause so that revoking took effect on the
    -- very next request rather than whenever a cache felt like it. A role stamped
    -- here would reintroduce precisely that staleness: a demoted admin would keep
    -- admin until they signed out. Resolve by join; the table is small and the join
    -- is free.
    user_id             bigint      not null references users (id) on delete cascade,

    token_sha256        text        not null unique,

    created_at          timestamptz not null default now(),
    -- Idle timeout is (now() - last_seen_at). Bumped on each authenticated request,
    -- coalesced to at most once a minute so a 1 Hz run-log poll does not write a row
    -- per second.
    last_seen_at        timestamptz not null default now(),
    -- Absolute timeout. An idle timeout ALONE means a stolen token that is being
    -- actively used never expires.
    absolute_expires_at timestamptz not null,

    revoked_at          timestamptz,
    revoked_reason      text        check (revoked_reason in
                                    ('signout', 'password_change', 'disabled',
                                     'role_change', 'admin')),

    -- Recorded so "sign this person out everywhere" can be reviewed afterwards, and
    -- so an admin looking at a suspicious session has something to look at.
    user_agent          text,
    client_ip           inet
);

create index if not exists user_sessions_live on user_sessions (user_id)
    where revoked_at is null;

-- ---------------------------------------------------------------------------
-- Drop the token path
-- ---------------------------------------------------------------------------

-- Bearer tokens are gone rather than kept alongside passwords. Nothing
-- machine-shaped consumed the api: the worker talks to Postgres directly
-- (service/worker.py) and the web BFF now holds a session. A second live door into
-- a service that can rewrite the config the money math uses, kept only in case
-- something might want it later, is an unmonitored door.
--
-- When a headless caller genuinely appears it should come back as `service_accounts`
-- with a NOT NULL expiry and no admin role — both things api_tokens did not enforce.
drop table if exists api_tokens;
