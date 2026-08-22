FROM golang:1.25.1-bookworm@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee AS build
ENV GO111MODULE=on CGO_ENABLED=0 GOWORK=off

ARG TARGETOS
ARG TARGETARCH
ENV GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64}
ENV GOEXPERIMENT=greenteagc

WORKDIR /build

ADD go.mod go.sum ./
ADD relaykit/go.mod ./relaykit/go.mod
RUN go mod download

COPY . .
COPY --from=harness main.go ./cmd/relay-fault-harness/main.go
RUN go build -trimpath -ldflags "-s -w" -o /out/relay-fault-harness ./cmd/relay-fault-harness

FROM alpine:3.22
RUN apk add --no-cache ca-certificates tzdata
COPY --from=build /out/relay-fault-harness /relay-fault-harness
USER 65532:65532
ENTRYPOINT ["/relay-fault-harness"]
