"""Work out what kind of file something is, from its name first and its content second."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

# kind -> human label (also the list `fixplan types` prints)
KINDS = {
    "dockerfile": "Dockerfile / Containerfile",
    "compose": "Docker Compose",
    "kubernetes": "Kubernetes / OpenShift manifest",
    "helm": "Helm chart",
    "kustomize": "Kustomize",
    "ansible": "Ansible",
    "terraform": "Terraform / OpenTofu",
    "github-actions": "GitHub Actions workflow",
    "gitlab-ci": "GitLab CI",
    "shell": "Shell script",
    "python": "Python",
    "javascript": "JavaScript / TypeScript",
    "go": "Go",
    "java": "Java / Kotlin",
    "php": "PHP",
    "sql": "SQL",
    "nginx": "nginx config",
    "apache": "Apache httpd config",
    "systemd": "systemd unit",
    "haproxy": "HAProxy config",
    "sshd": "sshd config",
    "powershell": "PowerShell",
    "log": "Log / error output",
    "yaml": "YAML",
    "json": "JSON",
    "ini": "INI / config",
    "markdown": "Markdown / text",
    "text": "Text",
}

_EXT = {
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ksh": "shell",
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".ts": "javascript",
    ".tsx": "javascript", ".jsx": "javascript",
    ".go": "go", ".java": "java", ".kt": "java", ".kts": "java", ".php": "php",
    ".sql": "sql", ".tf": "terraform", ".tfvars": "terraform", ".hcl": "terraform",
    ".ps1": "powershell", ".psm1": "powershell",
    ".service": "systemd", ".timer": "systemd", ".socket": "systemd", ".mount": "systemd",
    ".container": "systemd", ".path": "systemd",
    ".log": "log", ".out": "log", ".err": "log", ".trace": "log",
    ".json": "json", ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".toml": "ini",
    ".properties": "ini", ".env": "ini",
    ".md": "markdown", ".rst": "markdown", ".txt": "text",
    ".yml": "yaml", ".yaml": "yaml",
}

_LOG_LINE = re.compile(
    r"^(\d{4}-\d\d-\d\d[T ]\d\d:\d\d|\w{3} [ \d]\d \d\d:\d\d:\d\d|\[\d+\.\d+\]|"
    r"(ERROR|WARN(ING)?|INFO|DEBUG|FATAL|TRACE)\b|Traceback \(most recent call last\)|"
    r"\s+at [\w.$]+\(.*\)$|E\d{4} \d\d:\d\d)", re.M)


def detect(path: str, text: str) -> str:
    p = PurePosixPath(path)
    name = p.name.lower()
    parts = [x.lower() for x in p.parts]
    ext = p.suffix.lower()

    if name in ("dockerfile", "containerfile") or name.startswith(("dockerfile.", "containerfile.")) \
            or name.endswith((".dockerfile", ".containerfile")):
        return "dockerfile"
    if name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") \
            or name.startswith("docker-compose."):
        return "compose"
    if name == ".gitlab-ci.yml" or (ext in (".yml", ".yaml") and "gitlab-ci" in name):
        return "gitlab-ci"
    if ".github" in parts and "workflows" in parts and ext in (".yml", ".yaml"):
        return "github-actions"
    if name in ("chart.yaml", "values.yaml") or ("templates" in parts and _in_helm(parts)) \
            or name.endswith(".tpl"):
        return "helm"
    if name in ("kustomization.yaml", "kustomization.yml"):
        return "kustomize"
    if ext in ("", ".conf") and (name == "nginx.conf" or "nginx" in parts[:-1]
                                 or "sites-enabled" in parts or "conf.d" in parts
                                 and re.search(r"^\s*(server|location)\s.*\{", text, re.M)):
        return "nginx"
    if name in ("httpd.conf", "apache2.conf", ".htaccess"):
        return "apache"
    if name == "haproxy.cfg":
        return "haproxy"
    if name in ("sshd_config",) or ("sshd_config.d" in parts):
        return "sshd"
    if name in ("makefile",):
        return "shell"
    if ext in (".yml", ".yaml"):
        return _yaml_kind(parts, text)
    if ext == ".conf" and re.search(r"^\s*(server|location|upstream)\s.*\{", text, re.M):
        return "nginx"
    if ext == ".conf" and re.search(r"^\s*<VirtualHost|^\s*ServerName\s", text, re.M | re.I):
        return "apache"
    if ext in _EXT:
        kind = _EXT[ext]
        if kind in ("text", "markdown") and _looks_like_log(text):
            return "log"
        return kind
    # no or unknown extension: look at the content
    first = text.lstrip()[:200]
    if first.startswith("#!"):
        line = first.splitlines()[0]
        if "python" in line:
            return "python"
        if "node" in line:
            return "javascript"
        if "pwsh" in line:
            return "powershell"
        return "shell"
    if re.match(r"(?i)(FROM|ARG)\s+\S+", first) and re.search(r"(?im)^\s*(RUN|CMD|ENTRYPOINT|COPY)\s", text):
        return "dockerfile"
    if re.search(r"^\[(Unit|Service|Install|Container)\]\s*$", text, re.M):
        return "systemd"
    if re.search(r"^apiVersion:\s*\S+\s*$", text, re.M) and re.search(r"^kind:\s*\S+", text, re.M):
        return "kubernetes"
    if first.startswith(("{", "[")) and first.rstrip().endswith(("}", "]")):
        return "json"
    if _looks_like_log(text):
        return "log"
    return "text"


def _in_helm(parts: list[str]) -> bool:
    return "templates" in parts and not ("roles" in parts)


def _yaml_kind(parts: list[str], text: str) -> str:
    if re.search(r"^apiVersion:\s*\S+", text, re.M) and re.search(r"^kind:\s*\S+", text, re.M):
        return "kubernetes"
    if any(p in parts for p in ("roles", "playbooks", "group_vars", "host_vars", "tasks", "handlers")):
        return "ansible"
    if re.search(r"^\s*-\s*(hosts|import_playbook):", text, re.M) or \
            re.search(r"^\s*-\s*name:.*\n\s+(ansible\.builtin\.|community\.|become:|apt:|yum:|"
                      r"dnf:|copy:|template:|service:|systemd:|shell:|command:)", text, re.M):
        return "ansible"
    if re.search(r"^(stages|before_script|include|variables):", text, re.M) and \
            re.search(r"^\s+script:", text, re.M):
        return "gitlab-ci"
    if re.search(r"^on:\s*$|^on:\s*\[", text, re.M) and re.search(r"^jobs:", text, re.M):
        return "github-actions"
    if re.search(r"^services:\s*$", text, re.M) and re.search(r"^\s+image:", text, re.M):
        return "compose"
    return "yaml"


def _looks_like_log(text: str) -> bool:
    lines = text.splitlines()[:400]
    if len(lines) < 3:
        return bool(re.search(r"Traceback \(most recent|Exception|ERROR|FATAL|panic:", text))
    hits = sum(1 for ln in lines if _LOG_LINE.match(ln))
    return hits / len(lines) > 0.3 or "Traceback (most recent call last)" in text


def is_binary(data: bytes) -> bool:
    if not data:
        return False
    if b"\0" in data[:8192]:
        return True
    try:
        data[:8192].decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        # a multi-byte char cut at the 8192 boundary is fine
        return exc.start < 8180
