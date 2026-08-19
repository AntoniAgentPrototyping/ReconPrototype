-- M8 Phase 3 — the settlement date's spelling becomes part of the contract.
--
-- `config_platforms.dayfirst` is per-PLATFORM. The formats are per-KIND, and that
-- mismatch is the defect: TikTok orders really are `%d/%m/%Y %H:%M:%S` while
-- TikTok income is `%Y/%m/%d`, so one boolean cannot be right for both.
--
-- It cost a month. pandas infers a format from the first non-null element and
-- `dayfirst` only decides the AMBIGUOUS case, so which format wins is
-- DATA-dependent. May's first `Order settled time` value happened to be
-- unambiguous, inference quietly overrode the flag, and the warning pandas emits
-- went to stderr and died there. July's first value is `2026/07/07` — ambiguous —
-- so `dayfirst=True` won and the whole column parsed as `%Y/%d/%m`: a window
-- covering 1-7 July came out spanning 2026-01-07..2026-09-07, and
-- `tools/stage_exports.py` could not derive a single window from a 3.7 GB dump.
--
-- Nullable, and null means "infer, using dayfirst" — the pre-existing behaviour.
-- A platform/kind with no measured format is not forced to invent one.
alter table config_reading
    add column if not exists date_format text;

comment on column config_reading.date_format is
    'strptime format applied to every date column of this platform/kind, e.g. '
    '%Y/%m/%d. Takes precedence over config_platforms.dayfirst. Null = infer. '
    'Each value is measured against real exports before it is set: it must parse '
    '100% of the non-blank cells, and explicit parsing is STRICTER than inference '
    '— a cell that does not match becomes NaT, which ingest.report_undated counts '
    'and names rather than dropping silently.';
