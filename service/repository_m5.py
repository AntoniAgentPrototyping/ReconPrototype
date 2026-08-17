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
            return self._one(cur)

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

    def unpin_period_config(self, platform: str, period: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("delete from period_config where platform = %s and period = %s",
                        (platform, period))
            return cur.rowcount == 1

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
                        rebased_from: int | None = None) -> dict:
        """Record a proposed change and, since M6, the OPERATIONS that produced it.

        Storing the edits and not only the resulting file is what makes a stale
        proposal replayable. Without them a proposal whose base has moved can only
        be refused, and the change has to be retyped from memory against a file that
        has changed — which is how a two-line intent becomes a three-line diff.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into config_proposals (base_sha256, content, summary, diff,
                                              proposed_by, edits, rebased_from)
                values (%s, %s, %s, %s, %s, %s, %s)
                returning *
            """, (base_sha256, content, summary, diff, proposed_by,
                  Jsonb(edits) if edits is not None else None, rebased_from))
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
                      object_key: str | None = None) -> dict:
        """Insert, or raise DuplicateUpload if these exact bytes arrived before.

        The unique constraint is the M2.5 double-pull control moved to the door:
        a byte-identical re-upload is the failure shape that once carried 5.97B
        VND of double-invoicing risk (docs/06-DECISIONS.md#d9).

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
                                         object_key)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning *
                """, (filename, sha256, bytes_, platform, period, kind,
                      list(pii_columns_dropped), sanitized, uri, state, reason,
                      uploaded_by, store, store_canonical, object_key))
                return self._one(cur)
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateUpload(self.upload_by_digest(sha256)) from exc

    def upload_by_digest(self, sha256: str) -> dict | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from uploads where sha256 = %s", (sha256,))
            return self._one(cur)

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
                       j.platform, j.period, j.id as job_id, j.state as job_state,
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
