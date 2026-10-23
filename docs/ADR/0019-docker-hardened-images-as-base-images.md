# ADR-0019: Docker Hardened Images as the base of every image

- **Status:** ACCEPTED
- **Date:** 2026-10-23
- **Deciders:** DSS code owners
- **Informed:** Adopter engineering teams; OAN DPG steward

---

## 1. Context and Problem Statement

The DSS image was built on `python:3.13-slim`, and the compose files start
about ten other images from Docker Hub, ghcr.io, quay.io and a vendor
registry. Each one carries a shell, a package manager and a root user the
service never needs. Each is one more thing to scan, patch and explain.

**The question.** Can every image the repo builds or starts come from one
hardened source, and what does that cost us to run?

## 2. Decision Drivers

- **Fewer parts in each image.** Nothing to exploit that the service does not
  use.
- **Non-root by default.**
- **One source and one login**, not five registries.
- **No change to what the DSS does.** Only how it is packaged.

## 3. Considered Options

**A. Stay as we are.** Rejected: it is the problem.

**B. Harden our own image only.** The DSS image is the only one we publish,
so this is the smallest step. Rejected: the compose files are what an adopter
runs, and they start the other images.

**C. Docker Hardened Images (`dhi.io`) for everything.** Chosen.

## 4. Decision Outcome

Every image starts from `dhi.io`. The DSS Dockerfile has two stages: the
`-dev` Python image installs, the plain Python image runs. Both keep Python at
`/usr/bin/python3`, so the virtualenv copies across intact. The image runs as
user 65532.

| Was | Now |
|---|---|
| `python:3.13-slim`, `uv` from ghcr | `dhi.io/python:3.13-dev` then `dhi.io/python:3.13`; `dhi.io/uv:0.12` |
| `otel/opentelemetry-collector-contrib` | `dhi.io/opentelemetry-collector:0.150-contrib` |
| `eclipse-temurin:21-jre` | `dhi.io/eclipse-temurin:21-jre` |
| `clickhouse/clickhouse-server:25.12` | `dhi.io/clickhouse-server:25.12` |
| `postgres:17`, `postgres:16-alpine` | `dhi.io/postgres:17`, `dhi.io/postgres:16-alpine` |
| `redis:7` | `dhi.io/redis:7` |
| `grafana/grafana` | `dhi.io/grafana:12` |
| `cgr.dev/chainguard/minio` | `dhi.io/minio:latest` |
| `ghcr.io/berriai/litellm` | `dhi.io/litellm:1` |
| `docker.langfuse.com/langfuse/*:4` | `dhi.io/langfuse:4`, `dhi.io/langfuse-worker:4` |

The collector must be the `contrib` build. The plain build has no `transform`
processor and no ClickHouse exporter, which `otel/collector.yaml` uses.

## 5. Consequences

**What we gain.** One registry, non-root everywhere, and images with no shell
or package manager in most of them.

**What it costs to run.**

- **A login.** Pulling needs `docker login dhi.io`. CI needs the
  `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets. A PR from a fork cannot build.
- **No shell in several images.** Photon (Java), Redis, Grafana, the collector
  and the DSS image have none. Photon lost its healthcheck, since it had only
  `curl`. ClickHouse and MinIO check themselves with bash instead of
  `wget` and `mc`.
- **Photon's index download is now a Python script**
  (`scripts/photon_index.py`), because the Java image has no `curl` or
  `bzip2`. It runs as root and hands the files to Photon's user.
- **Existing Postgres and Redis volumes need a one-time owner change.**
  `docs/RUNNING.md` gives the commands. Postgres volumes mount at the image's
  data directory.
- **Langfuse prints `rm: not found`** during its ClickHouse migration. The
  image has no `rm`; the script only uses it to delete a temporary folder.
  Harmless, but one stray folder is left per start.
- **Tags are chosen, not `latest`.** DHI has no `latest` for most images.
  Upgrading is a tag change.

**Not done.** Images are not pinned by digest.
