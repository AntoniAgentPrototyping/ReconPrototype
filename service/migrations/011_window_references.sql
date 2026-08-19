-- The team's own totals for a settlement window (A3).
--
-- `src/pipeline.py` compares a run against `refs` and reports UNVERIFIED (exit 2)
-- when there are none. The api has accepted `refs` on a job since M4 and no screen
-- has ever sent any, so every browser-driven run since M6 has been UNVERIFIED: it
-- ran clean and nothing corroborated it. The figures existed only in a JSON file a
-- developer passed to `tools/devrun.py`.
--
-- **Keyed on the window, not the job.** `jobs.refs` is per job, so a re-run silently
-- drops them and the second run of a window makes a weaker claim than the first.
-- The team's figures are a property of the window.
--
-- **A separate table from `windows`.** `windows.declared_by` is `not null` and means
-- "a person declared this window's roster incomplete, and here is why". Supplying
-- reference totals is an unrelated act by possibly a different person, and folding
-- it in would either force a fake declaration or make that column nullable — which
-- would quietly weaken a control that exists to stop an undeclared partial window.
create table if not exists window_references (
    platform    text        not null check (platform in ('tiktok', 'shopee', 'lazada')),
    period      text        not null,

    -- Shaped exactly like the `refs` dict the pipeline reads: {"grand": {...}}.
    -- Stored as given, never recomputed -- the whole value of these numbers is that
    -- they came from somewhere else, so a difference is evidence rather than an
    -- argument. `service/references.py` owns which keys are accepted, because a
    -- figure recorded under a name no check reads would look verified and be ignored.
    refs        jsonb       not null default '{}'::jsonb,

    -- From the session, never the body. Same rule as requested_by / uploaded_by /
    -- declared_by / proposed_by: this is the audit trail for a number that decides
    -- whether a run is called verified.
    supplied_by text        not null,
    supplied_at timestamptz not null default now(),
    note        text,

    primary key (platform, period)
);

comment on table window_references is
    'The team''s reference totals per settlement window, so a run can be tied '
    'against something this system did not compute. Absent = the run reports '
    'UNVERIFIED, which is honest rather than a failure.';
