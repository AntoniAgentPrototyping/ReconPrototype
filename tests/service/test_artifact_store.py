"""The artifact store — no database required.

The claim under test is the one in service/artifacts.py's header: the worker does
not write the deliverable, it takes files `pipeline.write_artifacts` already
wrote. So the store's job is copy, hash, address — and the addressing has to
survive a Windows drive letter going through a file:// URI and back, which is the
part that silently breaks.
"""

from __future__ import annotations

import hashlib

from service.artifacts import LocalArtifactStore, sha256_of


def make_file(path, content: bytes = b"finance"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_put_copies_and_addresses_the_file(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    src = make_file(tmp_path / "scratch" / "finance_file.xlsx", b"x" * 1024)

    art = store.put(period="2026-05_l1", platform="lazada", run_id=7, path=src)

    assert art.name == "finance_file.xlsx"
    assert art.bytes == 1024
    assert art.sha256 == hashlib.sha256(b"x" * 1024).hexdigest()
    assert art.local_path.is_file()
    assert art.local_path.read_bytes() == src.read_bytes()
    # The source is untouched — the scratch copy is still the one the run wrote.
    assert src.is_file()


def test_layout_is_the_window_path_plus_the_run(tmp_path):
    """`<root>/<period>/<platform>/run-<id>/` — the path an operator already
    knows from output/, plus the run id, because comparing two runs of one window
    is normal and overwriting the earlier one destroys the evidence."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    first = store.put(period="2026-05_l1", platform="lazada", run_id=1,
                      path=make_file(tmp_path / "a" / "finance_file.xlsx", b"one"))
    second = store.put(period="2026-05_l1", platform="lazada", run_id=2,
                       path=make_file(tmp_path / "b" / "finance_file.xlsx", b"two"))

    assert first.local_path.parent.name == "run-1"
    assert second.local_path.parent.name == "run-2"
    assert first.local_path.parent.parent == tmp_path / "artifacts" / "2026-05_l1" / "lazada"
    assert first.local_path.read_bytes() == b"one", "run 1 was not clobbered"


def test_open_round_trips_the_uri(tmp_path):
    """A file:// URI puts a Windows drive letter in the path as `/C:/...`, and
    handing that straight to Path yields a path that does not exist. The round
    trip is what the api's download endpoint depends on."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    art = store.put(period="2026-05_w1", platform="tiktok", run_id=3,
                    path=make_file(tmp_path / "s" / "exceptions.xlsx", b"rows"))

    assert art.uri.startswith("file:")
    resolved = store.open(art.uri)
    assert resolved is not None, f"could not resolve {art.uri!r} back to a path"
    assert resolved.read_bytes() == b"rows"


def test_open_handles_a_directory_with_spaces(tmp_path):
    """The repo itself lives under "OneDrive - ADA Global", so percent-encoding
    in the URI is the normal case here, not an edge one."""
    store = LocalArtifactStore(tmp_path / "some dir with spaces")
    art = store.put(period="2026-05_l1", platform="lazada", run_id=1,
                    path=make_file(tmp_path / "s" / "run_log.txt", b"log"))
    assert "%20" in art.uri
    assert store.open(art.uri).read_bytes() == b"log"


def test_open_declines_a_uri_it_does_not_own(tmp_path):
    """An object-store deployment must serve a signed URL instead of a file, and
    the api turns this None into a 501 rather than a stack trace."""
    store = LocalArtifactStore(tmp_path)
    assert store.open("s3://bucket/2026-05_l1/finance_file.xlsx") is None


def test_open_declines_a_file_that_has_been_deleted(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    art = store.put(period="2026-05_l1", platform="lazada", run_id=1,
                    path=make_file(tmp_path / "s" / "gone.xlsx", b"x"))
    art.local_path.unlink()
    assert store.open(art.uri) is None


def test_reput_is_idempotent(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    src = make_file(tmp_path / "s" / "finance_file.xlsx", b"same")
    first = store.put(period="2026-05_l1", platform="lazada", run_id=1, path=src)
    again = store.put(period="2026-05_l1", platform="lazada", run_id=1, path=src)
    assert (first.uri, first.sha256) == (again.uri, again.sha256)


def test_putting_a_file_already_in_place_does_not_truncate_it(tmp_path):
    """shutil.copy2 onto itself raises SameFileError; the guard exists so a store
    rooted at the scratch directory degrades to registration rather than data
    loss."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    target_dir = store.location("2026-05_l1", "lazada", 1)
    in_place = make_file(target_dir / "finance_file.xlsx", b"already here")

    art = store.put(period="2026-05_l1", platform="lazada", run_id=1, path=in_place)
    assert art.bytes == len(b"already here")
    assert in_place.read_bytes() == b"already here"


def test_sha256_streams_a_large_file(tmp_path):
    """Workbooks run to tens of MB; the digest must not need the file in memory
    twice."""
    blob = b"a" * (3 * (1 << 20))
    path = make_file(tmp_path / "big.xlsx", blob)
    assert sha256_of(path, chunk=4096) == hashlib.sha256(blob).hexdigest()
