import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, enc } from "../api";
import { CodeBlock, CopyButton, Markdown } from "../markdown";
import { go, useApp } from "../store";
import { SEVS, isActive, type Finding, type JobDetail, type Plan, type Severity, type Step } from "../types";
import { ago, dur, logClass, looksLikeDiff, plural, progressPct, sourceLabel, statusText, stored, store } from "../util";

const TABS: [string, string][] = [["plan", "Plan"], ["findings", "Findings"], ["files", "Files"], ["report", "Report"], ["log", "Log"]];

export function PlanPage({ jobId, tab }: { jobId: string; tab: string }) {
  const { jobs, jobVersion, cancelJob, deleteJob, handleError } = useApp();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const lines = useRef<string[]>([]);
  const summary = jobs.find((j) => j.id === jobId);

  const load = useCallback(async (incremental = false) => {
    try {
      const since = incremental ? lines.current.length : 0;
      const d = await api<JobDetail>(`jobs/${jobId}?since=${since}`);
      lines.current = incremental ? [...lines.current, ...d.lines] : d.lines;
      setJob({ ...d, lines: lines.current });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setMissing(true);
      else handleError(err);
    }
  }, [jobId, handleError]);

  useEffect(() => {
    setJob(null);
    setMissing(false);
    lines.current = [];
    load();
  }, [load, jobVersion[jobId]]);   // eslint-disable-line react-hooks/exhaustive-deps

  const active = job ? isActive(job) : false;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => load(true), 1200);
    return () => window.clearInterval(t);
  }, [active, load]);

  if (missing) return <div className="empty"><h3>This plan is gone</h3><p>It was deleted, or it is older than the newest 100. <a href="#/new">Make a new one</a>.</p></div>;
  if (!job) return <div className="empty"><p>Loading…</p></div>;
  const view = summary && isActive(summary) ? { ...job, ...summary, lines: job.lines, plan: job.plan } : job;
  const plan = job.plan;
  const counts: Partial<Record<Severity, number>> = {};
  for (const f of plan?.findings || []) counts[f.severity] = (counts[f.severity] || 0) + 1;
  const current = TABS.some(([k]) => k === tab) ? tab : "plan";
  const tabCount: Record<string, number | undefined> = { plan: plan?.steps.length, findings: plan?.findings.length, files: plan?.files.length };

  return (
    <div>
      <div className="repo-head">
        <div className="repo-title">
          <h1>{plan?.title || job.name}</h1>
          <div className="repo-sub">
            <span className="mono">{job.name}</span>
            {plan && <span>{plural(plan.files.length, "file")}</span>}
            <span>{plan ? (plan.generator === "llm" ? "model " + plan.model : "rules only, no model") : job.rules_only ? "rules only" : job.model}</span>
            {job.ended && <span>{dur(job.seconds)} · {ago(job.ended)}</span>}
          </div>
          {job.goal && <div className="goal-line"><span className="muted small">Goal</span> {job.goal}</div>}
        </div>
        <div className="actions">
          {plan && (
            <>
              <CopyMarkdown jobId={jobId} />
              <a className="btn" href={`/api/jobs/${jobId}/plan.md`} download>↓ plan.md</a>
              <a className="btn" href={`/api/jobs/${jobId}/plan.json`} download>↓ JSON</a>
              <a className="btn" href={`/api/jobs/${jobId}/plan.sarif`} download title="SARIF, for code scanning">↓ SARIF</a>
            </>
          )}
          {isActive(view) ? (
            <button className="btn danger" onClick={() => cancelJob(jobId).then(() => load())}>{view.status === "queued" ? "Remove" : "Cancel"}</button>
          ) : (
            <button className="btn danger" onClick={async () => { if (confirm("Delete this plan?") && (await deleteJob(jobId))) go(null); }}>Delete</button>
          )}
        </div>
      </div>

      {(isActive(view) || view.status === "failed" || view.status === "cancelled") && <JobCard job={view} />}

      {plan && (
        <>
          {plan.summary && <p className="plan-summary">{plan.summary}</p>}
          <nav className="tabs" role="tablist">
            {TABS.map(([k, l]) => (
              <button key={k} role="tab" aria-selected={current === k} onClick={() => go(jobId, k)}>
                {l}{tabCount[k] != null && <span className="count">{tabCount[k]}</span>}
              </button>
            ))}
          </nav>
          {current === "plan" && <StepsTab jobId={jobId} plan={plan} counts={counts} />}
          {current === "findings" && <FindingsTab jobId={jobId} plan={plan} />}
          {current === "files" && <FilesTab jobId={jobId} plan={plan} />}
          {current === "report" && <ReportTab jobId={jobId} />}
          {current === "log" && <div className="card"><div className="card-body"><Log lines={job.lines} /></div></div>}
        </>
      )}
    </div>
  );
}

function CopyMarkdown({ jobId }: { jobId: string }) {
  const [md, setMd] = useState("");
  useEffect(() => {
    fetch(`/api/jobs/${jobId}/plan.md`, { credentials: "same-origin" }).then((r) => (r.ok ? r.text() : "")).then(setMd, () => setMd(""));
  }, [jobId]);
  if (!md) return null;
  return <span className="copy-md"><CopyButton text={md} label="Copy Markdown" /></span>;
}

function JobCard({ job }: { job: JobDetail }) {
  const pct = progressPct(job);
  return (
    <div className={"card job-card " + job.status}>
      <div className="card-head">
        <span className={"dot " + (job.status === "running" ? "run" : job.status === "queued" ? "wait" : "bad")} />
        <h3>{job.status === "running" ? "Making the plan" : job.status === "queued" ? "Waiting" : job.status === "failed" ? "Failed" : "Cancelled"}</h3>
        <span className="muted small">{statusText(job)}</span>
      </div>
      <div className="card-body">
        {job.status === "running" && (
          <>
            <div className="progress"><div style={{ width: `${pct}%` }} /></div>
            <div className="progress-meta"><span>{pct}%</span><span>{dur(job.seconds)} · it keeps running if you leave this page</span></div>
          </>
        )}
        {job.status === "queued" && <div className="progress waiting"><div /></div>}
        <Log lines={job.lines} />
      </div>
    </div>
  );
}

function Log({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines.length]);
  if (!lines.length) return <p className="muted small">No log yet.</p>;
  return <pre className="log" ref={box}>{lines.map((l, i) => <div key={i} className={logClass(l)}>{l}</div>)}</pre>;
}

// ------------------------------------------------------------------ plan steps
function StepsTab({ jobId, plan, counts }: { jobId: string; plan: Plan; counts: Partial<Record<Severity, number>> }) {
  const key = "done:" + jobId;
  const [done, setDone] = useState<number[]>(() => stored<number[]>(key, []));
  const toggle = (i: number) => {
    const next = done.includes(i) ? done.filter((x) => x !== i) : [...done, i];
    setDone(next);
    store(key, next);
  };
  const byId = useMemo(() => new Map(plan.findings.map((f) => [f.id, f])), [plan]);
  if (!plan.steps.length) return <div className="empty"><h3>Nothing to fix</h3><p>No problems were found in this upload.</p></div>;
  return (
    <div>
      <div className="summary-row">
        {SEVS.filter((s) => counts[s]).map((s) => (
          <a key={s} className="sev-chip" href={`#/p/${jobId}/findings`} aria-pressed="true">
            <span className="n">{counts[s]}</span>
            <span className="l"><span className="sw" style={{ background: `var(--sev-${s})` }} />{s}</span>
          </a>
        ))}
        <div className="meta-chip">
          <div className="done-count">{done.length}/{plan.steps.length} steps done</div>
          <div className="progress thin"><div style={{ width: `${(100 * done.length) / plan.steps.length}%` }} /></div>
        </div>
      </div>
      <ol className="steps">
        {plan.steps.map((s, i) => <StepCard key={i} n={i + 1} step={s} done={done.includes(i)} onDone={() => toggle(i)} byId={byId} jobId={jobId} />)}
      </ol>
      {plan.notes.length > 0 && (
        <div className="card">
          <div className="card-head"><h3>Notes</h3><span className="muted small">skipped files, missing linters, tool problems</span></div>
          <div className="card-body"><ul className="notes-list">{plan.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
        </div>
      )}
    </div>
  );
}

function StepCard({ n, step: s, done, onDone, byId, jobId }: { n: number; step: Step; done: boolean; onDone: () => void; byId: Map<string, Finding>; jobId: string }) {
  const [open, setOpen] = useState(!done);
  useEffect(() => setOpen(!done), [done]);
  const refs = s.findings.map((id) => byId.get(id)).filter(Boolean) as Finding[];
  return (
    <li className={"step card" + (done ? " done" : "")}>
      <div className="step-head">
        <span className={"step-num sev-" + s.priority}>{done ? "✓" : n}</span>
        <button className="step-title linklike" onClick={() => setOpen(!open)} aria-expanded={open}>
          <h3>{s.title}</h3>
          <div className="step-meta">
            <span className={"sev sev-" + s.priority}>{s.priority}</span>
            {s.effort && <span className="tag">{s.effort}</span>}
            {s.files.slice(0, 4).map((f) => <span key={f} className="mono muted">{f}</span>)}
            {s.files.length > 4 && <span className="muted">+{s.files.length - 4}</span>}
          </div>
        </button>
        <label className="check done-check" title="Mark this step done (kept in this browser)">
          <input type="checkbox" checked={done} onChange={onDone} /> done
        </label>
      </div>
      {open && (
        <div className="step-body">
          {s.why && <p className="why">{s.why}</p>}
          {s.actions.length > 0 && (<><div className="k">Do</div><ol className="actions-list">{s.actions.map((a, i) => <li key={i}>{a}</li>)}</ol></>)}
          {s.commands.length > 0 && (<><div className="k">Run</div><CodeBlock text={s.commands.join("\n")} /></>)}
          {s.patch && (<><div className="k">Change</div><CodeBlock text={s.patch} diff={looksLikeDiff(s.patch)} /></>)}
          {s.verify.length > 0 && (<><div className="k">Check</div><ul className="verify">{s.verify.map((v, i) => <li key={i}>{v}</li>)}</ul></>)}
          {s.risk && <div className="callout warn"><b>Risk:</b> {s.risk}</div>}
          {refs.length > 0 && (
            <details className="resolves">
              <summary>Resolves {plural(refs.length, "finding")}</summary>
              <ul>
                {refs.map((f) => (
                  <li key={f.id}>
                    <span className={"sev sev-" + f.severity}>{f.severity}</span>{" "}
                    <a className="mono" href={`#/p/${jobId}/findings`} onClick={() => store("focus:" + jobId, f.id)}>{f.file}{f.line ? ":" + f.line : ""}</a>{" "}
                    {f.message} <span className="f-src">{sourceLabel(f.source)}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </li>
  );
}

// ------------------------------------------------------------------ findings
const fileCache = new Map<string, Promise<string>>();
function fileContent(jobId: string, path: string): Promise<string> {
  const key = jobId + "|" + path;
  if (!fileCache.has(key)) {
    const p = api<{ content: string }>(`jobs/${jobId}/file?path=${enc(path)}`).then((d) => d.content);
    p.catch(() => fileCache.delete(key));
    fileCache.set(key, p);
  }
  return fileCache.get(key)!;
}

function Snippet({ jobId, file, line, all }: { jobId: string; file: string; line?: number | null; all?: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fileContent(jobId, file).then(setText, (e) => setError(e.message));
  }, [jobId, file]);
  if (error) return <div className="code"><div className="code-msg">Could not load the file: {error}</div></div>;
  if (text === null) return <div className="code"><div className="code-msg">Loading…</div></div>;
  const lines = text.split("\n");
  const start = all || !line ? 1 : Math.max(1, line - 5);
  const end = all || !line ? Math.min(lines.length, 2000) : Math.min(lines.length, line + 5);
  const rows = [];
  for (let n = start; n <= end; n++) {
    rows.push(<tr key={n} className={n === line ? "hl" : undefined}><td className="ln">{n}</td><td>{lines[n - 1] ?? ""}</td></tr>);
  }
  return (
    <div className="code">
      <table><tbody>{rows}</tbody></table>
      {all && lines.length > 2000 && <div className="code-msg">… {lines.length - 2000} more lines</div>}
    </div>
  );
}

function FindingRow({ jobId, f, showFile, focus }: { jobId: string; f: Finding; showFile: boolean; focus: boolean }) {
  const [open, setOpen] = useState(focus);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focus) ref.current?.scrollIntoView({ block: "center" });
  }, [focus]);
  return (
    <div className={"finding" + (focus ? " focus" : "")} ref={ref}>
      <button aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className={"sev sev-" + f.severity}>{f.severity}</span>
        <span className="lineno">{f.line ? "L" + f.line : "—"}</span>
        <span className="f-main">
          <span className="f-rule">{f.rule}</span>
          {showFile && <span className="f-rule">{f.file + (f.line ? ":" + f.line : "")}</span>}
          <span className="f-src">{sourceLabel(f.source)}</span>
          <span className="f-msg">{f.message}</span>
        </span>
      </button>
      {open && (
        <div className="f-detail">
          {f.suggestion && <div className="sugg"><b>Suggested fix</b>{f.suggestion}</div>}
          <div className="facts">
            <span>category: {f.category || "general"}</span>
            {f.confidence != null && <span>confidence: {Math.round(f.confidence * 100)}%</span>}
            <span className="mono">{f.id}</span>
          </div>
          {f.line ? <Snippet jobId={jobId} file={f.file} line={f.line} /> : null}
        </div>
      )}
    </div>
  );
}

function FindingsTab({ jobId, plan }: { jobId: string; plan: Plan }) {
  const [sev, setSev] = useState<Set<Severity>>(() => new Set(SEVS));
  const [source, setSource] = useState("all");
  const [q, setQ] = useState("");
  const [group, setGroup] = useState<"file" | "rule">("file");
  const [focus] = useState(() => {
    const id = stored<string>("focus:" + jobId, "");
    store("focus:" + jobId, "");
    return id;
  });
  const sources = useMemo(() => {
    const s = new Set(plan.findings.map((f) => f.source));
    const list: [string, string][] = [["all", "All"]];
    if (s.has("static")) list.push(["static", "FP checks"]);
    if ([...s].some((x) => x !== "static" && x !== "llm")) list.push(["lint", "Linters"]);
    if (s.has("llm")) list.push(["llm", "Model"]);
    return list;
  }, [plan]);
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const items = plan.findings.filter(
      (x) => sev.has(x.severity)
        && (source === "all" || x.source === source || (source === "lint" && x.source !== "static" && x.source !== "llm"))
        && (!needle || `${x.rule} ${x.file} ${x.message} ${x.category}`.toLowerCase().includes(needle)),
    );
    const m = new Map<string, Finding[]>();
    for (const x of items) {
      const k = group === "rule" ? x.rule : x.file;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(x);
    }
    const rank = (arr: Finding[]) => Math.min(...arr.map((x) => SEVS.indexOf(x.severity)));
    return [...m.entries()].sort((a, b) => rank(a[1]) - rank(b[1]) || a[0].localeCompare(b[0]));
  }, [plan, sev, source, q, group]);
  const counts: Partial<Record<Severity, number>> = {};
  for (const f of plan.findings) counts[f.severity] = (counts[f.severity] || 0) + 1;
  const toggle = (s: Severity) => {
    const n = new Set(sev);
    n.has(s) ? n.delete(s) : n.add(s);
    setSev(n);
  };
  if (!plan.findings.length) return <div className="empty"><h3>No findings</h3><p>Nothing to fix here.</p></div>;
  return (
    <div>
      <div className="summary-row">
        {SEVS.map((s) => (
          <button key={s} className="sev-chip" aria-pressed={sev.has(s)} title="Click to toggle, double-click to show only this severity" onClick={() => toggle(s)} onDoubleClick={() => setSev(new Set([s]))}>
            <span className="n">{counts[s] || 0}</span>
            <span className="l"><span className="sw" style={{ background: `var(--sev-${s})` }} />{s}</span>
          </button>
        ))}
      </div>
      <div className="filters">
        <input type="search" placeholder="Search rule, file or message…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg" role="group" aria-label="Source">
          {sources.map(([k, l]) => <button key={k} aria-pressed={source === k} onClick={() => setSource(k)}>{l}</button>)}
        </div>
        <div className="seg" role="group" aria-label="Group by">
          {(["file", "rule"] as const).map((k) => <button key={k} aria-pressed={group === k} onClick={() => setGroup(k)}>{k === "file" ? "By file" : "By rule"}</button>)}
        </div>
      </div>
      {!groups.length ? (
        <div className="empty"><p>No findings match these filters.</p></div>
      ) : (
        groups.map(([k, arr]) => (
          <div className="file-group" key={k}>
            <div className="file-head">{k}<span className="n">{plural(arr.length, "finding")}</span></div>
            {arr.map((x) => <FindingRow key={x.id} jobId={jobId} f={x} showFile={group === "rule"} focus={x.id === focus} />)}
          </div>
        ))
      )}
    </div>
  );
}

// ------------------------------------------------------------------ files and report
function FilesTab({ jobId, plan }: { jobId: string; plan: Plan }) {
  const [open, setOpen] = useState<string | null>(null);
  const per = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of plan.findings) m.set(f.file, (m.get(f.file) || 0) + 1);
    return m;
  }, [plan]);
  return (
    <div className="card">
      <table className="files-table">
        <thead><tr><th>File</th><th>Detected as</th><th className="num">Lines</th><th className="num">Findings</th></tr></thead>
        <tbody>
          {plan.files.map((f) => (
            <FileRow key={f.path} f={f} n={per.get(f.path) || 0} open={open === f.path} onToggle={() => setOpen(open === f.path ? null : f.path)} jobId={jobId} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FileRow({ f, n, open, onToggle, jobId }: { f: Plan["files"][number]; n: number; open: boolean; onToggle: () => void; jobId: string }) {
  return (
    <>
      <tr className="clickable" onClick={onToggle} aria-expanded={open}>
        <td className="mono">{open ? "▾ " : "▸ "}{f.path}</td>
        <td><span className="tag">{f.kind}</span></td>
        <td className="num">{f.lines}{f.truncated ? " (cut)" : ""}</td>
        <td className="num">{n || <span className="muted">0</span>}</td>
      </tr>
      {open && <tr><td colSpan={4}><Snippet jobId={jobId} file={f.path} all /></td></tr>}
    </>
  );
}

function ReportTab({ jobId }: { jobId: string }) {
  const [md, setMd] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/jobs/${jobId}/plan.md`, { credentials: "same-origin" }).then((r) => r.text()).then(setMd, () => setMd(""));
  }, [jobId]);
  if (md === null) return <div className="empty"><p>Loading…</p></div>;
  return (
    <div className="card">
      <div className="card-head"><h3>plan.md</h3><span className="muted small">paste it into an issue or merge request</span><span className="grow" /><CopyButton text={md} label="Copy Markdown" /></div>
      <div className="card-body md"><Markdown src={md} /></div>
    </div>
  );
}
