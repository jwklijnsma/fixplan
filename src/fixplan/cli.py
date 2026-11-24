"""Command line: fixplan [plan] <path|archive|-> | serve | doctor | rules | types | init."""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .config import SAMPLE, USER_CONFIG, Config, load_config
from .detect import KINDS
from .intake import Intake, IntakeError
from .linters import available
from .models import Severity
from .ollama import OllamaClient, OllamaError
from .planner import make_plan
from .report import to_markdown, write_reports
from .static import rules_table

COMMANDS = ("plan", "serve", "doctor", "rules", "types", "init")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", help="settings file (default ~/.fixplan.yml)")
    p.add_argument("--host", dest="ollama_host", help="Ollama URL (default $OLLAMA_HOST or localhost)")
    p.add_argument("--model", help="Ollama model (default qwen2.5-coder:14b)")
    p.add_argument("--lang", dest="language", choices=["en", "nl"], help="language of the plan")
    p.add_argument("--workers", type=int, help="parallel model requests")
    p.add_argument("--num-ctx", dest="num_ctx", type=int, help="model context size")
    p.add_argument("--timeout", type=int, help="seconds without model output before giving up")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fixplan",
        description="Upload anything (code, config, manifests, logs) and get a step-by-step "
                    "plan to fix it, made by a local Ollama model.")
    ap.add_argument("--version", action="version", version=f"fixplan {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("plan", help="make a fix plan (default command)")
    p.add_argument("targets", nargs="*", default=["."],
                   help="files, folders, .zip/.tar.gz archives, or - for stdin (default: .)")
    p.add_argument("-g", "--goal", help='what you want, e.g. "make it run on OpenShift"')
    p.add_argument("--name", help="file name for stdin input, helps detection (e.g. app.log)")
    p.add_argument("-o", "--out", default=".fixplan", help="report folder (default .fixplan)")
    p.add_argument("-f", "--format", default="md,json",
                   help="report formats: md, json, sarif (default md,json)")
    p.add_argument("--print", dest="print_md", action="store_true",
                   help="also print the Markdown plan (default when output is not a terminal)")
    p.add_argument("--quiet", action="store_true", help="no progress output")
    p.add_argument("--no-llm", action="store_true", help="rules and linters only, no model")
    p.add_argument("--no-lint", action="store_true", help="do not run installed linters")
    p.add_argument("--fail-on", dest="fail_on", choices=["critical", "high", "medium", "low", "info", "never"],
                   help="exit 1 when a finding is at or above this (default high)")
    p.add_argument("--max-steps", dest="max_steps", type=int, help="maximum plan steps")
    _common(p)

    s = sub.add_parser("serve", help="web UI: upload or paste, get a plan")
    s.add_argument("--bind", default="127.0.0.1", help="address (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=8766, help="port (default 8766)")
    s.add_argument("--token", default="",
                   help="access token (default $FIXPLAN_WEB_TOKEN, else a new random one per start)")
    s.add_argument("--no-token", action="store_true",
                   help="no access token (only on localhost or behind a proxy that authenticates)")
    s.add_argument("--data", help="where finished plans are kept (default ~/.cache/fixplan/jobs)")
    s.add_argument("--open", action="store_true", help="open the browser")
    _common(s)

    d = sub.add_parser("doctor", help="check Ollama, the model, its speed and the linters")
    _common(d)
    sub.add_parser("rules", help="list the built-in FP rules")
    sub.add_parser("types", help="list the file types fixplan recognises")
    i = sub.add_parser("init", help=f"write a sample {USER_CONFIG}")
    i.add_argument("--force", action="store_true")
    return ap


def client_for(cfg: Config) -> OllamaClient:
    return OllamaClient(cfg.ollama_host, cfg.model, api_key=cfg.api_key, timeout=cfg.timeout,
                        retries=cfg.retries, temperature=cfg.temperature, num_ctx=cfg.num_ctx,
                        num_predict=cfg.num_predict, deadline=cfg.deadline,
                        keep_alive=cfg.keep_alive)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv = ["plan", *argv]
    args = build_parser().parse_args(argv)
    if args.cmd == "rules":
        for rid, sev, kinds, msg in rules_table():
            print(f"{rid}  {sev:<8}  {msg}  [{kinds}]")
        return 0
    if args.cmd == "types":
        for k, label in KINDS.items():
            print(f"{k:<16} {label}")
        return 0
    if args.cmd == "init":
        if USER_CONFIG.exists() and not args.force:
            print(f"{USER_CONFIG} exists (use --force to overwrite)")
            return 1
        USER_CONFIG.write_text(SAMPLE)
        print(f"wrote {USER_CONFIG}")
        return 0

    overrides = {k: getattr(args, k, None) for k in
                 ("ollama_host", "model", "language", "workers", "num_ctx", "timeout",
                  "fail_on", "max_steps", "goal")}
    if getattr(args, "no_llm", False):
        overrides["use_llm"] = False
    if getattr(args, "no_lint", False):
        overrides["use_lint"] = False
    cfg, warnings = load_config(args.config, overrides)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    if args.cmd == "doctor":
        return doctor(cfg)
    if args.cmd == "serve":
        from .web import serve
        return serve(cfg, bind=args.bind, port=args.port, token=args.token, data_dir=args.data,
                     open_browser=args.open, no_auth=args.no_token)
    return run_plan(args, cfg)


def run_plan(args, cfg: Config) -> int:
    err = sys.stderr
    tty = err.isatty() and not args.quiet
    intake = Intake(cfg)
    try:
        for t in args.targets:
            if t == "-":
                intake.add_text(sys.stdin.read(), args.name or "")
            else:
                intake.add_path(t)
        files, notes = intake.result()
    except IntakeError as exc:
        print(f"fixplan: {exc}", file=err)
        return 2

    client = None
    if cfg.use_llm:
        client = client_for(cfg)
        try:
            client.version()
        except Exception as exc:   # noqa: BLE001
            print(f"fixplan: Ollama not reachable at {cfg.ollama_host} ({exc.__class__.__name__}); "
                  "making the plan from rules and linters only. Use --host or --no-llm.", file=err)
            cfg.use_llm, client = False, None

    def progress(msg: str, frac: float):
        if tty:
            err.write(f"\r\033[K[{int(frac * 100):>3}%] {msg[:100]}")
            err.flush()

    if not args.quiet:
        kinds = sorted({f.kind for f in files})
        print(f"fixplan: {len(files)} file(s): {', '.join(kinds)}", file=err)
    plan = make_plan(files, cfg, notes=notes, client=client, progress=progress,
                     title_hint=_title(args.targets, args.name))
    if tty:
        err.write("\r\033[K")
    written = write_reports(plan, args.out, args.format.split(","))
    if args.print_md or not sys.stdout.isatty():
        sys.stdout.write(to_markdown(plan))
    if not args.quiet:
        print(f"fixplan: {len(plan.steps)} step(s), {len(plan.findings)} finding(s) "
              f"in {plan.seconds:.0f}s -> " + ", ".join(str(p) for p in written), file=err)
        for i, s in enumerate(plan.steps, 1):
            print(f"  {i:>2}. [{s.priority.label}] {s.title}", file=err)
    if cfg.fail_on == "never":
        return 0
    worst = plan.worst
    return 1 if worst is not None and worst >= cfg.fail_on_severity else 0


def _title(targets: list[str], name: str | None) -> str:
    if targets == ["-"]:
        return f"Fix plan: {name}" if name else "Fix plan"
    t = targets[0].rstrip("/") or "."
    if t == ".":
        import os
        t = os.path.basename(os.getcwd())
    return f"Fix plan: {t.split('/')[-1]}" + (f" +{len(targets) - 1}" if len(targets) > 1 else "")


def doctor(cfg: Config) -> int:
    ok = True
    print(f"fixplan {__version__}")
    print(f"Ollama:  {cfg.ollama_host}")
    c = client_for(cfg)
    try:
        print(f"  version {c.version()}")
    except Exception as exc:   # noqa: BLE001
        print(f"  ✗ not reachable: {exc}")
        print("  plans still work with --no-llm (rules and linters only)")
        ok = False
    if ok:
        try:
            if c.has_model():
                print(f"  ✓ model {cfg.model} is pulled")
                t0 = time.monotonic()
                c.num_predict = 64
                c.chat_json("Answer with JSON only.", 'Return {"ok": true}.',
                            {"type": "object", "properties": {"ok": {"type": "boolean"}}})
                st = c.last_stats
                tps = (st.get("eval_count") or 0) / max((st.get("eval_duration") or 1) / 1e9, 1e-9)
                print(f"  ✓ answered in {time.monotonic() - t0:.1f}s ({tps:.0f} tokens/s)")
            else:
                print(f"  ✗ model {cfg.model} not pulled: ollama pull {cfg.model}")
                ok = False
        except OllamaError as exc:
            print(f"  ✗ test request failed: {exc}")
            ok = False
    print("Linters (optional, used when installed):")
    for name, path in available().items():
        print(f"  {'✓' if path else '-'} {name}{'' if path else ' (not installed)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
