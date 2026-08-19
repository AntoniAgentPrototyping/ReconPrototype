-- The digest of what is actually IN the object store, which is NOT `uploads.sha256`.
--
-- Defect 2.10 was written up as "`service/materialize.py` downloads by key while
-- `uploads.sha256` sits unused ten lines later". Implementing it that way in M8/2.5
-- failed every healthy window immediately, which is how this was found:
--
--   * `uploads.sha256` is the digest of the bytes the USER handed over. It is the
--     provenance record, and it is what the unique constraint uses to refuse a
--     byte-identical re-upload -- the M2.5 double-pull control moved to the door.
--   * What is stored under `object_key` is the SANITIZED rewrite of those bytes:
--     PII columns removed, one sheet, written by openpyxl. Different file,
--     different digest, deliberately (`service/uploads.py`).
--
-- So the two were never meant to be equal, and comparing them would have made the
-- integrity check a permanent false alarm. The check needs a third value, and this
-- is it: the digest of the sanitized object as it went into the store.
--
-- **No backfill is possible, and none is attempted.** Recomputing this for existing
-- rows means reading whatever is in the store today and writing that down as the
-- expected value -- which certifies the store against itself and would pass even if
-- the bytes had already been replaced. That is the recording-its-own-output failure
-- the golden rules exist to prevent (D26). NULL therefore means "uploaded before
-- this check existed" and `materialize.verify_digest` REFUSES it rather than
-- trusting it: an old upload has to be re-uploaded to be runnable.
alter table uploads add column if not exists object_sha256 text;

comment on column uploads.object_sha256 is
    'sha256 of the sanitized bytes in the object store. NOT uploads.sha256, which '
    'digests the original upload. NULL = predates the M8/2.5 integrity check and '
    'is refused at materialisation rather than trusted.';
