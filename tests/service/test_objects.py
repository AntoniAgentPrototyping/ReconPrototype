"""The object store, and the two modes that must not diverge.

`LocalDirObjects` is not a test double — it is the mode a single-machine
deployment runs in — so the same behavioural tests run against both, with `boto3`'s
client stubbed for the S3 side. No database and no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service import objects as object_lib                            # noqa: E402
from service.objects import ObjectNotFound                           # noqa: E402


class FakeS3Client:
    """The five botocore calls `S3Objects` makes, and the error shape it reads.

    Deliberately mimics botocore's `response["Error"]["Code"]` rather than raising
    a friendlier exception: `S3Objects._is_missing` reads exactly that structure,
    and a test that raised something else would prove the translation works against
    an error MinIO never sends.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.buckets: set[str] = {"recon-uploads"}

    @staticmethod
    def _missing(code: str = "NoSuchKey") -> Exception:
        exc = Exception(code)
        exc.response = {"Error": {"Code": code}}
        return exc

    def put_object(self, *, Bucket, Key, Body, Metadata=None):
        self.store[Key] = Body
        return {}

    def get_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise self._missing()
        import io
        return {"Body": io.BytesIO(self.store[Key])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise self._missing("404")
        return {"ContentLength": len(self.store[Key])}

    def delete_object(self, *, Bucket, Key):
        self.store.pop(Key, None)
        return {}

    def head_bucket(self, *, Bucket):
        if Bucket not in self.buckets:
            raise self._missing("NoSuchBucket")
        return {}

    def create_bucket(self, *, Bucket):
        self.buckets.add(Bucket)
        return {}


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    if request.param == "local":
        return object_lib.LocalDirObjects(tmp_path / "objects")
    return object_lib.S3Objects("recon-uploads", client=FakeS3Client())


# ---------------------------------------------------------------------------
# The five operations, identically in both modes
# ---------------------------------------------------------------------------

def test_put_get_stream_exists_delete(store):
    key = "uploads/2026-05_l1/lazada/weekly/abc.xlsx"
    assert store.exists(key) is False
    with pytest.raises(ObjectNotFound):
        store.get(key)

    ref = store.put(key, b"some bytes")
    assert ref.key == key
    assert ref.bytes == 10
    assert ref.sha256 == object_lib.digest_of(b"some bytes")
    assert store.exists(key) is True
    assert store.get(key) == b"some bytes"
    assert b"".join(store.stream(key, chunk=3)) == b"some bytes"

    assert store.delete(key) is True
    assert store.delete(key) is False, "deleting twice is not an error"


def test_download_to_creates_parents_and_copies(store, tmp_path):
    key = "uploads/x/y/z/file.xlsx"
    store.put(key, b"payload")
    target = tmp_path / "scratch" / "job-1" / "input" / "file.xlsx"
    assert store.download_to(key, target) == target
    assert target.read_bytes() == b"payload"


def test_download_to_a_missing_key_raises_rather_than_writing_an_empty_file(store, tmp_path):
    """A worker that materialised a 0-byte export would produce a workbook missing
    one store's revenue and report success."""
    target = tmp_path / "out.xlsx"
    with pytest.raises(ObjectNotFound):
        store.download_to("uploads/nope.xlsx", target)
    assert not target.exists()


def test_streaming_a_missing_key_raises_before_the_first_chunk(store):
    with pytest.raises(ObjectNotFound):
        next(iter(store.stream("uploads/nope.xlsx")))


def test_the_uri_says_which_mode_wrote_it(tmp_path):
    """Recorded on the row so an artifact stays locatable if the deployment's
    storage mode changes underneath it."""
    local = object_lib.LocalDirObjects(tmp_path / "o")
    s3 = object_lib.S3Objects("recon-artifacts", client=FakeS3Client())
    assert local.uri_for("a/b.xlsx").startswith("file:")
    assert s3.uri_for("a/b.xlsx") == "s3://recon-artifacts/a/b.xlsx"


# ---------------------------------------------------------------------------
# Keys are paths in someone else's namespace
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "", "/absolute", "trailing/", "a//b", "../escape", "a/../../b", "a/./b",
])
def test_a_path_shaped_key_is_refused(store, key):
    """`LocalDirObjects` joins the key onto a directory, so an unchecked `../`
    writes outside the store — and the keys are partly built from a period, which
    is the kind of two-layer guard that rots when only one layer exists."""
    with pytest.raises(ValueError):
        store.put(key, b"x")


def test_a_traversing_key_cannot_escape_the_root(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"do not overwrite me")
    store = object_lib.LocalDirObjects(tmp_path / "objects")
    with pytest.raises(ValueError):
        store.put("../secret.txt", b"clobbered")
    assert outside.read_bytes() == b"do not overwrite me"


# ---------------------------------------------------------------------------
# URI parsing and wiring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uri,expected", [
    ("s3://bucket/a/b.xlsx", ("bucket", "a/b.xlsx")),
    ("s3://bucket/a%20b.xlsx", ("bucket", "a b.xlsx")),
    ("file:///C:/tmp/a.xlsx", None),
    ("s3://bucket", None),
    ("s3://bucket/", None),
    ("nonsense", None),
])
def test_parse_s3_uri(uri, expected):
    assert object_lib.parse_s3_uri(uri) == expected


def test_the_local_mode_is_chosen_when_no_endpoint_is_configured(tmp_path):
    """`settings.s3 is None` is a SUPPORTED mode, not a broken one — it is what
    lets the whole upload/run/download path be exercised without a container."""
    from service.config import ServiceSettings

    settings = ServiceSettings(
        database_url="postgresql://x/y", config_dir=tmp_path, input_root=tmp_path,
        artifact_root=tmp_path / "art", scratch_root=tmp_path / "s",
        upload_root=tmp_path / "u", worker_id="t")
    assert isinstance(object_lib.upload_store(settings), object_lib.LocalDirObjects)
    assert isinstance(object_lib.artifact_object_store(settings), object_lib.LocalDirObjects)

    from service.artifacts import LocalArtifactStore, build_artifact_store
    assert isinstance(build_artifact_store(settings), LocalArtifactStore)


def test_s3_settings_refuse_an_endpoint_with_no_credentials(monkeypatch):
    """Falling back to the local directory would silently write client exports
    somewhere other than where this deployment expects to find them."""
    from service.config import ConfigError, S3Settings

    monkeypatch.setenv("RECON_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.delenv("RECON_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("RECON_S3_SECRET_KEY", raising=False)
    with pytest.raises(ConfigError, match="Refusing to fall back"):
        S3Settings.from_env()

    monkeypatch.setenv("RECON_S3_ACCESS_KEY", "key")
    monkeypatch.setenv("RECON_S3_SECRET_KEY", "secret")
    parsed = S3Settings.from_env()
    assert parsed is not None
    assert parsed.uploads_bucket == "recon-uploads"
    assert parsed.artifacts_bucket == "recon-artifacts"


def test_no_endpoint_means_no_s3_settings(monkeypatch):
    from service.config import S3Settings
    monkeypatch.delenv("RECON_S3_ENDPOINT", raising=False)
    assert S3Settings.from_env() is None


# ---------------------------------------------------------------------------
# The artifact store's one new method
# ---------------------------------------------------------------------------

def test_the_s3_artifact_store_streams_what_it_stored(tmp_path):
    """Without this, `GET /runs/{id}/artifacts/{name}` 501s in the deployment being
    targeted — Railway allows one volume per service and no cross-service mounts,
    so the worker writes the bytes and the api cannot read them (defect 2.4)."""
    from service.artifacts import S3ArtifactStore

    objects = object_lib.S3Objects("recon-artifacts", client=FakeS3Client())
    store = S3ArtifactStore(objects)
    source = tmp_path / "finance_file.xlsx"
    source.write_bytes(b"workbook bytes")

    art = store.put(period="2026-05_l1", platform="lazada", run_id=7, path=source)
    assert art.uri == "s3://recon-artifacts/artifacts/2026-05_l1/lazada/run-7/finance_file.xlsx"
    assert art.sha256 == object_lib.digest_of(b"workbook bytes")
    # None deliberately: the file it would point at is the worker's scratch copy,
    # which `Worker._cleanup` is about to delete.
    assert art.local_path is None
    assert store.open(art.uri) is None

    assert b"".join(store.stream(art.uri)) == b"workbook bytes"


def test_streaming_an_old_file_uri_returns_none_rather_than_guessing(tmp_path):
    """A run made before this deployment moved to a bucket keeps its old address.
    The api turns None into a 404 that names the URI, which is the honest answer."""
    from service.artifacts import S3ArtifactStore

    store = S3ArtifactStore(object_lib.S3Objects("b", client=FakeS3Client()))
    assert store.stream("file:///C:/old/finance_file.xlsx") is None
    assert store.stream("s3://b/artifacts/never/written.xlsx") is None


def test_the_local_artifact_store_also_implements_stream(tmp_path):
    """One Protocol, not two. `hasattr(store, "stream")` at the call site is exactly
    the drift the deletable-wrapper tests exist to prevent."""
    from service.artifacts import LocalArtifactStore

    store = LocalArtifactStore(tmp_path / "artifacts")
    source = tmp_path / "run_log.txt"
    source.write_bytes(b"log lines")
    art = store.put(period="p", platform="lazada", run_id=1, path=source)
    assert b"".join(store.stream(art.uri)) == b"log lines"
    assert store.stream("file:///nowhere/at/all.txt") is None
