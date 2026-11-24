# Deploy

## Docker

```bash
docker run -d --name fixplan -p 127.0.0.1:8766:8766 \
  --add-host=host.docker.internal:host-gateway \
  -e FIXPLAN_WEB_TOKEN="$(openssl rand -hex 24)" \
  -v fixplan-data:/home/fixplan/.cache/fixplan \
  ghcr.io/jwklijnsma/fixplan
```

The image listens on `0.0.0.0:8766` and **refuses to start without a token**. It contains
shellcheck, hadolint, ruff and yamllint, runs as UID 1000 (any UID with group 0 works, so
OpenShift's random UIDs are fine) and has a health check on `/healthz`. Ollama is not in the
image: `OLLAMA_HOST` defaults to the Docker host.

## Podman Quadlet (systemd)

[`deploy/systemd/fixplan.container`](https://github.com/jwklijnsma/fixplan/blob/main/deploy/systemd/fixplan.container)
runs it rootless, read-only, with no capabilities, the token in a Podman secret and daily
image updates:

```bash
openssl rand -hex 24 | tr -d '\n' | podman secret create fixplan-token -
mkdir -p ~/.config/containers/systemd ~/.config/fixplan
cp deploy/systemd/fixplan.container ~/.config/containers/systemd/
install -m 600 deploy/systemd/fixplan.env.example ~/.config/fixplan/fixplan.env
systemctl --user daemon-reload && systemctl --user start fixplan
loginctl enable-linger "$USER"
```

## OpenShift / Kubernetes

The image needs no root and no writable root filesystem except `~/.cache/fixplan` (mount a
PVC there to keep plans) and `/tmp`. Put the token in a Secret as `FIXPLAN_WEB_TOKEN` and
set `OLLAMA_HOST` to your Ollama Service.

## Releases

`make release` (or Actions → release) bumps the version, tags, builds the wheel, creates the
GitHub release and publishes `ghcr.io/jwklijnsma/fixplan:X.Y.Z`, `:X.Y` and `:latest`.
Merged PRs release on their own (patch by default; label `release:minor` / `release:major`).
