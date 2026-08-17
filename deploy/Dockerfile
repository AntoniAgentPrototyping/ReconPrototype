# One image, two commands — the api and the worker differ only in entrypoint.
#
# Built and verified on 2026-08-14 — see docs/07-VERIFICATION.md. Through M4 this
# file had never been built and was recorded as an open defect; that is closed.
#
# Why one image and not two: the worker imports `src/` to run the pipeline and
# the api imports `src/` for RunStatus and EXIT_CODES. Two images would be two
# dependency sets to keep in step, and a version skew between them would show up
# as an exit code meaning different things at each end of the same run.

FROM python:3.12-slim AS base

# 3.12, not 3.13/3.14: pandas 2.x has no cp314 wheels and the pandas <3 pin is a
# Copy-on-Write control, not laziness (docs/06-DECISIONS.md#d20). Bumping either
# is gated on the golden comparison, not on a green suite.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The deployable unit: src/ + service/. NOT tools/ and NOT tests/ — that absence
# is the reason build_context lives in src/pipeline.py rather than being imported
# from tools/full_run.py (tests/service/test_service_is_deletable.py).
#
# config/ ships too: settings.yaml is the domain contract, versioned in git, and
# the worker cannot map a column without it (docs/06-DECISIONS.md#d2).
#
# Sources are copied BEFORE the install rather than after, because
# `pip install .` builds a wheel and hatchling needs the packages to exist. The
# alternative — restating the dependency list in a RUN line to get a cacheable
# layer — buys build speed by creating a second copy of the dependencies that
# drifts from pyproject.toml. Correctness first; this image is built rarely.
COPY pyproject.toml README.md ./
COPY src/ src/
COPY service/ service/
COPY config/ config/
RUN pip install --no-cache-dir ".[service]"

# What is deliberately NOT in the image:
#
#   config/Lib & VAT rate.xlsb  — a team-owned live file. How a server obtains it
#                                 is an unresolved standing risk
#                                 (docs/10-ROADMAP.md); mount it, do not bake a
#                                 stale copy that would silently diverge from the
#                                 team's own rules (docs/06-DECISIONS.md#d8).
#   input/                      — there is no input mount since M6. Exports arrive
#                                 through the browser and live in the recon-uploads
#                                 bucket; the worker materialises each window into
#                                 its own scratch dir (service/materialize.py).
#   artifacts/                  — the recon-artifacts bucket, streamed by the api
#                                 through its own authorization. Never a presigned
#                                 URL (docs/06-DECISIONS.md#d43).
#
# The committed config/lazada_*.csv snapshots ARE present, so a run without the
# live master falls back and reports drift rather than failing.

# Non-root, and it must own the volumes it writes.
RUN useradd --create-home --uid 10001 recon \
 && mkdir -p /data/scratch \
 && chown -R recon:recon /app /data
USER recon

# INPUT_ROOT and ARTIFACT_ROOT are still set and still used: they are the
# local-directory fallback for a deployment with no RECON_S3_ENDPOINT, which is a
# supported mode rather than a broken one (service/objects.py). With S3 configured
# neither is read.
ENV RECON_INPUT_ROOT=/data/scratch/input \
    RECON_ARTIFACT_ROOT=/data/scratch/artifacts \
    RECON_SCRATCH_ROOT=/data/scratch \
    RECON_CONFIG_DIR=/app/config \
    RECON_API_HOST=0.0.0.0

# Memory: peak RSS measured 832 MB on the largest window, 41% of a 4 GB limit,
# and that 50%-of-container figure is the engine-port trigger
# (docs/10-ROADMAP.md). Set the worker's limit to 4 GB or the trigger measures
# nothing. `metrics.peak_rss_mb` reads getrusage in here, not the Windows branch.

EXPOSE 8080

# ---------------------------------------------------------------------------
FROM base AS api
# Authenticated since M5 and password-based since M6 (service/auth.py). Still not
# a port to publish: the browser reaches the api through `web`, and an api that can
# queue settlement runs and read every store's revenue belongs behind a front door.
# Entra ID SSO remains unbuilt (docs/13-ENTRA-SETUP.md).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "service.api", "--host", "0.0.0.0", "--port", "8080"]

# ---------------------------------------------------------------------------
FROM base AS worker
# One job at a time per process: settings["_vat_sku"] is a mutable back-channel
# and two concurrent runs sharing a settings dict cross-contaminate VAT rates.
# Scale by replicas — which is what FOR UPDATE SKIP LOCKED is for — never by
# threads inside one worker.
CMD ["python", "-m", "service.worker"]
