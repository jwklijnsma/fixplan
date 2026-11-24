import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { parseDiff } from "./diff";
import { Markdown } from "./markdown";
import { parseRoute } from "./store";
import type { Job } from "./types";
import { SKIP_PATH, ago, dur, logClass, looksLikeDiff, progressPct, size, sourceLabel, statusText } from "./util";

const job = (over: Partial<Job>): Job => ({
  id: "0123456789abcdef", name: "repo.zip", goal: "", status: "running", rules_only: false,
  progress: { pct: 0.42, message: "model review 2/5: Dockerfile" }, created: 1000, started: 1001, ended: null,
  seconds: 12, error: "", position: null, model: "qwen2.5-coder:14b", title: "", steps: 0, findings: 0,
  files: 3, counts: null, ...over,
});

describe("util", () => {
  it("formats time and size", () => {
    expect(ago(100, 130)).toBe("just now");
    expect(ago(100, 100 + 7200)).toBe("2 h ago");
    expect(dur(75)).toBe("1m 15s");
    expect(size(2048)).toBe("2 KB");
  });
  it("describes jobs", () => {
    expect(statusText(job({}))).toBe("model review 2/5: Dockerfile");
    expect(statusText(job({ status: "queued", position: 2 }))).toBe("waiting · #2 in the queue");
    expect(statusText(job({ status: "done", steps: 3, findings: 1 }))).toBe("3 steps · 1 finding");
    expect(statusText(job({ status: "done" }))).toBe("nothing to fix");
    expect(progressPct(job({}))).toBe(42);
    expect(progressPct(job({ status: "queued" }))).toBe(0);
  });
  it("labels and classifies", () => {
    expect(sourceLabel("static")).toBe("FP");
    expect(sourceLabel("llm")).toBe("model");
    expect(logClass("10:00:01 error: boom")).toBe("l-bad");
    expect(logClass("10:00:01 ✓ 3 step(s)")).toBe("l-ok");
    expect(logClass("10:00:01 note: 2 binary file(s) skipped")).toBe("l-warn");
    expect(SKIP_PATH.test("repo/node_modules/x.js")).toBe(true);
    expect(SKIP_PATH.test("repo/src/build.py")).toBe(false);
    expect(looksLikeDiff("--- a/x\n+++ b/x\n@@ -1 +1 @@")).toBe(true);
    expect(looksLikeDiff("USER 1001")).toBe(false);
  });
});

describe("routes", () => {
  it("parses hashes", () => {
    expect(parseRoute("#/p/0123456789abcdef/findings")).toEqual({ page: "plan", jobId: "0123456789abcdef", tab: "findings" });
    expect(parseRoute("#/p/0123456789abcdef")).toEqual({ page: "plan", jobId: "0123456789abcdef", tab: "plan" });
    expect(parseRoute("#/new").page).toBe("new");
    expect(parseRoute("#/p/../../x").page).toBe("home");
    expect(parseRoute("").page).toBe("home");
  });
});

describe("markdown", () => {
  it("renders plan.md safely", () => {
    const html = renderToStaticMarkup(
      <Markdown src={"# Plan\n\n1. first\n2. second\n\n```bash\nkubectl apply -f x\n```\n\n> ⚠️ **Risk:** pods restart\n\n<details><summary>Resolves 1 finding(s)</summary>\n\n- `F1` x\n\n</details>\n\n| a | b \\| c |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script>"} />,
    );
    expect(html).toContain("<h1>Plan</h1>");
    expect(html).toContain("<ol><li>first</li><li>second</li></ol>");
    expect(html).toContain("<pre>kubectl apply -f x</pre>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("Resolves 1 finding(s)");
    expect(html).not.toContain("<details>");
    expect(html).toContain("<th>b | c</th>");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
  it("colours diffs", () => {
    const html = renderToStaticMarkup(<Markdown src={"```diff\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n```"} />);
    expect(html).toContain('<div class="del">-old</div>');
    expect(html).toContain('<div class="add">+new</div>');
  });
});

describe("diff", () => {
  it("splits files", () => {
    const f = parseDiff("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n");
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ name: "x", add: 1, del: 1 });
  });
});
