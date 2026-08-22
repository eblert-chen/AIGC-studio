FROM golang:1.25.1-bookworm AS build

WORKDIR /src
COPY go.mod go.sum ./
COPY relaykit/go.mod relaykit/go.sum ./relaykit/
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/relay-real-channel-acceptance ./cmd/relay-real-channel-acceptance

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/relay-real-channel-acceptance /usr/local/bin/relay-real-channel-acceptance
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/relay-real-channel-acceptance"]
