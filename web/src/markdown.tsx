import { useEffect, useState, type ReactNode } from "react";

// A small, safe Markdown renderer for plan.md: headings, lists, tables, code fences,
// quotes, paragraphs, `code` and **bold**. Everything ends up as React text nodes, never
// HTML; the few HTML tags plan.md uses (<details>, <summary>, <sub>) are dropped.

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    out.push(t.startsWith("`") ? <code key={k++}>{t.slice(1, -1)}</code> : <strong key={k++}>{t.slice(2, -2)}</strong>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const HTML_LINE = /^\s*<\/?(details|summary|sub)\b[^>]*>(.*?)(<\/(summary|sub)>)?\s*$/i;

export function Markdown({ src }: { src: string }) {
  const blocks: ReactNode[] = [];
  const lines = src.split("\n");
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    let l = lines[i];
    const html = l.match(HTML_LINE);
    if (html) {
      // keep the text of <summary>…</summary> and <sub>…</sub>, drop the tags
      if (html[2].trim()) blocks.push(<p key={k++} className="muted small">{inline(html[2].replace(/<[^>]+>/g, ""))}</p>);
      i++;
      continue;
    }
    if (!l.trim()) {
      i++;
      continue;
    }
    if (l.startsWith("```")) {
      const lang = l.slice(3).trim();
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(<CodeBlock key={k++} text={body.join("\n")} diff={lang === "diff"} />);
      continue;
    }
    const hm = l.match(/^(#{1,4})\s+(.*)$/);
    if (hm) {
      const level = Math.min(hm[1].length, 4);
      const H = (`h${level}` as unknown) as "h1";
      blocks.push(<H key={k++}>{inline(hm[2])}</H>);
      i++;
      continue;
    }
    if (l.startsWith(">")) {
      const q: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) q.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={k++}>{inline(q.join(" "))}</blockquote>);
      continue;
    }
    if (l.startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const cells = (r: string) => r.replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
      const hasAlign = rows[1] !== undefined && /^\|?\s*:?-+/.test(rows[1]);
      const body = rows.slice(hasAlign ? 2 : 1);
      blocks.push(
        <table key={k++}>
          <thead>
            <tr>{cells(rows[0]).map((c, j) => <th key={j}>{inline(c)}</th>)}</tr>
          </thead>
          <tbody>
            {body.map((r, n) => (
              <tr key={n}>{cells(r).map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    }
    if (/^\s*[-*] /.test(l)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*] /.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*] /, ""));
      blocks.push(<ul key={k++}>{items.map((t, n) => <li key={n}>{inline(t)}</li>)}</ul>);
      continue;
    }
    if (/^\s*\d+\. /.test(l)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\. /.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+\. /, ""));
      blocks.push(<ol key={k++}>{items.map((t, n) => <li key={n}>{inline(t)}</li>)}</ol>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#|\||>|```|\s*[-*] |\s*\d+\. )/.test(lines[i]) && !HTML_LINE.test(lines[i])) {
      l = lines[i++];
      para.push(l);
    }
    if (!para.length) {
      i++;
      continue;
    }
    blocks.push(<p key={k++}>{inline(para.join(" ").replace(/^_(.*)_$/, "$1"))}</p>);
  }
  return <>{blocks}</>;
}

/** A code block with a copy button; diffs get +/- colours. */
export function CodeBlock({ text, diff }: { text: string; diff?: boolean }) {
  return (
    <div className="codeblock">
      <CopyButton text={text} />
      {diff ? (
        <div className="diff-body">
          {text.split("\n").map((l, i) => (
            <div key={i} className={l.startsWith("@@") ? "hunk" : l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : undefined}>{l || " "}</div>
          ))}
        </div>
      ) : (
        <pre>{text}</pre>
      )}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [shown, setShown] = useState(label);
  useEffect(() => setShown(label), [label]);
  return (
    <button
      className="btn small copy"
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setShown("Copied");
        } catch {
          setShown("Copy failed");
        }
        window.setTimeout(() => setShown(label), 1300);
      }}
    >
      {shown}
    </button>
  );
}
