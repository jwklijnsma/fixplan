# syntax=docker/dockerfile:1
#
# fixplan: upload code, config or logs and get a fix plan from a local Ollama model.
#
#   docker run -d -p 127.0.0.1:8766:8766 \
#     --add-host=host.docker.internal:host-gateway \
#     -e FIXPLAN_WEB_TOKEN="$(openssl rand -hex 24)" \
#     -v fixplan-data:/home/fixplan/.cache/fixplan ghcr.io/jwklijnsma/fixplan
#
#   One-shot on a folder:
#   docker run --rm -v "$PWD:/work" ghcr.io/jwklijnsma/fixplan plan /work -o /work/.fixplan
#
# Ollama is not in this image; point OLLAMA_HOST at it (default: the Docker host).

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- build
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# ".[lint]" brings yamllint, ruff and hadolint along; shellcheck comes from Debian below.
# hadolint ignore=DL3013
RUN pip install ".[lint]" \
 && fixplan --version \
 && hadolint --version \
 && ruff --version

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="fixplan" \
      org.opencontainers.image.description="Upload code, config or logs and get a step-by-step fix plan from a local Ollama model" \
      org.opencontainers.image.source="https://github.com/jwklijnsma/fixplan" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# $HOME is writable for any UID (docker --user, OpenShift's random UIDs): finished plans
# live in ~/.cache/fixplan/jobs. Mount a volume there to keep them.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends shellcheck tini ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --gid 0 --home-dir /home/fixplan --create-home --shell /usr/sbin/nologin fixplan \
 && mkdir -p /home/fixplan/.cache/fixplan \
 && chmod 1777 /home/fixplan /home/fixplan/.cache /home/fixplan/.cache/fixplan

COPY --from=build /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    HOME=/home/fixplan \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUFF_CACHE_DIR=/tmp/ruff \
    OLLAMA_HOST=http://host.docker.internal:11434

USER 1000:0
WORKDIR /home/fixplan
EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8766/healthz', timeout=4); sys.exit(0)"]

STOPSIGNAL SIGINT
ENTRYPOINT ["tini", "--", "fixplan"]
CMD ["serve", "--bind", "0.0.0.0", "--port", "8766"]
