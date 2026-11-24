import { useEffect, useState } from "react";
import { useApp } from "../store";
import { isActive, type Job } from "../types";
import { ago, dur, progressPct, statusText } from "../util";

function JobRow({ job, open, onToggle }: { job: Job; open: boolean; onToggle: () => void }) {
  const { cancelJob, setJobsOpen } = useApp();
  const dot = job.status === "running" ? "run" : job.status === "queued" ? "wait" : job.status === "done" ? "ok" : "bad";
  return (
    <li className={"job-row" + (open ? " open" : "")}>
      <div className="job-row-head">
        <span className={"dot " + dot} />
        <div className="job-row-main">
          <div className="job-row-title">
            <a href={`#/p/${job.id}/${job.status === "done" ? "plan" : "log"}`} onClick={() => setJobsOpen(false)}>{job.title && job.status === "done" ? job.title : job.name}</a>
          </div>
          <div className="job-row-meta">
            {statusText(job)}
            {job.started ? ` · ${dur(job.seconds)}` : ` · queued ${ago(job.created)}`}
            {!isActive(job) && job.ended ? ` · ${ago(job.ended)}` : ""}
          </div>
          {job.goal && <div className="job-row-meta">goal: {job.goal}</div>}
        </div>
        <div className="job-row-actions">
          {isActive(job) && <button className="btn small danger" onClick={() => cancelJob(job.id)}>{job.status === "queued" ? "Remove" : "Cancel"}</button>}
          <button className="btn small" onClick={onToggle} aria-expanded={open}>{open ? "Less" : "More"}</button>
        </div>
      </div>
      {isActive(job) && (
        <div className={"progress" + (job.status === "queued" ? " waiting" : "")}><div style={{ width: `${progressPct(job)}%` }} /></div>
      )}
      {open && (
        <div className="job-row-meta">
          {job.files ? `${job.files} files · ` : ""}{job.rules_only ? "rules only" : job.model}
          {" · "}<a href={`#/p/${job.id}/log`} onClick={() => setJobsOpen(false)}>full log</a>
        </div>
      )}
    </li>
  );
}

export function JobsPanel() {
  const { jobs, jobsOpen, setJobsOpen, focusJob } = useApp();
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    if (focusJob) setOpen(focusJob);
  }, [focusJob]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setJobsOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setJobsOpen]);
  if (!jobsOpen) return null;

  const running = jobs.filter((j) => j.status === "running");
  const queued = jobs.filter((j) => j.status === "queued").sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const finished = jobs.filter((j) => !isActive(j)).slice(0, 30);
  const section = (title: string, list: Job[]) =>
    list.length ? (
      <section>
        <h3>{title}</h3>
        <ul className="job-list">
          {list.map((j) => <JobRow key={j.id} job={j} open={open === j.id} onToggle={() => setOpen(open === j.id ? null : j.id)} />)}
        </ul>
      </section>
    ) : null;

  return (
    <>
      <div className="scrim" onClick={() => setJobsOpen(false)} />
      <aside className="jobs-panel" aria-label="Background jobs">
        <header>
          <h2>Background jobs</h2>
          <button className="btn small" onClick={() => setJobsOpen(false)} aria-label="Close">✕</button>
        </header>
        <p className="jobs-hint">Plans are made on the server one after another (one GPU) and keep going when you close this page.</p>
        {section("Running", running)}
        {section("Waiting", queued)}
        {section("Finished", finished)}
        {!jobs.length && <div className="empty"><p>No jobs yet. Make a plan and it shows up here.</p></div>}
      </aside>
    </>
  );
}
