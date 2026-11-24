import { useEffect, useRef, useState } from "react";
import { AuthError, fileToBase64, submitPlan } from "../api";
import { go, useApp } from "../store";
import type { Job } from "../types";
import { SKIP_PATH, plural, size, stored, store } from "../util";

interface Picked {
  path: string;
  file: File;
}

const GOALS = [
  "Make it run on OpenShift",
  "Harden it for production",
  "Find why it crashes",
  "Fix the security problems first",
];

/** Name of a file as it was picked: keeps folder paths from folder pickers and drops. */
export function pickedPath(f: File & { webkitRelativePath?: string; _path?: string }): string {
  return f._path || f.webkitRelativePath || f.name;
}

/** Walk a dropped folder (DataTransferItem entries) into a flat list of files with their paths. */
export async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean) as FileSystemEntry[];
  if (!entries.length) return [...dt.files];
  const out: File[] = [];
  const walk = (entry: FileSystemEntry, prefix: string): Promise<void> =>
    new Promise((resolve) => {
      if (entry.isFile) {
        (entry as FileSystemFileEntry).file(
          (f) => {
            Object.defineProperty(f, "_path", { value: prefix + f.name });
            out.push(f);
            resolve();
          },
          () => resolve(),
        );
      } else if (entry.isDirectory) {
        const reader = (entry as FileSystemDirectoryEntry).createReader();
        const all: FileSystemEntry[] = [];
        const read = () =>
          reader.readEntries(
            async (batch) => {
              if (!batch.length) {
                for (const e of all) await walk(e, prefix + entry.name + "/");
                resolve();
              } else {
                all.push(...batch);
                read();
              }
            },
            () => resolve(),
          );
        read();
      } else resolve();
    });
  for (const e of entries) await walk(e, "");
  return out;
}

export function NewPlan() {
  const { status, dropped, setDropped, refreshJobs, toast, setAuthed, handleError } = useApp();
  const [picked, setPicked] = useState<Picked[]>([]);
  const [text, setText] = useState("");
  const [textName, setTextName] = useState("");
  const [goal, setGoal] = useState("");
  const [language, setLanguage] = useState<string>(() => stored("language", ""));
  const [rulesOnly, setRulesOnly] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const maxMb = status?.max_upload_mb ?? 50;
  const lang = language || status?.language || "en";
  const offline = status ? !status.ollama : false;

  const add = (files: File[]) => {
    setError("");
    setPicked((cur) => {
      const have = new Set(cur.map((p) => p.path));
      const next = [...cur];
      let skipped = 0;
      for (const f of files) {
        const path = pickedPath(f);
        if (SKIP_PATH.test(path)) {
          skipped++;
          continue;
        }
        if (!have.has(path)) {
          have.add(path);
          next.push({ path, file: f });
        }
      }
      if (skipped) toast({ text: `Left out ${plural(skipped, "file")} from .git, node_modules, virtualenvs and build output.` }, 4000);
      return next;
    });
  };

  useEffect(() => {
    if (dropped?.length) {
      add(dropped);
      setDropped(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dropped]);

  const bytes = picked.reduce((a, p) => a + p.file.size, 0) + text.length;
  const tooBig = bytes > maxMb * 1048576;
  const empty = !picked.length && !text.trim();

  const submit = async () => {
    if (empty) return setError("Add files or paste something first.");
    if (tooBig) return setError(`That is ${size(bytes)}; the limit is ${maxMb} MB. Leave out build output or vendored code.`);
    setError("");
    setProgress(0);
    try {
      const files = [];
      for (const p of picked) files.push({ name: p.path, data: await fileToBase64(p.file) });
      const job = await submitPlan<Job>({ files, text, text_name: textName.trim(), goal: goal.trim(), language: lang, no_llm: rulesOnly || offline }, setProgress);
      store("language", language);
      await refreshJobs();
      go(job.id);
    } catch (err) {
      if (err instanceof AuthError) setAuthed(false);
      else if (err instanceof Error) setError(err.message);
      else handleError(err);
    } finally {
      setProgress(null);
    }
  };

  const busy = progress !== null;
  return (
    <div className="new-plan">
      <h1>What should be fixed?</h1>
      <p className="muted lead">
        Code, config, Kubernetes or OpenShift manifests, Dockerfiles, Terraform, pipelines, logs or a stack trace. fixplan works out what each
        file is, runs its checks and the linters, has the model review it, and writes an ordered plan to fix it.
      </p>

      <div className="card drop-card">
        <div className="drop-inner">
          <div className="drop-title">Drop files, a folder or a ZIP / tar archive here</div>
          <div className="drop-actions">
            <button className="btn" type="button" onClick={() => fileInput.current?.click()} disabled={busy}>Choose files</button>
            <button className="btn" type="button" onClick={() => folderInput.current?.click()} disabled={busy}>Choose a folder</button>
          </div>
          <div className="muted small">max {maxMb} MB · binary files, .git and node_modules are skipped · nothing is executed</div>
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => { add([...(e.target.files || [])]); e.target.value = ""; }} />
          <input ref={folderInput} type="file" multiple hidden {...({ webkitdirectory: "" } as Record<string, string>)} onChange={(e) => { add([...(e.target.files || [])]); e.target.value = ""; }} />
        </div>
        {picked.length > 0 && (
          <div className="picked">
            <div className="picked-head">
              <strong>{plural(picked.length, "file")}</strong>
              <span className={"muted small" + (tooBig ? " bad" : "")}>{size(bytes)}</span>
              <button className="linklike small" type="button" onClick={() => setPicked([])} disabled={busy}>Clear</button>
            </div>
            <ul>
              {picked.slice(0, 200).map((p, i) => (
                <li key={p.path}>
                  <span className="mono ellipsis" title={p.path}>{p.path}</span>
                  <span className="muted small">{size(p.file.size)}</span>
                  <button className="toast-x" aria-label={`Remove ${p.path}`} onClick={() => setPicked(picked.filter((_, n) => n !== i))} disabled={busy}>✕</button>
                </li>
              ))}
              {picked.length > 200 && <li className="muted small">… and {picked.length - 200} more</li>}
            </ul>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head"><h3>Or paste</h3><span className="muted small">a log, an error, a manifest, a script…</span></div>
        <div className="card-body">
          <textarea className="paste" value={text} onChange={(e) => setText(e.target.value)} placeholder={"2026-11-24 10:00:03 ERROR connect: connection refused\n…"} spellCheck={false} disabled={busy} />
          <label className="field inline">
            <span className="small muted">File name, helps detection (optional)</span>
            <input className="mono" value={textName} onChange={(e) => setTextName(e.target.value)} placeholder="app.log, Dockerfile, deploy.yaml" disabled={busy} />
          </label>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Goal</h3><span className="muted small">optional: the plan is ordered around it</span></div>
        <div className="card-body">
          <input className="goal" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. make it run on OpenShift; find why the pod restarts every 10 minutes" disabled={busy} maxLength={2000} />
          <div className="goal-chips">
            {GOALS.map((g) => <button key={g} type="button" className="chip" onClick={() => setGoal(g)} disabled={busy}>{g}</button>)}
          </div>
          <div className="opt-row">
            <label className="check">
              Language
              <select value={lang} onChange={(e) => setLanguage(e.target.value)} disabled={busy}>
                <option value="en">English</option>
                <option value="nl">Nederlands</option>
              </select>
            </label>
            <label className="check" title={offline ? "Ollama is not reachable, so the plan is made from the rules" : "No model: built-in checks and linters only, in seconds"}>
              <input type="checkbox" checked={rulesOnly || offline} disabled={offline || busy} onChange={(e) => setRulesOnly(e.target.checked)} />
              Rules only (no model)
            </label>
          </div>
        </div>
      </div>

      <div className="submit-row">
        {error && <div className="form-error" role="alert">{error}</div>}
        {busy && (
          <div className="submit-progress">
            <div className="progress"><div style={{ width: `${progress}%` }} /></div>
            <span className="muted small" aria-live="polite">{progress! < 100 ? `Uploading… ${progress}%` : "Unpacking and checking…"}</span>
          </div>
        )}
        <button className="btn primary big" type="button" onClick={submit} disabled={busy || empty}>
          {busy ? "Uploading…" : "Make the plan"}
        </button>
      </div>
    </div>
  );
}
