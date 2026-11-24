# Configuration

Settings come from, in order (later wins):

1. the defaults below;
2. `~/.fixplan.yml` (or `--config path`) — `fixplan init` writes a sample;
3. environment variables `FIXPLAN_<KEY>` (and `OLLAMA_HOST`);
4. command-line flags.

There is no per-upload or per-repo config file on purpose: an upload is untrusted and must
not be able to change where its content is sent or how it is judged.

| Key | Environment | Default |
|---|---|---|
| `ollama_host` | `FIXPLAN_OLLAMA_HOST` | `http://localhost:11434` |
| `model` | `FIXPLAN_MODEL` | `qwen2.5-coder:14b` |
| `api_key` | `FIXPLAN_API_KEY` | `` |
| `language` | `FIXPLAN_LANGUAGE` | `en` |
| `temperature` | `FIXPLAN_TEMPERATURE` | `0.1` |
| `num_ctx` | `FIXPLAN_NUM_CTX` | `16384` |
| `timeout` | `FIXPLAN_TIMEOUT` | `600` |
| `deadline` | `FIXPLAN_DEADLINE` | `0` |
| `num_predict` | `FIXPLAN_NUM_PREDICT` | `4096` |
| `keep_alive` | `FIXPLAN_KEEP_ALIVE` | `30m` |
| `retries` | `FIXPLAN_RETRIES` | `2` |
| `workers` | `FIXPLAN_WORKERS` | `1` |
| `use_llm` | `FIXPLAN_USE_LLM` | `True` |
| `use_lint` | `FIXPLAN_USE_LINT` | `True` |
| `use_static` | `FIXPLAN_USE_STATIC` | `True` |
| `max_files` | `FIXPLAN_MAX_FILES` | `400` |
| `max_file_chars` | `FIXPLAN_MAX_FILE_CHARS` | `20000` |
| `max_upload_mb` | `FIXPLAN_MAX_UPLOAD_MB` | `50` |
| `chunk_chars` | `FIXPLAN_CHUNK_CHARS` | `14000` |
| `min_confidence` | `FIXPLAN_MIN_CONFIDENCE` | `0.5` |
| `min_severity` | `FIXPLAN_MIN_SEVERITY` | `info` |
| `fail_on` | `FIXPLAN_FAIL_ON` | `high` |
| `max_steps` | `FIXPLAN_MAX_STEPS` | `12` |
| `goal` | `FIXPLAN_GOAL` | `` |
| `house_rules` | `FIXPLAN_HOUSE_RULES` | `` |
| `disabled_rules` | `FIXPLAN_DISABLED_RULES` | `` |
| `exclude` | `FIXPLAN_EXCLUDE` | `.git/**, **/.git/**, **/node_modules/**, **/.venv/**, **/ven…` |

## House rules

`house_rules` is appended to every model prompt as instructions from the operator. Use it
for the things a generic model can't know:

```yaml
house_rules: |
  We run OpenShift 4: containers get a random UID with group 0 and never run as root.
  Images come from quay.example.internal, never from Docker Hub.
  Secrets live in Vault and reach pods through the Vault agent injector.
  Plans must keep changes GitOps-friendly: change the repo, ArgoCD applies it.
```

## Goal

`goal` (or `-g` / the Goal field in the web UI) tells the model what you are trying to
achieve, so the plan is ordered around it: *"make it run on OpenShift"*, *"pass the CIS
benchmark"*, *"find why the pod restarts every 10 minutes"*.
