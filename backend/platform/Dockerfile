FROM python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df

ARG INSTALL_OBS=true
ARG PLATFORM_SOURCE_REVISION=0000000000000000000000000000000000000000
ARG PLATFORM_SOURCE_SNAPSHOT_SHA256=sha256:0000000000000000000000000000000000000000000000000000000000000000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN apk add --no-cache ffmpeg=8.1.2-r0 \
    && ffprobe -version \
    && ffmpeg -hide_banner -encoders 2>&1 | grep -Eq '(^|[[:space:]])libx264([[:space:]]|$)' \
    && ffmpeg -hide_banner -encoders 2>&1 | grep -Eq '(^|[[:space:]])aac([[:space:]]|$)' \
    && pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OBS" = "true" ]; then pip install "esdk-obs-python>=3.25,<4"; fi \
    && printf '%s' "$PLATFORM_SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$' \
    && test "$PLATFORM_SOURCE_REVISION" != '0000000000000000000000000000000000000000' \
    && printf '%s' "$PLATFORM_SOURCE_SNAPSHOT_SHA256" | grep -Eq '^sha256:[0-9a-f]{64}$' \
    && test "$PLATFORM_SOURCE_SNAPSHOT_SHA256" != 'sha256:0000000000000000000000000000000000000000000000000000000000000000' \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app \
    && mkdir -p /app/data/platform-input-assets \
    && chown -R app:app /app/data

# Verify exactly the source bytes that are subsequently installed.  There is
# deliberately no second COPY of runtime Python after this check.
COPY platform_api /tmp/platform-source/platform_api
COPY migrations /tmp/platform-source/migrations
COPY requirements.txt alembic.ini Dockerfile .dockerignore /tmp/platform-source/
COPY scripts/platform_source_snapshot.py /tmp/platform-source/scripts/platform_source_snapshot.py
RUN python /tmp/platform-source/scripts/platform_source_snapshot.py \
        --root /tmp/platform-source \
        --expected "$PLATFORM_SOURCE_SNAPSHOT_SHA256" \
    && cp -a /tmp/platform-source/platform_api /app/platform_api \
    && cp -a /tmp/platform-source/migrations /app/migrations \
    && cp /tmp/platform-source/alembic.ini /app/alembic.ini \
    && printf '{"platform_source_revision":"%s","platform_source_snapshot_sha256":"%s"}' \
        "$PLATFORM_SOURCE_REVISION" "$PLATFORM_SOURCE_SNAPSHOT_SHA256" \
        > /app/platform-release-identity.json \
    && chmod 0444 /app/platform-release-identity.json \
    && chown -R app:app /app/platform_api /app/migrations /app/alembic.ini \
    && rm -rf /tmp/platform-source

USER app
EXPOSE 8000

CMD ["uvicorn", "platform_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
