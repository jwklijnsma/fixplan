"""Run the linters that happen to be installed, on a private copy of the upload.

Every linter is optional: when it is not on PATH it is skipped with a note. Linters only
read the files; nothing from the upload is executed.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .models import Finding, Severity, SourceFile

TIMEOUT = 120


def available() -> dict[str, str | None]:
    return {name: shutil.which(name) for name in ("shellcheck", "hadolint", "yamllint", "ruff")}


def run_linters(files: list[SourceFile]) -> tuple[list[Finding], list[str]]:
    tools = available()
    findings: list[Finding] = []
    notes: list[str] = []
    by_kind: dict[str, list[SourceFile]] = {}
    for f in files:
        by_kind.setdefault(f.kind, []).append(f)
    wanted = {
        "shellcheck": by_kind.get("shell", []),
        "hadolint": by_kind.get("dockerfile", []),
        "ruff": by_kind.get("python", []),
        "yamllint": [f for k in ("yaml", "kubernetes", "compose", "gitlab-ci", "github-actions",
                                 "kustomize") for f in by_kind.get(k, [])],
    }
    if not any(wanted.values()):
        return findings, notes
    with tempfile.TemporaryDirectory(prefix="fixplan-lint-") as tmp:
        root = Path(tmp)
        for f in files:
            dest = root / f.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f.text, encoding="utf-8")
        for tool, todo in wanted.items():
            if not todo:
                continue
            if not tools.get(tool):
                notes.append(f"{tool} is not installed: {len(todo)} file(s) reviewed without it")
                continue
            try:
                findings += _RUNNERS[tool](tools[tool], root, [f.path for f in todo])
            except (subprocess.SubprocessError, OSError, ValueError) as exc:
                notes.append(f"{tool} failed: {exc}")
    return findings, notes


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT,
                          stdin=subprocess.DEVNULL)
    return proc.stdout


def _shellcheck(exe: str, root: Path, paths: list[str]) -> list[Finding]:
    out = _run([exe, "-f", "json", "-x", "--", *paths], root)
    sev = {"error": Severity.HIGH, "warning": Severity.MEDIUM, "info": Severity.LOW,
           "style": Severity.INFO}
    return [Finding(f"SC{d['code']}", sev.get(d.get("level"), Severity.LOW), d["file"],
                    d.get("line"), d.get("message", ""), "shellcheck", "correctness",
                    f"https://www.shellcheck.net/wiki/SC{d['code']}")
            for d in json.loads(out or "[]")]


def _hadolint(exe: str, root: Path, paths: list[str]) -> list[Finding]:
    res = []
    sev = {"error": Severity.HIGH, "warning": Severity.MEDIUM, "info": Severity.LOW,
           "style": Severity.INFO}
    for p in paths:
        out = _run([exe, "-f", "json", "--no-fail", p], root)
        for d in json.loads(out or "[]"):
            res.append(Finding(d.get("code", "DL"), sev.get(d.get("level"), Severity.LOW), p,
                               d.get("line"), d.get("message", ""), "hadolint", "best-practice"))
    return res


def _ruff(exe: str, root: Path, paths: list[str]) -> list[Finding]:
    out = _run([exe, "check", "--output-format", "json", "--no-cache", "--exit-zero",
                "--select", "E9,F,B,S", *paths], root)
    res = []
    for d in json.loads(out or "[]"):
        code = d.get("code") or "ruff"
        sev = Severity.HIGH if code.startswith(("E9", "F82")) else \
            Severity.MEDIUM if code.startswith(("S", "B")) else Severity.LOW
        res.append(Finding(code, sev, Path(d["filename"]).resolve().relative_to(root.resolve()).as_posix(),
                           (d.get("location") or {}).get("row"), d.get("message", ""), "ruff",
                           "security" if code.startswith("S") else "correctness"))
    return res


def _yamllint(exe: str, root: Path, paths: list[str]) -> list[Finding]:
    cfg = "{extends: relaxed, rules: {line-length: disable, document-start: disable}}"
    out = _run([exe, "-f", "parsable", "-d", cfg, *paths], root)
    res = []
    for line in out.splitlines():
        # path:line:col: [level] message (rule)
        try:
            path, ln, _col, rest = line.split(":", 3)
            level = "error" if "[error]" in rest else "warning"
            msg = rest.split("]", 1)[1].strip()
        except ValueError:
            continue
        res.append(Finding("yamllint", Severity.MEDIUM if level == "error" else Severity.LOW,
                           path, int(ln), msg, "yamllint", "syntax"))
    return res


_RUNNERS = {"shellcheck": _shellcheck, "hadolint": _hadolint, "ruff": _ruff, "yamllint": _yamllint}
