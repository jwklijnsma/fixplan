import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, AuthError } from "./api";
import { isActive, type Job, type Status } from "./types";
import { dur, plural } from "./util";

export interface Toast {
  id: number;
  text: string;
  link?: { label: string; href?: string; download?: boolean; onClick?: () => void };
  kind?: "ok" | "bad";
}

interface Ctx {
  authed: boolean | null;
  setAuthed: (v: boolean) => void;
  status: Status | null;
  jobs: Job[];
  /** Bumped per job whenever it finishes, so an open plan page reloads. */
  jobVersion: Record<string, number>;
  toasts: Toast[];
  toast: (t: Omit<Toast, "id">, ms?: number) => void;
  dismiss: (id: number) => void;
  refreshJobs: () => Promise<void>;
  refreshStatus: () => Promise<void>;
  /** Set the model for every new plan ("" = back to the configured default). */
  setModel: (model: string) => Promise<boolean>;
  cancelJob: (id: string) => Promise<void>;
  deleteJob: (id: string) => Promise<boolean>;
  jobsOpen: boolean;
  setJobsOpen: (v: boolean) => void;
  openJob: (id: string) => void;
  focusJob: string | null;
  /** Files dropped anywhere on the page, handed to the New plan form. */
  dropped: File[] | null;
  setDropped: (f: File[] | null) => void;
  handleError: (err: unknown) => void;
}

const AppContext = createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const c = useContext(AppContext);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
}

let toastSeq = 0;

export function AppProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobVersion, setJobVersion] = useState<Record<string, number>>({});
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [focusJob, setFocusJob] = useState<string | null>(null);
  const [dropped, setDropped] = useState<File[] | null>(null);
  const prevJobs = useRef<Map<string, Job["status"]>>(new Map());
  const openJob = useCallback((id: string) => {
    setFocusJob(id);
    setJobsOpen(true);
  }, []);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const toast = useCallback(
    (t: Omit<Toast, "id">, ms = 6000) => {
      const id = ++toastSeq;
      setToasts((list) => [...list.slice(-3), { ...t, id }]);
      window.setTimeout(() => dismiss(id), ms);
    },
    [dismiss],
  );

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof AuthError) setAuthed(false);
      else toast({ text: err instanceof Error ? err.message : String(err), kind: "bad" });
    },
    [toast],
  );

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api<Status>("status"));
      setAuthed(true);
    } catch (err) {
      if (err instanceof AuthError) setAuthed(false);
    }
  }, []);

  const refreshJobs = useCallback(async () => {
    let list: Job[];
    try {
      list = (await api<{ jobs: Job[] }>("jobs")).jobs;
    } catch (err) {
      if (err instanceof AuthError) setAuthed(false);
      return;
    }
    // Announce plans that finished since the last poll (they may have run while you were elsewhere).
    const finished: Job[] = [];
    for (const j of list) {
      const before = prevJobs.current.get(j.id);
      if (before && (before === "queued" || before === "running") && !isActive(j)) finished.push(j);
    }
    prevJobs.current = new Map(list.map((j) => [j.id, j.status]));
    setJobs(list);
    if (finished.length) {
      setJobVersion((v) => {
        const n = { ...v };
        for (const j of finished) n[j.id] = (n[j.id] || 0) + 1;
        return n;
      });
      for (const j of finished) {
        if (j.status === "done") {
          toast({ text: `Plan for ${j.name} ready in ${dur(j.seconds)}: ${plural(j.steps, "step")}.`, kind: "ok", link: { label: "Open", href: `#/p/${j.id}/plan` } }, 10000);
        } else if (j.status === "failed") {
          toast({ text: `Plan for ${j.name} failed.`, kind: "bad", link: { label: "Show log", onClick: () => openJob(j.id) } }, 12000);
        }
      }
    }
  }, [toast, openJob]);

  const setModel = useCallback(
    async (model: string) => {
      try {
        const r = await api<{ model: string; model_source: string }>("settings", { method: "POST", body: { model } });
        await refreshStatus();
        toast({ text: r.model_source === "ui" ? `New plans use ${r.model}.` : `Back to the default model, ${r.model}.`, kind: "ok" });
        return true;
      } catch (err) {
        handleError(err);
        return false;
      }
    },
    [refreshStatus, toast, handleError],
  );

  const cancelJob = useCallback(
    async (id: string) => {
      try {
        await api(`jobs/${id}/cancel`, { method: "POST" });
        await refreshJobs();
      } catch (err) {
        handleError(err);
      }
    },
    [refreshJobs, handleError],
  );

  const deleteJob = useCallback(
    async (id: string) => {
      try {
        await api(`jobs/${id}/delete`, { method: "POST" });
        await refreshJobs();
        return true;
      } catch (err) {
        handleError(err);
        return false;
      }
    },
    [refreshJobs, handleError],
  );

  // Boot + polling. Jobs poll fast while anything is queued or running.
  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);
  useEffect(() => {
    if (!authed) return;
    refreshJobs();
    const t = window.setInterval(refreshStatus, 30000);
    return () => window.clearInterval(t);
  }, [authed, refreshStatus, refreshJobs]);
  const anyActive = jobs.some(isActive);
  useEffect(() => {
    if (!authed) return;
    const t = window.setInterval(refreshJobs, anyActive ? 1200 : 8000);
    return () => window.clearInterval(t);
  }, [authed, anyActive, refreshJobs]);

  // The tab title shows background work, so it is visible from other tabs too.
  useEffect(() => {
    const running = jobs.filter(isActive).length;
    document.title = running ? `(${running}) fixplan` : "fixplan";
  }, [jobs]);

  const value = useMemo<Ctx>(
    () => ({ authed, setAuthed, status, jobs, jobVersion, toasts, toast, dismiss, refreshJobs, refreshStatus, setModel, cancelJob, deleteJob, jobsOpen, setJobsOpen, openJob, focusJob, dropped, setDropped, handleError }),
    [authed, status, jobs, jobVersion, toasts, toast, dismiss, refreshJobs, refreshStatus, setModel, cancelJob, deleteJob, jobsOpen, openJob, focusJob, dropped, handleError],
  );
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

/** Routes: `#/new` and `#/p/<job>/<tab>`. */
export interface Route {
  page: "new" | "plan" | "home";
  jobId: string | null;
  tab: string;
}

export function parseRoute(hash: string): Route {
  const p = hash.match(/^#\/p\/([a-f0-9]{16})(?:\/(\w+))?/);
  if (p) return { page: "plan", jobId: p[1], tab: p[2] || "plan" };
  if (hash.startsWith("#/new")) return { page: "new", jobId: null, tab: "" };
  return { page: "home", jobId: null, tab: "" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (jobId: string | null, tab = "plan") => {
  location.hash = jobId ? `#/p/${jobId}/${tab}` : "#/new";
};
