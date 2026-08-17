-- M6, workstream F: `exceptions._norm` now NFC-normalises. This records that it
-- moved nothing.
--
-- ---------------------------------------------------------------------------
-- Why the change, and why this file does no work
-- ---------------------------------------------------------------------------
--
-- `run_exceptions.fingerprint` is a stable identity for one exception across runs —
-- the thing that turns "this unmatched order has recurred for six weeks" into a
-- query. `service/exceptions.py::_norm` did not NFC-normalise, so an identity value
-- arriving in a different Unicode form hashed differently and silently orphaned its
-- own history. `store` and `fee_name` are Vietnamese, and NFD is byte-unequal to the
-- visually identical NFC — the same bug `ingest.py:211` fixes for headers and
-- `pipeline.norm_store` has always fixed for store names.
--
-- **Measured before the change, not after** (`service/nfc_audit.py`, 2026-08-17):
--
--     settings.yaml store names ......................  0 non-NFC
--     lazada_fee_types.csv .......................  0 of 118 non-NFC
--     live "Lib & VAT rate.xlsb" fee names .......  0 of 118 non-NFC
--     run_exceptions stored identity values ......  0 rows
--
-- So **zero fingerprints move** and there is nothing to recompute. That measurement
-- matters more than it looks: the M6 plan claimed "0 impact" on the strength of a
-- FILENAME survey (166/166 NFC), which says nothing about `fee_name` — a value that
-- comes from Vietnamese Lazada exports and was the real candidate. It was audited
-- separately and came back clean.
--
-- ---------------------------------------------------------------------------
-- Why there is no UPDATE here, even conditionally
-- ---------------------------------------------------------------------------
--
-- Recomputing a fingerprint in SQL would mean reimplementing
-- `exceptions.fingerprint` in PL/pgSQL: the sheet name, the ordered identity
-- columns, the float-to-int rule, the whole-row fallback, sha256 truncated to 32
-- chars. That is a SECOND definition of what an exception's identity IS, and the two
-- would drift the first time `IDENTITY_COLUMNS` changed. One definition, in Python.
--
-- If a future change to `_norm` DOES move fingerprints, the correct migration is a
-- Python backfill that calls `exceptions.fingerprint` — not SQL that imitates it.
-- `service/nfc_audit.py` is how you find out whether one is needed.
--
-- This file exists so the audit and its result are stamped into the schema history
-- with a date, where they cannot be lost from a commit message.

comment on column run_exceptions.fingerprint is
    'stable identity across runs, from service/exceptions.py::fingerprint. '
    'NFC-normalised since M6 (006); audited 2026-08-17 and 0 stored rows were '
    'affected. Recomputing this in SQL would be a second definition of identity — '
    'if a future change moves fingerprints, backfill in Python.';
