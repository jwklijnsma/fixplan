import { SEVS, type Counts, type Job } from "./types";

export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "never";
  const s = Math.max(0, now - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function dur(s: number | null | undefined): string {
  if (s == null) return "";
  s = Math.round(s);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

export function size(n: number | null | undefined): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function total(c: Counts | null | undefined): number {
  return c ? SEVS.reduce((a, s) => a + (c[s] || 0), 0) : 0;
}

export function plural(n: number, word: string, many = word + "s"): string {
  return `${n} ${n === 1 ? word : many}`;
}

export function statusText(j: Job): string {
  switch (j.status) {
    case "queued":
      return j.position ? `waiting · #${j.position} in the queue` : "waiting";
    case "running":
      return j.progress.message || "running";
    case "cancelled":
      return j.started ? "cancelled" : "cancelled before it started";
    case "failed":
      return "failed" + (j.error ? `: ${j.error}` : "");
    case "done":
      return j.steps ? `${plural(j.steps, "step")} · ${plural(j.findings, "finding")}` : "nothing to fix";
  }
}

export function progressPct(j: Job): number {
  if (j.status === "queued") return 0;
  if (j.status !== "running") return 100;
  return Math.round(100 * (j.progress.pct || 0));
}

export function logClass(text: string): string {
  const t = text.replace(/^\d\d:\d\d:\d\d /, "");
  if (/^error|^✗/.test(t)) return "l-bad";
  if (/^warning|^note:/.test(t)) return "l-warn";
  if (/^✓/.test(t)) return "l-ok";
  return "";
}

export function sourceLabel(src: string): string {
  return src === "static" ? "FP" : src === "llm" ? "model" : src;
}

/** Skip what nobody wants reviewed, already in the browser (saves the upload). */
export const SKIP_PATH = /(^|\/)(\.git|node_modules|\.venv|venv|__pycache__|\.terraform|\.tox|dist|build)(\/|$)/;

export function looksLikeDiff(s: string): boolean {
  return /^(--- |\+\+\+ |@@ |diff --git)/m.test(s);
}

/** Per-browser conveniences; storage can be missing or throw. */
export function stored<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem("fixplan:" + key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}
export function store(key: string, value: unknown): void {
  try {
    localStorage.setItem("fixplan:" + key, JSON.stringify(value));
  } catch {
    /* private mode etc. */
  }
}
