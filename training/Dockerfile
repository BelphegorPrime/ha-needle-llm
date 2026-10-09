# Offline-capable training image. Build once (online), train later (network disabled).
# Keep this Needle version aligned with ha-addon-collection/addon-needle.
FROM python:3.11-slim-bookworm

ARG NEEDLE_VERSION=3.1.3
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JAX_PLATFORMS=cpu \
    NEEDLE_TELEMETRY=0 \
    DO_NOT_TRACK=1

RUN python -m pip install --no-cache-dir "cactus-needle[train]==${NEEDLE_VERSION}" \
    && python -m pip check

WORKDIR /work
CMD ["needle", "--help"]
