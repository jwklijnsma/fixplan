# fixplan

[![Docker](https://github.com/jwklijnsma/fixplan/actions/workflows/docker.yml/badge.svg)](https://github.com/jwklijnsma/fixplan/actions/workflows/docker.yml)
[![Docs](https://github.com/jwklijnsma/fixplan/actions/workflows/docs.yml/badge.svg)](https://jwklijnsma.github.io/fixplan/)

**Upload anything — code, config, manifests, pipelines, logs, a stack trace — and get back a
step-by-step plan to fix it.** Everything runs on your own hardware with a local Ollama model.

The sibling of [ansreview](https://github.com/jwklijnsma/ansreview) (Ansible) and
[helmreview](https://github.com/jwklijnsma/helmreview) (Helm), but for everything else.

1. **Detect** what each file is: Dockerfile, Kubernetes/OpenShift, Helm, Ansible, Terraform,
   Compose, GitHub Actions, GitLab CI, shell, Python, Go, JS, nginx, systemd, sshd, logs, … (`fixplan types`)
2. **Built-in checks (FP0xx)**: hard-coded secrets, committed keys, `curl | sh`, `chmod 777`,
   TLS verification off, privileged pods, unpinned images, root containers, and known failure
   signatures in logs (OOM, disk full, x509, DNS, CrashLoopBackOff, …).
3. **Linters when installed**: shellcheck, hadolint, ruff, yamllint (all in the container image).
4. **Model review**: every file (big files in chunks) goes past the model, which is told what
   was already found and looks for the rest — and for logs, the root cause.
5. **The plan**: ordered steps — *why*, *what to do*, *commands*, a *patch*, *how to verify*,
   *risk* — grouped so one change fixes related findings. Leaked secrets come first, then what
   breaks, then hardening.

No content leaves your network. Output: Markdown, JSON and SARIF. Without Ollama it still
works (rules + linters, plan ordered by severity).

## Quick start

```bash
git clone https://github.com/jwklijnsma/fixplan.git && cd fixplan
pip install ".[lint]"           # Python 3.10+; shellcheck comes from your distro
ollama pull qwen2.5-coder:14b   # any coding model; bigger is better if VRAM allows
fixplan doctor                  # checks Ollama, the model, its speed and the linters

fixplan ./my-service --host http://gpu-box:11434        # plan in .fixplan/plan.md
fixplan repo.zip -g "make it run on OpenShift"           # with a goal
kubectl logs deploy/api --previous | fixplan - --name api.log   # a log from stdin
fixplan serve --open                                     # web UI on http://localhost:8766
```

## Commands

| Command | |
|---|---|
| `fixplan [plan] <files, folders, .zip/.tar.gz, ->` | Make a fix plan (default command) |
| `fixplan serve` | Web UI: drop files, a folder or an archive, or paste text |
| `fixplan doctor` | Check Ollama, the model and its speed, and the linters |
| `fixplan rules` | List the built-in FP rules |
| `fixplan types` | List the file types it recognises |
| `fixplan init` | Write a sample `~/.fixplan.yml` |

Useful flags for `plan`: `-g/--goal`, `--lang nl`, `--no-llm`, `-f md,json,sarif`,
`-o <dir>`, `--fail-on high|never`, `--max-steps 8`, `--model`, `--host`, `--workers`.

Exit code `1` when a finding is at or above `--fail-on` (default `high`), `2` on a tool
error, `0` otherwise — so it can gate a pipeline.

## Web UI

```bash
fixplan serve                                   # localhost only, no token needed
FIXPLAN_WEB_TOKEN=$(openssl rand -hex 24) fixplan serve --bind 0.0.0.0
```

Drop files, a whole folder or a ZIP/tar, or paste a log; add a goal; get the plan with
copyable commands and patches, and download it as Markdown, JSON or SARIF. Jobs run one at
a time (one GPU) and finished plans survive a restart.

Security: a non-local bind needs a token; without one, cross-site requests are refused.
Archives are unpacked in memory with path-traversal, link, size and file-count checks.
Uploaded content is never executed and is passed to the model as data, with an explicit
instruction not to follow instructions inside it. There is no per-upload config: an upload
can't change the model, server or rules.

## Container

```bash
docker run -d --name fixplan -p 127.0.0.1:8766:8766 \
  --add-host=host.docker.internal:host-gateway \
  -e FIXPLAN_WEB_TOKEN="$(openssl rand -hex 24)" \
  -v fixplan-data:/home/fixplan/.cache/fixplan \
  ghcr.io/jwklijnsma/fixplan
```

As a service: [`deploy/systemd/fixplan.container`](deploy/systemd/fixplan.container)
(Podman Quadlet, rootless, read-only).

## Configuration

`~/.fixplan.yml` (`fixplan init`), every key also as `FIXPLAN_<KEY>`, flags win. The useful
ones: `model`, `ollama_host`, `language` (en/nl), `workers`, `num_ctx`, `goal`,
`house_rules` (e.g. *"We run OpenShift: random UIDs, images from our Quay mirror"*),
`disabled_rules`, `max_steps`, `min_confidence`, `max_upload_mb`.

📖 Full docs: <https://jwklijnsma.github.io/fixplan/>

## Development

```bash
make install && make test
```

Tests use a mock Ollama server (`tests/mock_ollama.py`), so they need no GPU.
