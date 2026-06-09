FROM golang:1.25-alpine AS build

RUN apk add --no-cache git

# Pin Zoekt to a known commit for reproducible patch application.
ARG ZOEKT_COMMIT=071adfde901e3e10992ec5afc11f54984ab7e7fc
ARG ZOEKT_REPO=https://github.com/sourcegraph/zoekt.git

WORKDIR /src
RUN git clone --depth=1 "$ZOEKT_REPO" zoekt \
  && cd zoekt \
  && git fetch --depth=1 origin "$ZOEKT_COMMIT" \
  && git checkout "$ZOEKT_COMMIT"

# Apply local patches.
COPY patches/ /patches/
RUN chmod +x /patches/apply-patches.sh /patches/*.sh \
  && sh /patches/apply-patches.sh /src/zoekt

# Build patched binaries.
RUN cd /src/zoekt \
  && go build -o /go/bin/zoekt-git-index ./cmd/zoekt-git-index \
  && go build -o /go/bin/zoekt-index ./cmd/zoekt-index \
  && go build -o /go/bin/zoekt-webserver ./cmd/zoekt-webserver

FROM alpine:3.22

RUN apk add --no-cache bash ca-certificates git

COPY --from=build /go/bin/zoekt-git-index /usr/local/bin/zoekt-git-index
COPY --from=build /go/bin/zoekt-index /usr/local/bin/zoekt-index
COPY --from=build /go/bin/zoekt-webserver /usr/local/bin/zoekt-webserver
