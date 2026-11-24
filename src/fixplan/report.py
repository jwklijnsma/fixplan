"""Write a plan as Markdown (for people), JSON (for tools) and SARIF (for code scanning)."""

from __future__ import annotations

import json
from pathlib import Path

from . import __version__
from .models import Plan, Severity

ICON = {Severity.CRITICAL: "🟥", Severity.HIGH: "🟧", Severity.MEDIUM: "🟨",
        Severity.LOW: "🟦", Severity.INFO: "⬜"}


def to_markdown(plan: Plan) -> str:
    by_id = {f.id: f for f in plan.findings}
    out = [f"# {plan.title}", ""]
    if plan.summary:
        out += [plan.summary, ""]
    counts = {}
    for f in plan.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    if counts:
        out.append(" · ".join(f"{ICON[s]} {counts[s]} {s.label}" for s in sorted(counts, reverse=True)))
        out.append("")
    files = ", ".join(sorted({f.kind for f in plan.files}))
    out.append(f"_{len(plan.files)} file(s) ({files}); "
               f"{'model ' + plan.model if plan.generator == 'llm' else 'rules only, no model'}; "
               f"{plan.seconds:.0f}s_")
    out.append("")
    if plan.steps:
        out += ["## Plan", ""]
        for i, s in enumerate(plan.steps, 1):
            meta = [s.priority.label]
            if s.effort:
                meta.append(s.effort)
            out.append(f"### {i}. {ICON[s.priority]} {s.title}")
            out.append(f"_{' · '.join(meta)}_" + (f" — {', '.join(f'`{p}`' for p in s.files)}" if s.files else ""))
            out.append("")
            if s.why:
                out += [f"**Why:** {s.why}", ""]
            if s.actions:
                out += ["**Do:**", ""] + [f"{n}. {a}" for n, a in enumerate(s.actions, 1)] + [""]
            if s.commands:
                out += ["```bash", *s.commands, "```", ""]
            if s.patch:
                lang = "diff" if s.patch.lstrip().startswith(("---", "@@", "diff ")) else ""
                out += [f"```{lang}", s.patch, "```", ""]
            if s.verify:
                out += ["**Check:**", ""] + [f"- {v}" for v in s.verify] + [""]
            if s.risk:
                out += [f"> ⚠️ **Risk:** {s.risk}", ""]
            if s.findings:
                refs = [by_id[i] for i in s.findings if i in by_id]
                out.append("<details><summary>Resolves "
                           f"{len(refs)} finding(s)</summary>\n")
                for f in refs:
                    out.append(f"- `{f.id}` {f.file}{':' + str(f.line) if f.line else ''} "
                               f"— {f.message} _({f.source}/{f.rule})_")
                out += ["", "</details>", ""]
    else:
        out += ["Nothing to fix. ✅", ""]
    if plan.findings:
        out += ["## All findings", "", "| | Id | Where | Problem | Source |", "|---|---|---|---|---|"]
        for f in plan.findings:
            where = f"{f.file}:{f.line}" if f.line else f.file
            msg = f.message.replace("|", "\\|").replace("\n", " ")
            out.append(f"| {ICON[f.severity]} | `{f.id}` | `{where}` | {msg} | {f.source}/{f.rule} |")
        out.append("")
    if plan.notes:
        out += ["## Notes", ""] + [f"- {n}" for n in plan.notes] + [""]
    out.append(f"<sub>fixplan {__version__}</sub>")
    return "\n".join(out) + "\n"


def to_json(plan: Plan) -> str:
    return json.dumps({"fixplan": __version__, **plan.to_dict()}, indent=2, ensure_ascii=False)


def to_sarif(plan: Plan) -> str:
    level = {Severity.CRITICAL: "error", Severity.HIGH: "error", Severity.MEDIUM: "warning",
             Severity.LOW: "note", Severity.INFO: "note"}
    rules = {}
    results = []
    for f in plan.findings:
        rules.setdefault(f.rule, {"id": f.rule, "shortDescription": {"text": f.message[:120]}})
        loc = {"physicalLocation": {"artifactLocation": {"uri": f.file}}}
        if f.line:
            loc["physicalLocation"]["region"] = {"startLine": f.line}
        results.append({"ruleId": f.rule, "level": level[f.severity],
                        "message": {"text": f.message + (f"\n{f.suggestion}" if f.suggestion else "")},
                        "locations": [loc],
                        "partialFingerprints": {"fixplan/v1": f.id}})
    return json.dumps({
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "fixplan", "version": __version__,
                                      "informationUri": "https://github.com/jwklijnsma/fixplan",
                                      "rules": list(rules.values())}},
                  "results": results}]}, indent=2)


def write_reports(plan: Plan, out_dir: str | Path, formats: list[str]) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        fmt = fmt.strip().lower()
        if fmt in ("md", "markdown"):
            p = out / "plan.md"
            p.write_text(to_markdown(plan), encoding="utf-8")
        elif fmt == "json":
            p = out / "plan.json"
            p.write_text(to_json(plan), encoding="utf-8")
        elif fmt == "sarif":
            p = out / "plan.sarif"
            p.write_text(to_sarif(plan), encoding="utf-8")
        else:
            continue
        written.append(p)
    return written
