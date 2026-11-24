export class AuthError extends Error {}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

type Opts = { method?: "GET" | "POST"; body?: unknown };

/** Calls the fixplan API. POSTs carry the X-Fixplan header the server requires (CSRF). */
export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const init: RequestInit = { credentials: "same-origin", headers: {} };
  if (opts.method === "POST") {
    init.method = "POST";
    init.headers = { "X-Fixplan": "1", "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.body ?? {});
  }
  const res = await fetch("/api/" + path, init);
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (res.status === 401) throw new AuthError("unauthorised");
  if (!res.ok) throw new ApiError((data && data.error) || res.statusText, res.status);
  return data as T;
}

export const enc = encodeURIComponent;

export interface UploadBody {
  files: { name: string; data: string }[];
  text: string;
  text_name: string;
  goal: string;
  language: string;
  no_llm: boolean;
}

/** POST a new plan with upload progress (fetch cannot report upload progress, XHR can). */
export function submitPlan<T>(body: UploadBody, onProgress: (pct: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Fixplan", "1");
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((100 * e.loaded) / e.total));
    xhr.onerror = () => reject(new ApiError("the connection was closed (upload too large?)", 0));
    xhr.onload = () => {
      let data: any = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status === 401) reject(new AuthError("unauthorised"));
      else if (xhr.status !== 202) reject(new ApiError((data && data.error) || xhr.statusText, xhr.status));
      else resolve(data as T);
    };
    xhr.send(JSON.stringify(body));
  });
}

/** Read a File as base64 (without the data: prefix). */
export function fileToBase64(f: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(f);
  });
}
