"""Configuration: defaults < ~/.fixplan.yml (or --config) < FIXPLAN_* env < CLI flags.

There is deliberately no per-upload config file: an upload is untrusted input and must not
be able to change where its contents are sent or how they are judged.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import os
from pathlib import Path

import yaml

from .models import Severity

USER_CONFIG = Path.home() / ".fixplan.yml"


@dataclass
class Config:
    ollama_host: str = "http://localhost:11434"
    model: str = "qwen2.5-coder:14b"
    api_key: str = ""                 # for an Ollama behind an auth proxy
    language: str = "en"              # "en" or "nl": language of the plan
    temperature: float = 0.1
    num_ctx: int = 16384
    timeout: int = 600                # max seconds without output from Ollama
    deadline: int = 0                 # max seconds for one request (0 = 2 x timeout)
    num_predict: int = 4096
    keep_alive: str = "30m"
    retries: int = 2
    workers: int = 1                  # parallel review requests (1 = one GPU)
    use_llm: bool = True
    use_lint: bool = True             # run linters that are installed (shellcheck, hadolint, ...)
    use_static: bool = True           # built-in FP0xx checks
    max_files: int = 400              # stop collecting after this many text files
    max_file_chars: int = 20000       # truncate a file beyond this for the model
    max_upload_mb: int = 50           # web UI / archive limit (unpacked size)
    chunk_chars: int = 14000          # split a big file into chunks of this size for review
    min_confidence: float = 0.5       # drop model findings below this
    min_severity: str = "info"        # hide findings below this
    fail_on: str = "high"             # exit 1 at or above this severity
    max_steps: int = 12               # plan length cap
    goal: str = ""                    # what the user wants ("make this pass CIS", "fix the crash")
    house_rules: str = ""             # extra instructions for the model
    disabled_rules: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=lambda: [
        ".git/**", "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**",
        "**/__pycache__/**", "**/.terraform/**", "**/dist/**", "**/build/**",
        "**/vendor/**", "**/*.min.js", "**/*.lock", "**/package-lock.json",
    ])

    @property
    def fail_on_severity(self) -> Severity:
        return Severity.parse(self.fail_on, Severity.HIGH)

    @property
    def min_severity_level(self) -> Severity:
        return Severity.parse(self.min_severity, Severity.INFO)

    def update(self, data: dict, source: str = "") -> list[str]:
        warnings = []
        known = {f.name: f for f in fields(self)}
        for key, value in (data or {}).items():
            key = key.replace("-", "_")
            if key not in known:
                warnings.append(f"{source}: unknown setting '{key}' ignored")
                continue
            current = getattr(self, key)
            try:
                if isinstance(current, bool):
                    value = value if isinstance(value, bool) else str(value).lower() in (
                        "1", "true", "yes", "on")
                elif isinstance(current, int):
                    value = int(value)
                elif isinstance(current, float):
                    value = float(value)
                elif isinstance(current, list):
                    value = value if isinstance(value, list) else [
                        v.strip() for v in str(value).split(",") if v.strip()]
                else:
                    value = str(value)
            except (TypeError, ValueError):
                warnings.append(f"{source}: bad value for '{key}': {value!r}")
                continue
            setattr(self, key, value)
        return warnings


def load_config(path: str | None = None, overrides: dict | None = None) -> tuple[Config, list[str]]:
    cfg = Config()
    warnings: list[str] = []
    file = Path(path).expanduser() if path else USER_CONFIG
    if file.is_file():
        try:
            data = yaml.safe_load(file.read_text()) or {}
            if not isinstance(data, dict):
                raise ValueError("not a mapping")
            warnings += cfg.update(data, str(file))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            warnings.append(f"{file}: cannot read ({exc})")
    elif path:
        warnings.append(f"{file}: not found")
    env = {}
    if os.environ.get("OLLAMA_HOST"):
        host = os.environ["OLLAMA_HOST"]
        env["ollama_host"] = host if "://" in host else f"http://{host}"
    for f in fields(cfg):
        name = "FIXPLAN_" + f.name.upper()
        if name in os.environ:
            env[f.name] = os.environ[name]
    warnings += cfg.update(env, "environment")
    warnings += cfg.update({k: v for k, v in (overrides or {}).items() if v is not None}, "flags")
    return cfg, warnings


SAMPLE = """\
# fixplan settings (~/.fixplan.yml). Every key can also be set as FIXPLAN_<KEY>.
ollama_host: http://localhost:11434
model: qwen2.5-coder:14b
language: en            # en or nl
workers: 1              # parallel model requests; raise if Ollama has OLLAMA_NUM_PARALLEL > 1
num_ctx: 16384
fail_on: high
min_confidence: 0.5
max_steps: 12
# goal: "harden for production"     # default goal for every plan
# house_rules: |
#   We run OpenShift: containers run as a random UID, never as root.
#   Images come from our Quay mirror, not Docker Hub.
# disabled_rules: [FP007]
"""
