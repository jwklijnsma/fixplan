export const SEVS = ["critical", "high", "medium", "low", "info"] as const;
export type Severity = (typeof SEVS)[number];
export type Counts = Partial<Record<Severity, number>>;

export interface Finding {
  id: string;
  rule: string;
  severity: Severity;
  file: string;
  line: number | null;
  message: string;
  source: "static" | "llm" | string;
  category: string;
  suggestion: string;
  confidence: number | null;
}

export interface Step {
  title: string;
  why: string;
  priority: Severity;
  files: string[];
  findings: string[];
  actions: string[];
  commands: string[];
  patch: string;
  verify: string[];
  effort: string;
  risk: string;
}

export interface PlanFile {
  path: string;
  kind: string;
  lines: number;
  truncated: boolean;
}

export interface Plan {
  title: string;
  summary: string;
  generator: "llm" | "rules";
  model: string;
  seconds: number;
  steps: Step[];
  findings: Finding[];
  files: PlanFile[];
  notes: string[];
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  name: string;
  goal: string;
  status: JobStatus;
  rules_only: boolean;
  progress: { pct: number; message: string };
  created: number;
  started: number | null;
  ended: number | null;
  seconds: number;
  error: string;
  position: number | null;
  model: string;
  title: string;
  steps: number;
  findings: number;
  files: number;
  counts: Counts | null;
}

export interface JobDetail extends Job {
  plan: Plan | null;
  lines: string[];
  next: number;
}

export interface Status {
  version: string;
  ollama_host: string;
  ollama: string | null;
  ollama_error: string;
  model: string;
  default_model: string;
  model_source: "ui" | "config";
  model_available: boolean | null;
  models: string[];
  language: "en" | "nl";
  linters: Record<string, boolean>;
  auth: boolean;
  max_upload_mb: number;
}

export const isActive = (j: Pick<Job, "status">) => j.status === "queued" || j.status === "running";
