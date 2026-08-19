"""A file that cannot be opened says WHY, and the why turns out to matter.

**How this test exists.** Two files in this tree refuse to open. Both had been
recorded for months as something they are not:

* `input/master/ADA marketplace MASTER July 2026.xlsx` — written up in the
  production-readiness register as "not an `.xlsx`: an OLE2 compound file, i.e. a
  legacy Excel 97–2003 `.xls` with the wrong extension";
* one Lazada weekly export — recorded in `12-CHANGE-HISTORY.md`, `10-ROADMAP.md`
  and `CLAUDE.md` since Aug 2026 as **"password-protected"**.

Both are genuine `.xlsx` files, encrypted by a Microsoft Purview **sensitivity
label** — the same label id, the same tenant, `method="Privileged"` on each. The
OLE2 signature that led to the first diagnosis is the encryption wrapper, not a
legacy workbook: `xlrd` opens the container and finds no workbook stream in it.

The distinction is the whole value here. A password is something you ask a
colleague for. A sensitivity label is org policy — the file opens only for an
identity the label grants rights to, and no re-saving, renaming or reader-swapping
touches it. It also will not be the last such file, which makes it a constraint on
hosting this system at all (`docs/13-ENTRA-SETUP.md`).
"""

from __future__ import annotations

import pytest

pytest.importorskip("pandas")

from src import ingest                                          # noqa: E402


# The two markers that distinguish the cases, as they appear in an OLE2 stream
# directory: UTF-16LE, because that is how OLE2 stores entry names.
def _ole2(*stream_names: str) -> bytes:
    body = b"".join(name.encode("utf-16-le") + b"\x00\x00" for name in stream_names)
    return bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64 + body


def test_a_healthy_export_is_rejected_on_eight_bytes(tmp_path):
    """The cost this imposes on every good file. A real `.xlsx` is a ZIP, so the
    signature check answers before anything is read — which is what makes it safe
    to call on a 382 MB export at the upload door."""
    good = tmp_path / "1_Store.xlsx"
    good.write_bytes(b"PK\x03\x04" + b"\x00" * 4096)
    assert ingest.rights_protected(good) is None


def test_a_sensitivity_label_is_named_as_such(tmp_path):
    """**The finding.** Not "corrupt", not "password-protected" — and the message
    has to rule out the two things a person would otherwise try first."""
    f = tmp_path / "ADA marketplace MASTER July 2026.xlsx"
    f.write_bytes(_ole2("EncryptedPackage", "LabelInfo", "DataSpaces"))

    message = ingest.rights_protected(f)
    assert message is not None
    assert "sensitivity label" in message
    assert "Re-saving or renaming will not help" in message
    assert f.name in message, "a refusal that does not name the file is not actionable"


def test_encryption_without_a_label_is_a_different_message(tmp_path):
    """A genuinely password-protected workbook is encrypted with no `LabelInfo`.
    That one IS something you ask a colleague for, so it must not be told to go and
    negotiate with an information-protection policy."""
    f = tmp_path / "locked.xlsx"
    f.write_bytes(_ole2("EncryptedPackage"))

    message = ingest.rights_protected(f)
    assert "sensitivity label" not in message
    assert "unprotected copy" in message


def test_a_real_legacy_xls_is_told_to_re_save(tmp_path):
    """OLE2 with no encrypted package: an actual `.xls` renamed. This is the case
    the register originally claimed, and it needs the opposite advice — here
    re-saving IS the fix."""
    f = tmp_path / "old.xlsx"
    f.write_bytes(_ole2("Workbook"))

    message = ingest.rights_protected(f)
    assert "Excel Workbook (*.xlsx)" in message
    assert "renaming it is not enough" in message


def test_a_marker_split_across_a_read_boundary_is_still_found(tmp_path):
    """The chunked scan reads 1 MiB at a time. A marker straddling a boundary must
    not be missed — a false 'this file is fine' here sends the file on to a reader
    that fails with 'File is not a zip file', which is the message this exists to
    replace."""
    marker = "EncryptedPackage".encode("utf-16-le")
    chunk = 1 << 20
    head = bytes.fromhex("d0cf11e0a1b11ae1")
    padding = b"\x00" * (chunk - len(head) - len(marker) // 2)
    f = tmp_path / "straddle.xlsx"
    f.write_bytes(head + padding + marker + b"\x00" * 128)

    assert ingest.rights_protected(f) is not None


def test_a_protected_file_stops_the_read_instead_of_confusing_the_reader(tmp_path):
    """`read_excel_sheet` refuses before openpyxl or calamine sees it."""
    from src.errors import ReconHardStop

    f = tmp_path / "labelled.xlsx"
    f.write_bytes(_ole2("EncryptedPackage", "LabelInfo"))

    with pytest.raises(ReconHardStop, match="sensitivity label"):
        ingest.read_excel_sheet(f, 0, 1, None)
