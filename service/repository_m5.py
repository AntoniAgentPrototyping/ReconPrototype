"""M5's SQL: config versions, proposals, uploads, exceptions.

A second module rather than 400 more lines in `repository.py`, because those two
files answer different questions. `repository.py` is the queue — its correctness
is one `FOR UPDATE SKIP LOCKED` statement and it is worth being able to read in
one sitting. This is the record-keeping around it.

The chain is linear so callers still hold exactly one object:

    Repository  ->  IdentityRepository  ->  M5Repository

`repository_identity.py` (M6) owns users and sessions; it slotted in the middle
rather than at the end because identity is what everything else records. The
alternative — separate repositories threaded through the api — buys nothing except
an argument at every call site about which one to use.

The token methods that used to live here went with `api_tokens` in M6's
`003_password_auth.sql`. `principal_for_digest` was renamed
`principal_for_session` and moved to `repository_identity.py`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .repository import NotFound
from .repository_identity import IdentityRepository


class ProposalConflict(RuntimeError):
    """The config changed under an editor's feet.

    Refused rather than merged: `settings.yaml`'s comments are evidence
    (docs/06-DECISIONS.md#d2), and a three-way merge of an evidence trail is a
    way to produce a file nobody wrote.
    """


class M5Repository(IdentityRepository):

    # -- config versions ---------------------------------------------------

    @staticmethod
    def content_digest(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def render_config(self) -> str | None:
        """The contract as text, generated from the config_* tables.

        `None` when the tables are empty, which is how a fresh deployment says
        "seed me from the file". Every other caller treats a rendered string as
        authoritative: it is what makes an edit applied through the api reach the
        WORKER, which is a separate process with its own copy of `config/` baked
        into its image (docs/14-PRODUCTION-READINESS.md A1).
        """
        from . import config_render
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select count(*) from config_scalars")
                if not cur.fetchone()[0]:
                    return None
            return config_render.render(conn)

    # -- config as rows (M8/1.6) -------------------------------------------
    #
    # The tables are the editable working set; `config_versions` stays the
    # immutable archive and a run is still pinned to a whole rendered file. What
    # changed in 1.6 is that an EDIT is a row operation rather than a dotted path
    # into text, so per-entry evidence has somewhere to live and "can this move a
    # cell" is read off the row instead of inferred (docs/14, D6, A12).

    def config_tables_payload(self) -> list[dict]:
        """Every table, its columns and its rows. The editor's whole input."""
        from . import config_rows
        with self._conn() as conn:
            return config_rows.payload(conn)

    def config_rows(self, table: str) -> list[dict]:
        from . import config_rows as rows
        with self._conn() as conn:
            return rows.read_rows(conn, table)

    def render_config_after(self, edits: list) -> str:
        """What the contract would read as after these edits. Writes nothing."""
        from . import config_rows
        with self._conn() as conn:
            return config_rows.render_after(conn, edits, who="preview")

    def config_invalidating(self, edits: list) -> list[str]:
        from . import config_rows
        with self._conn() as conn:
            return config_rows.invalidating(conn, edits)

    def apply_config_rows(self, edits: list, *, who: str, source: str = "proposal",
                          expect: str | None = None) -> str:
        """Apply the edits and return the rendered contract. One transaction.

        Applying and rendering together is not tidiness: the text recorded as the
        config version has to be the text these rows produce, and a second
        connection could observe a different row set. A refusal part-way leaves the
        tables on the previous contract rather than half-applied.

        `expect` is the text the proposal was REVIEWED as. If replaying the edits
        produces anything else, the transaction is rolled back and nothing is
        applied — checking after the commit would report a conflict for a change
        that had already landed, which is the one message that must never be wrong.
        """
        import psycopg

        from . import config_render, config_rows

        rendered: str | None = None
        with self._conn() as conn:
            try:
                with conn.transaction() as tx:
                    config_rows.apply(conn, edits, who=who, source=source)
                    rendered = config_render.render(conn)
                    if expect is not None and rendered != expect:
                        rendered = None
                        raise psycopg.Rollback(tx)
            except psycopg.Rollback:
                pass
        if rendered is None:
            raise ProposalConflict(
                "replaying these edits produced a contract different from the one "
                "that was reviewed. Nothing has been applied; withdraw the proposal "
                "and make the change again against the current contract.")
        return rendered

    def record_config_version(self, content: str, *, source: str = "disk",
                              git_commit: str | None = None,
                              created_by: str | None = None) -> dict:
        """Store this config text, or return the existing row for it.

        Content-addressed, so a hundred runs under an unchanged config produce
        one row and `runs.config_version_id` still identifies exactly what each
        of them ran under.
        """
        digest = self.content_digest(content)
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into config_versions (sha256, content, source, git_commit, created_by)
                values (%s, %s, %s, %s, %s)
                on conflict (sha256) do update set sha256 = excluded.sha256
                returning *
            """, (digest, content, source, git_commit, created_by))
            return self._one(cur)

    def config_version(self, version_id: int) -> dict:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from config_versions where id = %s", (version_id,))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"config version {version_id}")
        return row

    def list_config_versions(self, *, limit: int = 50) -> list[dict]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select id, sha256, source, git_commit, created_at, created_by,
                       length(content) as bytes
                from config_versions order by id desc limit %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    # -- pinning a window to a config --------------------------------------

    def pin_period_config(self, platform: str, period: str, version_id: int, *,
                          pinned_by: str | None = None, reason: str | None = None) -> dict:
        """Freeze a window to one config version, and record that it happened.

        The event row is written in the SAME transaction as the upsert: current state
        and its history cannot disagree, and a crash between them is not a state this
        can reach (migration `014`).
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into period_config (platform, period, config_version_id, pinned_by, reason)
                values (%s, %s, %s, %s, %s)
                on conflict (platform, period) do update
                    set config_version_id = excluded.config_version_id,
                        pinned_at = now(), pinned_by = excluded.pinned_by,
                        reason = excluded.reason
                returning *
            """, (platform, period, version_id, pinned_by, reason))
            row = self._one(cur)
            cur.execute("""
                insert into config_pin_events
                    (platform, period, action, config_version_id, actor, reason)
                values (%s, %s, 'pin', %s, %s, %s)
            """, (platform, period, version_id, pinned_by or "unknown",
                  reason or "no reason recorded"))
            return row

    def pinned_config(self, platform: str, period: str) -> dict | None:
        """The config text a re-run of this window must use, or None.

        This is the whole of defect 2.5's fix: a run resolves its rules through
        here first and falls back to disk only when a window has never been
        pinned. Changing a VAT rate in August then cannot change what a re-run of
        May produces.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select v.*, p.pinned_at, p.pinned_by, p.reason
                from period_config p join config_versions v on v.id = p.config_version_id
                where p.platform = %s and p.period = %s
            """, (platform, period))
            return self._one(cur)

    def unpin_period_config(self, platform: str, period: str, *,
                            actor: str, reason: str) -> bool:
        """Release a window's pin, leaving a record that it was released.

        `actor` and `reason` are REQUIRED. Until 2026-08-19 this was a bare delete,
        so afterwards nothing recorded that the window had ever been pinned, to what,
        or why — the one act on the config path that left no trace, and the one whose
        consequence is that a re-run may produce different numbers than the run the
        invoice came from (defect 2.5).

        The version being released is read inside the transaction so the event names
        it; after the delete there is nowhere left to look it up.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                select config_version_id from period_config
                where platform = %s and period = %s
            """, (platform, period))
            found = cur.fetchone()
            if not found:
                return False
            version_id = found[0]
            cur.execute("delete from period_config where platform = %s and period = %s",
                        (platform, period))
            deleted = cur.rowcount == 1
            if deleted:
                cur.execute("""
                    insert into config_pin_events
                        (platform, period, action, config_version_id, actor, reason)
                    values (%s, %s, 'unpin', %s, %s, %s)
                """, (platform, period, version_id, actor, reason))
            return deleted

    def pin_events(self, platform: str | None = None,
                   period: str | None = None) -> list[dict]:
        """Pin/unpin history, newest first. Optionally for one window.

        Read-only over an append-only table: this is the record that survives an
        unpin, so nothing here filters by whether a pin is currently in force.
        """
        clauses, params = [], []
        if platform:
            clauses.append("e.platform = %s")
            params.append(platform)
        if period:
            clauses.append("e.period = %s")
            params.append(period)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"""
                select e.id, e.platform, e.period, e.action, e.config_version_id,
                       e.actor, e.reason, e.at, v.sha256
                from config_pin_events e
                left join config_versions v on v.id = e.config_version_id
                {where}
                order by e.at desc, e.id desc
            """, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def list_pins(self) -> list[dict]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select p.platform, p.period, p.config_version_id, p.pinned_at,
                       p.pinned_by, p.reason, v.sha256
                from period_config p join config_versions v on v.id = p.config_version_id
                order by p.platform, p.period
            """)
            return [dict(r) for r in cur.fetchall()]

    def attach_config_to_run(self, run_id: int, version_id: int, *, pinned: bool) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update runs set config_version_id = %s, config_was_pinned = %s
                where id = %s
            """, (version_id, pinned, run_id))

    # -- config proposals --------------------------------------------------

    def create_proposal(self, *, base_sha256: str, content: str, summary: str,
                        diff: str, proposed_by: str, edits: list | None = None,
                        rebased_from: int | None = None,
                        edit_model: str | None = None) -> dict:
        """Record a proposed change and, since M6, the OPERATIONS that produced it.

        Storing the edits and not only the resulting file is what makes a stale
        proposal replayable. Without them a proposal whose base has moved can only
        be refused, and the change has to be retyped from memory against a file that
        has changed — which is how a two-line intent becomes a three-line diff.

        `edit_model` says which editor produced them (008). A row proposal and a
        pre-1.6 path proposal are both jsonb and neither is readable as the other,
        so replay reads this before trying.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into config_proposals (base_sha256, content, summary, diff,
                                              proposed_by, edits, rebased_from,
                                              edit_model)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                returning *
            """, (base_sha256, content, summary, diff, proposed_by,
                  Jsonb(edits) if edits is not None else None, rebased_from,
                  edit_model))
            return self._one(cur)

    def record_config_verification(self, version_id: int, verdict) -> dict:
        """What the canary run said about this config version.

        Written separately from `record_config_version` because it happens after it:
        the version has to exist before a run can be attributed to it, and the run
        takes seconds. A version with a NULL state is one nothing has been claimed
        about — which is a third thing, distinct from 'verified' and from
        'unavailable' (005_config_multi_edit.sql).
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update config_versions set
                    verification_state = %s, verified_window = %s,
                    verified_window_is_real = %s, cells_moved = %s,
                    verification = %s, verified_at = now()
                where id = %s returning *
            """, (verdict.state, verdict.window, verdict.strong, verdict.cells_moved,
                  Jsonb(json.loads(verdict.to_json())), version_id))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"config version {version_id}")
        return row

    def proposal(self, proposal_id: int) -> dict:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from config_proposals where id = %s", (proposal_id,))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"config proposal {proposal_id}")
        return row

    def list_proposals(self, *, state: str | None = None, limit: int = 50) -> list[dict]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select id, base_sha256, summary, state, proposed_by, proposed_at,
                       decided_by, decided_at, decision_note, applied_version_id
                from config_proposals
                where (%(state)s::text is null or state = %(state)s)
                order by id desc limit %(limit)s
            """, {"state": state, "limit": limit})
            return [dict(r) for r in cur.fetchall()]

    def decide_proposal(self, proposal_id: int, *, state: str, decided_by: str,
                        note: str | None = None) -> dict:
        """approve / reject a pending proposal. Only pending may be decided —
        re-approving an applied change would be a second write of the rules the
        money math runs on."""
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update config_proposals
                set state = %s, decided_by = %s, decided_at = now(), decision_note = %s
                where id = %s and state = 'pending'
                returning *
            """, (state, decided_by, note, proposal_id))
            row = self._one(cur)
        if row is None:
            current = self.proposal(proposal_id)
            raise ValueError(f"proposal {proposal_id} is {current['state']}, not pending")
        return row

    def mark_proposal_applied(self, proposal_id: int, version_id: int) -> dict:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update config_proposals
                set state = 'applied', applied_version_id = %s
                where id = %s and state = 'approved'
                returning *
            """, (version_id, proposal_id))
            row = self._one(cur)
        if row is None:
            current = self.proposal(proposal_id)
            raise ValueError(f"proposal {proposal_id} is {current['state']}, not approved")
        return row

    def withdraw_proposal(self, proposal_id: int) -> dict:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update config_proposals set state = 'withdrawn'
                where id = %s and state = 'pending' returning *
            """, (proposal_id,))
            row = self._one(cur)
        if row is None:
            current = self.proposal(proposal_id)
            raise ValueError(f"proposal {proposal_id} is {current['state']}, not pending")
        return row

    # -- uploads -----------------------------------------------------------

    def record_upload(self, *, filename: str, sha256: str, bytes_: int,
                      uploaded_by: str, platform: str | None = None,
                      period: str | None = None, kind: str | None = None,
                      pii_columns_dropped: Sequence[str] = (), sanitized: bool = False,
                      uri: str | None = None, state: str = "stored",
                      reason: str | None = None, store: str | None = None,
                      store_canonical: str | None = None,
                      object_key: str | None = None,
                      object_sha256: str | None = None) -> dict:
        """Insert, or raise DuplicateUpload if these exact bytes arrived before.

        The unique constraint is the M2.5 double-pull control moved to the door:
        a byte-identical re-upload is the failure shape that once carried 5.97B
        VND of double-invoicing risk (docs/06-DECISIONS.md#d9).

        `sha256` digests the ORIGINAL upload; `object_sha256` digests the sanitized
        bytes that actually go into the store. They are different files on purpose,
        and conflating them is what M8/2.5 found (010_object_digest.sql).

        `store` is recorded because it is resolved at the door in M6 rather than
        inside a run. That is what lets the upload screen count roster coverage
        before anything is queued — and it means a filename the pipeline's regex
        cannot parse is refused while a human is still looking at it, instead of
        hard-stopping a run at month end (004_uploads_objects.sql).
        """
        try:
            with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    insert into uploads (filename, sha256, bytes, platform, period, kind,
                                         pii_columns_dropped, sanitized, uri, state,
                                         reason, uploaded_by, store, store_canonical,
                                         object_key, object_sha256)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning *
                """, (filename, sha256, bytes_, platform, period, kind,
                      list(pii_columns_dropped), sanitized, uri, state, reason,
                      uploaded_by, store, store_canonical, object_key, object_sha256))
                return self._one(cur)
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateUpload(self.upload_by_digest(sha256)) from exc

    def upload_by_digest(self, sha256: str) -> dict | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from uploads where sha256 = %s", (sha256,))
            return self._one(cur)

    # -- the order-coverage index (migration 015, defect 2.12) ---------------
    #
    # Identifiers and counts only. The database may know where every number came
    # from; it may never compute one — the money math stays in `src/`, verified
    # row-by-row against the team's own workbooks, and a SQL second implementation
    # of it would be the D31 failure.

    def record_order_index(self, upload_id: int, store: str,
                           order_ids: Sequence[str], *,
                           settles_from=None, settles_to=None) -> int:
        """Index one upload's distinct order ids, and stamp it as indexed.

        Idempotent: re-indexing an upload replaces its rows rather than accumulating
        them, so the backfill CLI can be run twice without doubling anything.

        `indexed_at` is stamped even when `order_ids` is empty. An income or order
        export with no order-id column is a legitimate answer — a Lazada ledger has
        none — and it must be distinguishable from work still outstanding, which is
        why the column is nullable rather than defaulted.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("delete from upload_order_index where upload_id = %s",
                        (upload_id,))
            rows = [(upload_id, str(store), str(o)) for o in dict.fromkeys(order_ids)]
            if rows:
                cur.executemany(
                    "insert into upload_order_index (upload_id, store, order_id) "
                    "values (%s, %s, %s)", rows)
            cur.execute("""
                update uploads
                set indexed_at = now(),
                    settles_from = coalesce(%s, settles_from),
                    settles_to = coalesce(%s, settles_to)
                where id = %s
            """, (settles_from, settles_to, upload_id))
            return len(rows)

    def uploads_unindexed(self, limit: int = 500) -> list[dict]:
        """Uploads with no index rows yet, oldest first — the backfill work list."""
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select * from uploads
                where indexed_at is null and state in ('stored', 'consumed')
                order by id limit %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    def upload_spans(self, platform: str, period: str, kind: str) -> list[dict]:
        """Each upload's settlement span for one (platform, period, kind).

        Feeds the mis-pull warning at the door: a file whose settlement range starts
        earlier than its siblings' is the shape `tools/stage_exports.find_outliers`
        detects, and the api had no equivalent (defect 2.3's residual).
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select id, filename, store_canonical, settles_from, settles_to
                from uploads
                where platform = %s and period = %s and kind = %s
                  and state in ('stored', 'consumed') and settles_from is not null
                order by id
            """, (platform, period, kind))
            return [dict(r) for r in cur.fetchall()]

    def order_coverage(self, platform: str, period: str) -> list[dict]:
        """Per store: income order ids with no hit in THIS window's order uploads.

        Counts only. The pipeline computes the authoritative per-store coverage from
        the frames it reads (`tieout.coverage_by_store`); this is the same question
        asked of the *uploads* so the answer exists before a run is queued.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                with income as (
                    select i.store, i.order_id from upload_order_index i
                    join uploads u on u.id = i.upload_id
                    where u.platform = %(platform)s and u.period = %(period)s
                      and u.kind in ('income', 'weekly', 'daily')
                      and u.state in ('stored', 'consumed')
                ), own_orders as (
                    select i.store, i.order_id from upload_order_index i
                    join uploads u on u.id = i.upload_id
                    where u.platform = %(platform)s and u.period = %(period)s
                      and u.kind = 'orders' and u.state in ('stored', 'consumed')
                )
                select income.store,
                       count(*) as income_orders,
                       count(*) filter (where own_orders.order_id is null)
                           as unmatched_orders
                from income
                left join own_orders
                    on own_orders.store = income.store
                   and own_orders.order_id = income.order_id
                group by income.store
                order by unmatched_orders desc
            """, {"platform": platform, "period": period})
            return [dict(r) for r in cur.fetchall()]

    def cross_window_order_holders(self, platform: str, period: str) -> list[dict]:
        """The exact signal for defect 2.12, and it has ZERO legitimate traffic.

        For each of this window's income orders that has no lines in this window's own
        order uploads, which EARLIER same-month window's order upload holds it.

        Why this is the check worth alarming on, where the per-store share is not: the
        legitimate ~21% reconciling class has lines in **no** window at all (the team's
        own VLOOKUP drops them too). An order whose lines sit in a sibling window's
        export is not that class — it is an order export that does not cover the orders
        its window settles.

        "Earlier" is same month, same window letter, lower ordinal — so a re-run of
        `w2` cannot change because `w5` arrived later. Sub-batch labels (`s2x`) sort
        after their base by string comparison, which is the intended order.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                with income as (
                    select i.store, i.order_id from upload_order_index i
                    join uploads u on u.id = i.upload_id
                    where u.platform = %(platform)s and u.period = %(period)s
                      and u.kind in ('income', 'weekly', 'daily')
                      and u.state in ('stored', 'consumed')
                ), own_orders as (
                    select i.store, i.order_id from upload_order_index i
                    join uploads u on u.id = i.upload_id
                    where u.platform = %(platform)s and u.period = %(period)s
                      and u.kind = 'orders' and u.state in ('stored', 'consumed')
                ), missing as (
                    select income.store, income.order_id from income
                    left join own_orders
                        on own_orders.store = income.store
                       and own_orders.order_id = income.order_id
                    where own_orders.order_id is null
                )
                select m.store, u.period as holder_period, u.id as upload_id,
                       u.filename, count(*) as orders
                from missing m
                join upload_order_index i
                    on i.store = m.store and i.order_id = m.order_id
                join uploads u on u.id = i.upload_id
                where u.platform = %(platform)s and u.kind = 'orders'
                  and u.state in ('stored', 'consumed')
                  and split_part(u.period, '_', 1) = split_part(%(period)s, '_', 1)
                  and u.period < %(period)s
                group by m.store, u.period, u.id, u.filename
                order by orders desc
            """, {"platform": platform, "period": period})
            return [dict(r) for r in cur.fetchall()]

    def upload(self, upload_id: int) -> dict:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from uploads where id = %s", (upload_id,))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"upload {upload_id}")
        return row

    def list_uploads(self, *, platform: str | None = None, period: str | None = None,
                     state: str | None = None, limit: int = 100) -> list[dict]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select * from uploads
                where (%(platform)s::text is null or platform = %(platform)s)
                  and (%(period)s::text is null or period = %(period)s)
                  and (%(state)s::text is null or state = %(state)s)
                order by id desc limit %(limit)s
            """, {"platform": platform, "period": period, "state": state, "limit": limit})
            return [dict(r) for r in cur.fetchall()]

    def uploads_for_window(self, platform: str, period: str) -> list[dict]:
        """Every file that IS this window, oldest first.

        Both `stored` and `consumed` — `consumed` is provenance ("run 41 read
        this"), not removal from the window. Selecting only `stored` would make a
        second run of the same window find nothing and silently fall back to the
        local input directory, which is a mode change disguised as a re-run.

        `rejected` rows are excluded: they were refused at the door and were never
        part of the window.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select * from uploads
                where platform = %s and period = %s
                  and state in ('stored', 'consumed')
                order by id
            """, (platform, period))
            return [dict(r) for r in cur.fetchall()]

    def mark_uploads_consumed(self, upload_ids: Sequence[int], run_id: int) -> int:
        """Record which run first read these files.

        `coalesce` on the run id, so a re-run does not rewrite the attribution:
        the interesting fact is which run first consumed an export, and
        overwriting it would erase the link to the workbook that was invoiced from.
        """
        if not upload_ids:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update uploads
                set state = 'consumed',
                    consumed_by_run_id = coalesce(consumed_by_run_id, %s),
                    consumed_at = coalesce(consumed_at, now())
                where id = any(%s)
            """, (run_id, list(upload_ids)))
            return cur.rowcount

    def delete_uploads_for_period(self, period: str) -> int:
        """Hard-delete a window's upload rows. **Only for the demo window.**

        Everywhere else an upload is rejected, never deleted, because which file was
        in a window and why is the audit trail for numbers somebody later queries.
        The demo window is the exception that proves the rule: it holds no client
        data, its whole purpose is to be re-creatable, and leaving rejected synthetic
        rows behind would clutter the queue an operator learns the system from.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("delete from uploads where period = %s", (period,))
            return cur.rowcount

    def reject_upload(self, upload_id: int, reason: str) -> dict:
        """Take a file out of the window without deleting the record of it.

        Never a delete: an operator who uploaded the wrong export and then a right
        one leaves two rows, and which was rejected and why is the audit trail for
        a window whose numbers someone later queries.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update uploads set state = 'rejected', reason = %s
                where id = %s and state <> 'consumed' returning *
            """, (reason, upload_id))
            row = self._one(cur)
        if row is None:
            existing = self.upload(upload_id)
            raise ValueError(
                f"upload {upload_id} was already consumed by run "
                f"{existing['consumed_by_run_id']}; a file a run has read cannot be "
                f"un-read")
        return row

    # -- window roster declaration ------------------------------------------

    def declare_window_roster(self, platform: str, period: str, *, partial: bool,
                              reason: str | None, declared_by: str) -> dict:
        """Record, once per window, that an incomplete roster is expected.

        Replaces `jobs.partial_roster`. The difference is not the plumbing — the
        worker still passes the same flag into `build_context` — it is that this
        is stated once, with a reason, by a named person, and is visible to whoever
        reviews the numbers. A checkbox on the queue form was none of those things.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into windows (platform, period, roster_declared_partial,
                                     reason, declared_by)
                values (%s, %s, %s, %s, %s)
                on conflict (platform, period) do update
                set roster_declared_partial = excluded.roster_declared_partial,
                    reason = excluded.reason,
                    declared_by = excluded.declared_by,
                    declared_at = now()
                returning *
            """, (platform, period, partial, reason, declared_by))
            return self._one(cur)

    def window_declaration(self, platform: str, period: str) -> dict | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from windows where platform = %s and period = %s",
                        (platform, period))
            return self._one(cur)

    def clear_window_declaration(self, platform: str, period: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("delete from windows where platform = %s and period = %s",
                        (platform, period))
            return cur.rowcount > 0

    # -- reference totals (A3) ---------------------------------------------

    def window_references(self, platform: str, period: str) -> dict | None:
        """The team's own totals for this window, or None if nobody supplied any."""
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from window_references "
                        "where platform = %s and period = %s", (platform, period))
            return self._one(cur)

    def set_window_references(self, platform: str, period: str, *, refs: dict,
                              supplied_by: str, note: str | None = None) -> dict:
        """Record or replace the team's figures for a window.

        Upsert rather than insert-only: a figure gets corrected, and a second row
        would leave two answers with nothing deciding which a run uses. The previous
        value is not versioned here — `runs` already records what each run compared
        against, which is the question an auditor actually asks.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into window_references (platform, period, refs, supplied_by, note)
                values (%s, %s, %s, %s, %s)
                on conflict (platform, period) do update
                    set refs = excluded.refs,
                        supplied_by = excluded.supplied_by,
                        supplied_at = now(),
                        note = excluded.note
                returning *
            """, (platform, period, Jsonb(refs), supplied_by, note))
            return self._one(cur)

    def clear_window_references(self, platform: str, period: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("delete from window_references "
                        "where platform = %s and period = %s", (platform, period))
            return cur.rowcount > 0

    # -- the month, for the month-end master (M8 Phase 3) -------------------

    def month_windows(self, month: str) -> list[dict]:
        """Every window the system knows about for a month, and its latest run.

        **This is what kills the hardcoded window table** (register A5). The old
        `tools/build_master_summary.py` carried `w1..w5 / s1..s4 / l1..l5` in a
        dict and silently omitted the sub-batch windows that really exist —
        Shopee's `s2x` and `s3k` — while its own tie check re-read the same dict,
        so it could not possibly notice. Here the answer is "what actually ran",
        which cannot omit a window that ran.

        A window counts as known if anything in the system references it: a run, a
        roster declaration, or an upload. That union matters — a window whose
        uploads arrived but which nobody has run yet is exactly the window a
        month-end master must report as MISSING rather than pass over in silence.

        `latest_run_id` is the most recent run per window, not the most recent
        successful one: a window whose last run hard-stopped must not quietly
        contribute the workbook of an earlier attempt.
        """
        like = f"{month}_%"
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                with known as (
                    select platform, period from runs     where period like %(like)s
                    union
                    select platform, period from windows  where period like %(like)s
                    union
                    -- `platform` is nullable on uploads (a file can be recorded
                    -- before it is classified) and a rejected upload is not
                    -- evidence that a window exists.
                    select platform, period from uploads
                     where period like %(like)s
                       and platform is not null
                       and state <> 'rejected'
                ),
                latest as (
                    select distinct on (platform, period)
                           platform, period, id as run_id, status, finished_at
                      from runs
                     where period like %(like)s
                     order by platform, period, id desc
                )
                select k.platform, k.period,
                       l.run_id as latest_run_id, l.status, l.finished_at
                  from known k
                  left join latest l
                    on l.platform = k.platform and l.period = k.period
                 order by k.platform, k.period
            """, {"like": like})
            return list(cur.fetchall())

    # -- exceptions --------------------------------------------------------

    def record_exceptions(self, run_id: int, sheet: str, rows: Iterable[dict], *,
                          total: int, fingerprint_of) -> int:
        """Store exception rows and, separately, how many there really were.

        `total` is passed in rather than inferred from `rows` because `rows` is
        already capped by the caller. A queue that shows 500 of 11,765 and says
        so is useful; one that shows 500 and implies completeness is a lie with a
        UI on it.
        """
        batch = [(run_id, sheet, fingerprint_of(r), Jsonb(_jsonable(r))) for r in rows]
        with self._conn() as conn, conn.cursor() as cur:
            if batch:
                cur.executemany("""
                    insert into run_exceptions (run_id, sheet, fingerprint, payload)
                    values (%s, %s, %s, %s)
                """, batch)
            cur.execute("""
                insert into run_exception_sheets (run_id, sheet, total_rows, stored_rows)
                values (%s, %s, %s, %s)
                on conflict (run_id, sheet) do update
                    set total_rows = excluded.total_rows, stored_rows = excluded.stored_rows
            """, (run_id, sheet, total, len(batch)))
        return len(batch)

    def exception_sheets(self, run_id: int) -> list[dict]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select sheet, total_rows, stored_rows,
                       total_rows > stored_rows as truncated
                from run_exception_sheets where run_id = %s order by sheet
            """, (run_id,))
            return [dict(r) for r in cur.fetchall()]

    def exceptions(self, run_id: int, *, sheet: str | None = None,
                   limit: int = 200, offset: int = 0) -> list[dict]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select id, sheet, fingerprint, payload, created_at
                from run_exceptions
                where run_id = %(run_id)s
                  and (%(sheet)s::text is null or sheet = %(sheet)s)
                order by id limit %(limit)s offset %(offset)s
            """, {"run_id": run_id, "sheet": sheet, "limit": limit, "offset": offset})
            return [dict(r) for r in cur.fetchall()]

    def exception_history(self, fingerprint: str, *, limit: int = 20) -> list[dict]:
        """Every run this same exception appeared in.

        The reason `fingerprint` exists at all: an unmatched order that recurs
        every week is a different thing from one that appeared once, and no
        per-run view can tell them apart. M6 hangs dispositions off this.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select e.run_id, r.platform, r.period, r.started_at, e.sheet
                from run_exceptions e join runs r on r.id = e.run_id
                where e.fingerprint = %s
                order by e.run_id desc limit %s
            """, (fingerprint, limit))
            return [dict(r) for r in cur.fetchall()]

    # -- the month board ---------------------------------------------------

    def board(self, month: str | None = None) -> list[dict]:
        """One row per settlement window: its latest job, run and verdict.

        `distinct on` picks the newest run per window in one pass. A window with
        two runs shows the most recent — with `run_count` alongside, because a
        window that has been run four times is telling you something.

        **`partial_roster` now comes from `windows`, not from `jobs`.** The board is
        where a reviewer sees that a window's totals are a subset of the month, so
        it must show the declaration a person made about the WINDOW — with its
        reason and its author — rather than a flag on whichever job happened to be
        latest. `coalesce` keeps a pre-M6 job's flag visible so old rows do not
        silently lose their caveat.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select distinct on (j.platform, j.period)
                       j.platform, j.period, j.kind, j.id as job_id,
                       j.state as job_state,
                       coalesce(w.roster_declared_partial, j.partial_roster)
                           as partial_roster,
                       w.reason as roster_reason, w.declared_by as roster_declared_by,
                       j.requested_by, j.created_at as queued_at,
                       r.id as run_id, r.status, r.exit_code, r.started_at, r.finished_at,
                       r.wall_s, r.peak_rss_mb, r.config_was_pinned, r.roster_missing,
                       jsonb_array_length(r.findings) as finding_count,
                       (select count(*) from jobs j2
                        where j2.platform = j.platform and j2.period = j.period) as job_count
                from jobs j
                left join runs r on r.job_id = j.id
                left join windows w on w.platform = j.platform and w.period = j.period
                where (%(month)s::text is null or j.period like %(month)s || '%%')
                order by j.platform, j.period, j.id desc
            """, {"month": month})
            return [dict(r) for r in cur.fetchall()]


class DuplicateUpload(RuntimeError):
    """These exact bytes have been uploaded before — the double-pull shape."""

    def __init__(self, existing: dict | None) -> None:
        super().__init__(
            f"an identical file was already uploaded"
            + (f" as upload {existing['id']} ({existing['filename']})" if existing else ""))
        self.existing = existing


def _jsonable(value: Any) -> Any:
    """Exception rows arrive as pandas records — NaN, Timestamp, numpy scalars.

    None of those survive `json.dumps`, and a 500 out of a workbook detail is a
    bad way to learn that.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # NaN and inf are not JSON. NaN specifically means "this cell was blank",
        # which null represents honestly.
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
