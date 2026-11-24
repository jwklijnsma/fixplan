import { useState } from "react";
import { go, useApp, type Route } from "../store";
import { SEVS, isActive, type Counts, type Job } from "../types";
import { ago, plural, statusText, total } from "../util";

export function SevBar({ counts }: { counts: Counts | null }) {
  if (!counts) return null;
  const t = total(counts);
  if (!t) return <div className="sevbar"><span style={{ width: "100%", background: "var(--ok)" }} /></div>;
  return (
    <div className="sevbar" title={SEVS.map((s) => `${s}: ${counts[s] || 0}`).join(" · ")}>
      {SEVS.filter((s) => counts[s]).map((s) => <span key={s} style={{ width: `${(100 * counts[s]!) / t}%`, background: `var(--sev-${s})` }} />)}
    </div>
  );
}

function PlanItem({ j, current, tab }: { j: Job; current: boolean; tab: string }) {
  const dot = j.status === "running" ? "run" : j.status === "queued" ? "wait" : j.status === "failed" ? "bad" : null;
  return (
    <li>
      <button aria-current={current} onClick={() => go(j.id, current ? tab || "plan" : "plan")}>
        <div className="repo-name">
          <span className="ellipsis">{j.title && j.title !== `Fix plan: ${j.name}` ? j.title : j.name}</span>
          {j.rules_only && <span className="zip-badge" title="Built without a model">RULES</span>}
          {dot && <span className={"dot " + dot} title={j.status} />}
        </div>
        <div className="repo-meta">
          {ago(j.created)} · {j.status === "done" ? plural(j.steps, "step") + " · " + plural(j.findings, "finding") : isActive(j) ? statusText(j) : j.status}
        </div>
        {j.status === "running" ? (
          <div className="progress thin"><div style={{ width: `${Math.round(100 * j.progress.pct)}%` }} /></div>
        ) : (
          <SevBar counts={j.counts} />
        )}
      </button>
    </li>
  );
}

export function Sidebar({ route }: { route: Route }) {
  const { jobs } = useApp();
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const shown = jobs.filter((j) => !needle || `${j.name} ${j.title} ${j.goal}`.toLowerCase().includes(needle));
  return (
    <aside className="sidebar">
      <div className="upload-box">
        <a className="btn primary upload-btn" href="#/new" aria-current={route.page === "new"}>+ New plan</a>
        <div className="upload-hint">or drop files anywhere on the page</div>
      </div>
      <h2>Plans</h2>
      {jobs.length > 6 && <input className="search" type="search" placeholder="Filter…" aria-label="Filter plans" value={q} onChange={(e) => setQ(e.target.value)} />}
      <ul className="repo-list">
        {!jobs.length && <li className="empty-roots">No plans yet. Upload something to get the first one.</li>}
        {shown.map((j) => <PlanItem key={j.id} j={j} current={j.id === route.jobId} tab={route.tab} />)}
      </ul>
    </aside>
  );
}
