"""Built-in checks (FP0xx). Fast, offline and deterministic; the model builds on them.

Each check is a regex per line, limited to some file kinds, plus a few structured checks
for Kubernetes manifests. Secrets are reported with the value masked.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

import yaml

from .models import Finding, Severity, SourceFile

CODE = None  # sentinel: every kind except logs and prose
_PROSE = {"log", "markdown", "text"}


@dataclass(frozen=True)
class Rule:
    id: str
    kinds: frozenset | None       # None = all code/config kinds
    pattern: re.Pattern
    severity: Severity
    category: str
    message: str
    suggestion: str
    unless: re.Pattern | None = None   # skip the line when this also matches


def _r(id, kinds, pattern, sev, cat, msg, sug, flags=re.I, unless=None):
    return Rule(id, frozenset(kinds) if kinds else None, re.compile(pattern, flags),
                sev, cat, msg, sug, re.compile(unless, re.I) if unless else None)


S = Severity
_SECRET_VALUE = r"""['"]?([^\s'"$`{}<>()]{8,})['"]?"""
_PLACEHOLDER = (r"(changeme|change_me|example|placeholder|dummy|xxx+|\*\*\*|<[^>]+>|\$\{|\{\{|"
                r"lookup\(|vault|secretKeyRef|valueFrom|getenv|environ|os\.env|process\.env|"
                r"your[-_]|redacted|\.\.\.)")

RULES: list[Rule] = [
    _r("FP001", None,
       r"""(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token|"""
       r"""client[_-]?secret|private[_-]?key)\w*["']?\s*[:=]\s*""" + _SECRET_VALUE,
       S.HIGH, "security", "Hard-coded secret ({name}).",
       "Move the value to a secret store (Vault, Kubernetes Secret, CI variable, ansible-vault) "
       "and rotate it, because it is in version control now.", unless=_PLACEHOLDER),
    _r("FP001", None, r"\b(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|glpat-[A-Za-z0-9_-]{20,}|"
       r"xox[abpr]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{32,})\b",
       S.CRITICAL, "security", "A real-looking access token is in this file.",
       "Revoke the token now, then load it from a secret store.", flags=0),
    _r("FP002", None, r"-----BEGIN ((RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----",
       S.CRITICAL, "security", "A private key is committed.",
       "Remove it from the repository and its history, and replace the key pair."),
    _r("FP003", ["shell", "dockerfile", "ansible", "gitlab-ci", "github-actions", "python",
                 "powershell", "systemd", "yaml", "helm", "kubernetes"],
       r"(curl|wget)\b[^|;&\n]*\|\s*(sudo\s+)?(ba|z|k)?sh\b|iex\s*\(\s*(New-Object|iwr|Invoke-WebRequest)",
       S.HIGH, "security", "Downloads a script and runs it straight from the network.",
       "Download to a file, verify a checksum or signature, then run it; or use a package."),
    _r("FP004", None, r"""(chmod\s+(-R\s+)?0?777\b|mode:\s*['"]?0?777\b|0o777\b|os\.chmod\([^)]*0o?777)""",
       S.HIGH, "security", "World-writable permissions (777).",
       "Use the smallest mode that works, e.g. 0755 for directories and 0644 for files."),
    _r("FP005", None,
       r"(verify\s*=\s*False|--insecure\b|curl\s+(-\w*k\w*\s)|validate_certs:\s*(no|false)|"
       r"InsecureSkipVerify:\s*true|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0|"
       r"sslVerify\s*=?\s*false|insecure-skip-tls-verify|ssl_verify\s*[:=]\s*(false|no|0)|"
       r"rejectUnauthorized:\s*false|CURLOPT_SSL_VERIFYPEER,\s*(false|0))",
       S.HIGH, "security", "TLS certificate verification is switched off.",
       "Trust the right CA (a CA bundle or the system trust store) instead of skipping "
       "verification."),
    _r("FP006", None, r"\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:([^/\s@'\"$]{3,})@[\w.-]+",
       S.HIGH, "security", "Credentials inside a URL.",
       "Pass the credentials separately (netrc, a credential helper, an env var from a "
       "secret).", unless=_PLACEHOLDER),
    _r("FP007", ["shell", "ansible", "dockerfile", "kubernetes", "helm", "systemd", "yaml"],
       r"(setenforce\s+0|SELINUX=disabled|SELINUX=permissive|systemctl\s+(stop|disable)\s+firewalld|"
       r"ufw\s+disable|iptables\s+-F\b)",
       S.MEDIUM, "security", "SELinux or the host firewall is switched off.",
       "Keep it on and add the specific SELinux boolean/context or firewall rule you need."),
    # Dockerfile
    _r("FP010", ["dockerfile"], r"^\s*FROM\s+(?!scratch\b)([^\s:@]+)(:latest)?\s*(AS\s+\S+)?\s*$",
       S.MEDIUM, "reliability", "Base image without a fixed tag (or :latest).",
       "Pin a version tag, ideally with a digest (image:1.2.3@sha256:...), and let Renovate "
       "bump it.", unless=r"^\s*FROM\s+\$|:\S+:|^\s*FROM\s+\S+:(?!latest)\S+"),
    _r("FP011", ["dockerfile"], r"^\s*USER\s+(root|0)(:\S+)?\s*$",
       S.MEDIUM, "security", "The container runs as root.",
       "Create an unprivileged user and switch to it (USER 1001); on OpenShift any UID with "
       "group 0 works."),
    _r("FP012", ["dockerfile"], r"^\s*ADD\s+https?://",
       S.LOW, "security", "ADD with a URL downloads without checksum verification.",
       "Use ADD --checksum=sha256:... or curl + sha256sum -c in a RUN step."),
    _r("FP013", ["dockerfile"], r"apt-get\s+install(?!.*--no-install-recommends)",
       S.LOW, "performance", "apt-get install without --no-install-recommends.",
       "Add --no-install-recommends and clean /var/lib/apt/lists in the same RUN."),
    # Kubernetes / compose (line based; the structured checks are below)
    _r("FP020", ["kubernetes", "helm", "compose"], r"^\s*privileged:\s*true\b",
       S.HIGH, "security", "Privileged container.",
       "Drop privileged and add only the capabilities it really needs."),
    _r("FP021", ["kubernetes", "helm"], r"^\s*(hostNetwork|hostPID|hostIPC):\s*true\b",
       S.HIGH, "security", "The pod shares the host's network/PID/IPC namespace.",
       "Remove it unless this is a node agent that truly needs it."),
    _r("FP022", ["kubernetes", "helm"], r"^\s*hostPath:\s*$",
       S.MEDIUM, "security", "hostPath volume: the pod can read or write the node's disk.",
       "Use a PVC, a ConfigMap/Secret, or an emptyDir instead."),
    _r("FP023", ["kubernetes", "helm", "compose"], r"^\s*image:\s*['\"]?[^\s'\"]+?(:latest)?['\"]?\s*$",
       S.MEDIUM, "reliability", "Image without a fixed tag (or :latest).",
       "Pin a version tag or digest so every node runs the same image.",
       unless=r"image:\s*['\"]?[^\s'\"]+:(?!latest)[^\s'\"]+|image:\s*['\"]?\{\{|@sha256:|image:\s*$"),
    _r("FP024", ["kubernetes", "helm"], r"^\s*allowPrivilegeEscalation:\s*true\b",
       S.MEDIUM, "security", "allowPrivilegeEscalation is true.",
       "Set allowPrivilegeEscalation: false."),
    _r("FP025", ["kubernetes", "helm"], r"^\s*runAsUser:\s*0\b",
       S.MEDIUM, "security", "runAsUser: 0 runs the container as root.",
       "Use runAsNonRoot: true and a non-zero UID (or leave the UID to OpenShift's SCC)."),
    # shell
    _r("FP030", ["shell"], r"^\s*eval\s+[\"']?\$",
       S.MEDIUM, "security", "eval on a variable: whatever is in it gets executed.",
       "Use an array or a case statement instead of eval."),
    _r("FP031", ["shell", "dockerfile"], r"\brm\s+-rf?\s+[\"']?\$\{?\w+\}?/?[\"']?\s*$",
       S.MEDIUM, "reliability", "rm -rf on a variable that may be empty (rm -rf /).",
       "Use \"${VAR:?}\" so the script stops when the variable is empty."),
    # sshd
    _r("FP040", ["sshd", "ansible", "shell"], r"^\s*(-\s*)?.*PermitRootLogin\s+yes\b",
       S.HIGH, "security", "SSH allows root to log in.",
       "Set PermitRootLogin no (or prohibit-password) and use sudo."),
    _r("FP041", ["sshd", "ansible", "shell"], r"PasswordAuthentication\s+yes\b",
       S.MEDIUM, "security", "SSH allows password login.",
       "Use keys only: PasswordAuthentication no."),
    # python
    _r("FP050", ["python"], r"subprocess\.\w+\([^)]*shell\s*=\s*True",
       S.MEDIUM, "security", "subprocess with shell=True: risk of shell injection.",
       "Pass a list of arguments and leave shell=False."),
    _r("FP051", ["python"], r"\byaml\.load\((?![^)]*Loader\s*=\s*yaml\.(Safe|CSafe)Loader)",
       S.HIGH, "security", "yaml.load without SafeLoader can run code from the YAML.",
       "Use yaml.safe_load()."),
    _r("FP052", ["python"], r"\b(pickle|cPickle|marshal)\.loads?\(",
       S.MEDIUM, "security", "Unpickling data can run code if the data is not trusted.",
       "Use JSON, or only unpickle data you created yourself."),
    _r("FP053", ["python", "javascript", "php"], r"(?<![\w.])(eval|exec)\s*\(",
       S.MEDIUM, "security", "eval/exec runs a string as code.",
       "Parse the data instead (json, ast.literal_eval) or call the function directly.",
       unless=r"literal_eval|re\.compile|\.exec\(|regex"),
    # terraform
    _r("FP060", ["terraform"], r"cidr_blocks\s*=\s*\[[^\]]*\"0\.0\.0\.0/0\"",
       S.MEDIUM, "security", "Open to the whole internet (0.0.0.0/0).",
       "Limit the CIDR to the networks that need access."),
    _r("FP061", ["terraform"], r"acl\s*=\s*\"public-read(-write)?\"|publicly_accessible\s*=\s*true",
       S.HIGH, "security", "Publicly readable storage or database.",
       "Make it private and give access through IAM or a CDN with origin access."),
    # nginx / apache TLS
    _r("FP070", ["nginx", "apache", "haproxy"], r"(ssl_protocols|SSLProtocol|ssl-min-ver)[^;\n]*(TLSv1(\.0|\.1)?\b|SSLv3)(?!\.[23])",
       S.MEDIUM, "security", "Old TLS versions (TLS 1.0/1.1 or SSLv3) are enabled.",
       "Allow TLS 1.2 and 1.3 only."),
    _r("FP071", ["nginx"], r"^\s*server_tokens\s+on\s*;",
       S.LOW, "security", "nginx advertises its version.", "Set server_tokens off;"),
]

# Known failure signatures in logs: these become findings with a ready answer.
LOG_SIGNATURES: list[tuple[str, re.Pattern, Severity, str, str]] = [
    ("FP090", re.compile(r"OOMKilled|Out of memory: Killed process|java\.lang\.OutOfMemoryError|"
                         r"Cannot allocate memory|MemoryError", re.I), S.HIGH,
     "The process ran out of memory.",
     "Raise the memory limit or lower the process's memory use (heap size, workers, batch size)."),
    ("FP091", re.compile(r"No space left on device|ENOSPC|disk quota exceeded", re.I), S.HIGH,
     "The disk (or inode table) is full.",
     "Free space (df -h, df -i), rotate logs, or grow the volume."),
    ("FP092", re.compile(r"x509:|certificate verify failed|CERTIFICATE_VERIFY_FAILED|"
                         r"unable to get local issuer certificate|self[- ]signed certificate|"
                         r"PKIX path building failed|certificate has expired", re.I), S.HIGH,
     "TLS certificate problem (unknown CA, expired, or wrong name).",
     "Check the chain with openssl s_client -connect host:443 -showcerts and add the CA "
     "to the trust store; do not switch verification off."),
    ("FP093", re.compile(r"Connection refused|ECONNREFUSED|connect: connection refused", re.I), S.MEDIUM,
     "Connection refused: nothing listens on that host and port.",
     "Check the service is running and the host/port/Service name is right."),
    ("FP094", re.compile(r"Name or service not known|no such host|NXDOMAIN|"
                         r"Temporary failure in name resolution|getaddrinfo ENOTFOUND", re.I), S.MEDIUM,
     "DNS lookup failed.", "Check the hostname, /etc/resolv.conf and the DNS server."),
    ("FP095", re.compile(r"Permission denied|EACCES|Operation not permitted|AccessDenied|"
                         r"forbidden: User", re.I), S.MEDIUM,
     "Permission denied.",
     "Check file ownership/mode, SELinux (ausearch -m avc -ts recent) and RBAC/IAM rights."),
    ("FP096", re.compile(r"CrashLoopBackOff|Back-off restarting failed container", re.I), S.HIGH,
     "The container keeps crashing on start.",
     "Read the previous container log: kubectl logs <pod> --previous."),
    ("FP097", re.compile(r"ImagePullBackOff|ErrImagePull|manifest unknown|pull access denied", re.I), S.HIGH,
     "The image cannot be pulled.",
     "Check the image name and tag, and the pull secret for the registry."),
    ("FP098", re.compile(r"timed out|Timeout exceeded|deadline exceeded|ETIMEDOUT", re.I), S.MEDIUM,
     "A timeout: a dependency is slow or unreachable (firewall, proxy, wrong route).",
     "Test the connection from the same host/pod (curl -v, nc -zv) and check proxy and "
     "firewall rules."),
    ("FP099", re.compile(r"Too many open files|EMFILE", re.I), S.MEDIUM,
     "The process hit its open-file limit.",
     "Raise LimitNOFILE (systemd) or ulimit -n, and look for leaked file handles."),
    ("FP089", re.compile(r"segmentation fault|SIGSEGV|core dumped|panic:", re.I), S.HIGH,
     "The program crashed (segfault or panic).",
     "Look at the stack trace just above this line; check versions of native libraries."),
]


def run_static(files: list[SourceFile], disabled: set[str] | None = None) -> list[Finding]:
    disabled = disabled or set()
    out: list[Finding] = []
    for f in files:
        if f.kind == "log":
            out += _log_signatures(f, disabled)
            continue
        if f.kind in _PROSE:
            continue
        for no, line in enumerate(f.text.splitlines(), 1):
            if len(line) > 2000:
                continue
            for rule in RULES:
                if rule.id in disabled or (rule.kinds is not None and f.kind not in rule.kinds):
                    continue
                m = rule.pattern.search(line)
                if not m or (rule.unless and rule.unless.search(line)):
                    continue
                msg = rule.message
                if "{name}" in msg:
                    msg = msg.format(name=m.group(1).lower())
                out.append(Finding(rule.id, rule.severity, f.path, no, msg, "static",
                                   rule.category, rule.suggestion))
                break   # one finding per line is enough
        if f.kind in ("kubernetes",) and "FP026" not in disabled:
            out += _k8s_structured(f)
    return _dedupe(out)


def _log_signatures(f: SourceFile, disabled: set[str]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    count: dict[str, int] = {}
    for no, line in enumerate(f.text.splitlines(), 1):
        for rid, pat, sev, msg, sug in LOG_SIGNATURES:
            if rid in disabled or not pat.search(line):
                continue
            count[rid] = count.get(rid, 0) + 1
            if rid not in seen:
                seen[rid] = Finding(rid, sev, f.path, no, msg, "static", "runtime", sug)
    for rid, finding in seen.items():
        if count[rid] > 1:
            finding.message += f" (seen {count[rid]} times; first at this line)"
    return list(seen.values())


def _k8s_structured(f: SourceFile) -> list[Finding]:
    """Containers without resource limits or without a securityContext."""
    out = []
    try:
        docs = list(yaml.safe_load_all(f.text))
    except yaml.YAMLError:
        return out
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        spec = doc.get("spec") or {}
        pod = spec.get("template", {}).get("spec") if isinstance(spec.get("template"), dict) else None
        if doc.get("kind") == "CronJob":
            pod = (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec")
        if doc.get("kind") == "Pod":
            pod = spec
        if not isinstance(pod, dict):
            continue
        name = (doc.get("metadata") or {}).get("name", "?")
        for c in (pod.get("containers") or []):
            if not isinstance(c, dict):
                continue
            line = _line_of(f.text, f"name: {c.get('name')}")
            limits = (c.get("resources") or {}).get("limits")
            if not limits or "memory" not in limits:
                out.append(Finding(
                    "FP026", Severity.MEDIUM, f.path, line,
                    f"{doc.get('kind')} {name}: container '{c.get('name')}' has no memory limit.",
                    "static", "reliability",
                    "Set resources.requests and resources.limits.memory so one pod cannot "
                    "starve the node."))
            if not c.get("readinessProbe") and doc.get("kind") in ("Deployment", "StatefulSet"):
                out.append(Finding(
                    "FP027", Severity.LOW, f.path, line,
                    f"{doc.get('kind')} {name}: container '{c.get('name')}' has no readinessProbe.",
                    "static", "reliability",
                    "Add a readinessProbe so traffic only goes to pods that are ready."))
    return out


def _line_of(text: str, needle: str) -> int | None:
    for no, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return no
    return None


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        key = (f.rule, f.file, f.line)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def rules_table() -> list[tuple[str, str, str, str]]:
    rows, seen = [], set()
    for r in RULES:
        if r.id in seen:
            continue
        seen.add(r.id)
        kinds = ", ".join(sorted(r.kinds)) if r.kinds else "all code and config"
        rows.append((r.id, r.severity.label, kinds, r.message.replace(" ({name})", "")))
    rows.append(("FP026", "medium", "kubernetes", "Container without a memory limit."))
    rows.append(("FP027", "low", "kubernetes", "Deployment container without a readinessProbe."))
    for rid, _, sev, msg, _ in LOG_SIGNATURES:
        rows.append((rid, sev.label, "log", msg))
    return sorted(rows)
