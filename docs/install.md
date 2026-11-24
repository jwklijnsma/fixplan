# Install

## pip

```bash
git clone https://github.com/jwklijnsma/fixplan.git && cd fixplan
pip install ".[lint]"      # Python 3.10+; adds yamllint, ruff and hadolint
sudo dnf install ShellCheck   # or: apt install shellcheck  (optional)
fixplan doctor
```

Each release also has a wheel on the
[releases page](https://github.com/jwklijnsma/fixplan/releases).

## Ollama and a model

```bash
ollama pull qwen2.5-coder:14b      # good default on 16 GB VRAM
# ollama pull qwen2.5-coder:32b    # better plans with 24 GB+
fixplan doctor --host http://gpu-box:11434
```

`doctor` checks that Ollama answers, the model is pulled, measures tokens per second, and
lists the linters it found. Without Ollama, `fixplan --no-llm` still makes a plan from the
built-in checks and linters.

!!! tip "Context size"
    The plan request contains all findings plus the most affected files. `num_ctx: 16384`
    is a good default; raise it to 32768 for big uploads if your GPU has room
    (`OLLAMA_CONTEXT_LENGTH` on the server, `num_ctx` here).

## Container

```bash
docker run --rm -v "$PWD:/work" ghcr.io/jwklijnsma/fixplan plan /work -o /work/.fixplan
```

See [Deploy](deploy.md) for the web UI as a service.
