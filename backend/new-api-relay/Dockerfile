FROM oven/bun:1@sha256:0733e50325078969732ebe3b15ce4c4be5082f18c4ac1a0f0ca4839c2e4e42a7 AS builder

WORKDIR /build/web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY ./web ./
COPY ./VERSION /build/VERSION
RUN version="$(cat /build/VERSION)" \
    && test "$(wc -l < /build/VERSION)" -eq 1 \
    && test -n "$version" \
    && test "${#version}" -le 50 \
    && printf '%s' "$version" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+([+.-][0-9A-Za-z.+-]+)?$'
RUN DISABLE_ESLINT_PLUGIN='true' VITE_REACT_APP_VERSION=$(cat /build/VERSION) bun run build

FROM golang:1.25.1-bookworm@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee AS builder2
ENV GO111MODULE=on CGO_ENABLED=0 GOWORK=off

ARG TARGETOS
ARG TARGETARCH
ARG RELAY_BUILD_UPSTREAM_REVISION=unknown
ARG RELAY_BUILD_SOURCE_REVISION=unknown
ARG RELAY_BUILD_SOURCE_SNAPSHOT_SHA256=unknown
ARG RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT=unknown
ARG RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256=unknown
ENV GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64}
ENV GOEXPERIMENT=greenteagc

WORKDIR /build

ADD go.mod go.sum ./
# relaykit is a local submodule referenced via replace; its go.mod must be
# present for go mod download to resolve the main module graph.
ADD relaykit/go.mod ./relaykit/go.mod
RUN go mod download

COPY . .
COPY --from=builder /build/web/dist ./web/dist
RUN version="$(cat VERSION)" \
    && test "$(wc -l < VERSION)" -eq 1 \
    && test -n "$version" \
    && test "${#version}" -le 50 \
    && printf '%s' "$version" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+([+.-][0-9A-Za-z.+-]+)?$'
RUN go build -ldflags "-s -w -X 'github.com/QuantumNous/new-api/common.Version=$(cat VERSION)' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledUpstreamRevision=${RELAY_BUILD_UPSTREAM_REVISION}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledSourceRevision=${RELAY_BUILD_SOURCE_REVISION}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotSHA256=${RELAY_BUILD_SOURCE_SNAPSHOT_SHA256}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotFileCount=${RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledRouteAcceptanceKeysSHA256=${RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256}'" -o new-api
RUN go build -ldflags "-s -w -X 'github.com/QuantumNous/new-api/common.Version=$(cat VERSION)' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledUpstreamRevision=${RELAY_BUILD_UPSTREAM_REVISION}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledSourceRevision=${RELAY_BUILD_SOURCE_REVISION}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotSHA256=${RELAY_BUILD_SOURCE_SNAPSHOT_SHA256}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotFileCount=${RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT}' -X 'github.com/QuantumNous/new-api/service.platformRelayCompiledRouteAcceptanceKeysSHA256=${RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256}'" -o relay-download-edge ./cmd/relay-download-edge

FROM debian:bookworm-slim@sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata libasan8 wget \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates \
    && groupadd --gid 10001 relayedge \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin relayedge \
    && install -d -o relayedge -g relayedge /data /app/logs /artifacts

COPY --from=builder2 /build/new-api /
COPY --from=builder2 /build/relay-download-edge /
COPY LICENSE NOTICE THIRD-PARTY-LICENSES.md /licenses/
EXPOSE 3000
WORKDIR /data
USER 10001:10001
ENTRYPOINT ["/new-api"]
