import { Fragment, type ReactNode } from "react";

/**
 * Markdown ответа агента — без пакетов и без innerHTML: абзацы, заголовки, списки, цитаты,
 * код, **жирный**, *курсив*, `код`, ссылки. Плюс сервисное: «клип #123» и «источник #7» —
 * ссылки, а таймкоды рядом с ними («12.5 с», «1:05», «1:02:03») открывают клип/источник на
 * этой секунде (onRef). Без явного клипа в абзаце таймкод относится к defaultRef — клипу,
 * которым занимался агент в этом ответе.
 */

export interface MediaRef {
  kind: "clip" | "source";
  id: number;
  t?: number;
}

interface Ctx {
  ref: MediaRef | null;
  onRef?: (ref: MediaRef) => void;
  key: number;
}

const INLINE = new RegExp(
  [
    "(?<code>`[^`\\n]+`)",
    "(?<bold>\\*\\*[^*\\n]+\\*\\*)",
    "(?<link>\\[[^\\]\\n]+\\]\\((?:https?://|/)[^)\\s]+\\))",
    "(?<url>https?://[^\\s<>()]+[^\\s<>().,;:!?»\"'])",
    "(?<ref>(?:клип[а-я]*|источник[а-я]*)\\s*(?:№|#)\\s*\\d+)",
    "(?<hms>(?<![\\d:.])\\d{1,2}:\\d{2}:\\d{2}(?:[.,]\\d+)?(?![\\d:]))",
    "(?<ms>(?<![\\d:.])\\d{1,3}:\\d{2}(?:[.,]\\d+)?(?![\\d:]))",
    "(?<sec>(?<![\\d:.,])\\d+(?:[.,]\\d+)?\\s?(?:с|сек|s)(?![а-яёa-z]))",
    "(?<em>(?<![\\w*])\\*[^*\\s][^*\\n]*\\*(?![\\w*]))",
  ].join("|"),
  "giu",
);

function toSeconds(text: string): number {
  const clean = text.replace(",", ".").replace(/\s?(сек|с|s)$/u, "");
  if (!clean.includes(":")) return Number(clean);
  return clean.split(":").reduce((acc, part) => acc * 60 + Number(part), 0);
}

function inline(text: string, ctx: Ctx): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  INLINE.lastIndex = 0;
  const re = new RegExp(INLINE.source, INLINE.flags);
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const g = m.groups ?? {};
    const k = ctx.key++;
    if (g.code) out.push(<code key={k}>{g.code.slice(1, -1)}</code>);
    else if (g.bold) out.push(<strong key={k}>{inline(g.bold.slice(2, -2), ctx)}</strong>);
    else if (g.em) out.push(<em key={k}>{inline(g.em.slice(1, -1), ctx)}</em>);
    else if (g.link) {
      const lm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(g.link)!;
      out.push(
        <a key={k} href={lm[2]} target={lm[2].startsWith("/") ? undefined : "_blank"} rel="noreferrer">
          {lm[1]}
        </a>,
      );
    } else if (g.url) {
      out.push(
        <a key={k} href={g.url} target="_blank" rel="noreferrer">
          {g.url}
        </a>,
      );
    } else if (g.ref) {
      const id = Number(/\d+/.exec(g.ref.replace(/^[^#№]*[#№]/, ""))![0]);
      const ref: MediaRef = { kind: /^клип/i.test(g.ref) ? "clip" : "source", id };
      ctx.ref = ref;
      out.push(
        ctx.onRef ? (
          <button key={k} type="button" className="md-ref" onClick={() => ctx.onRef?.(ref)}>
            {g.ref}
          </button>
        ) : (
          g.ref
        ),
      );
    } else {
      const raw = g.hms || g.ms || g.sec || "";
      const ref = ctx.ref;
      if (ref && ctx.onRef) {
        const t = toSeconds(raw.trim());
        out.push(
          <button
            key={k}
            type="button"
            className="md-time"
            title={`${ref.kind === "clip" ? "Клип" : "Источник"} #${ref.id} на ${t} с`}
            onClick={() => ctx.onRef?.({ ...ref, t })}
          >
            {raw}
          </button>,
        );
      } else out.push(raw);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { type: "p"; lines: string[] }
  | { type: "h"; level: number; text: string }
  | { type: "code"; text: string }
  | { type: "quote"; lines: string[] }
  | { type: "list"; ordered: boolean; items: string[] };

function blocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push({ type: "code", text: body.join("\n") });
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      out.push({ type: "h", level: h[1].length, text: h[2] });
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push({ type: "quote", lines: body });
      continue;
    }
    const li = /^\s*([-*•]|\d+[.)])\s+/;
    if (li.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      while (i < lines.length && (li.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (li.test(lines[i])) items.push(lines[i].replace(li, ""));
        else items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      out.push({ type: "list", ordered, items });
      continue;
    }
    const body: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*```/.test(lines[i]) && !li.test(lines[i]) && !/^#{1,4}\s/.test(lines[i]) && !/^\s*>/.test(lines[i]))
      body.push(lines[i++]);
    out.push({ type: "p", lines: body });
  }
  return out;
}

export function Markdown({
  text,
  defaultRef,
  onRef,
}: {
  text: string;
  defaultRef?: MediaRef | null;
  onRef?: (ref: MediaRef) => void;
}) {
  const ctx: Ctx = { ref: null, onRef, key: 0 };
  const withLines = (lines: string[]) =>
    lines.map((l, i) => (
      <Fragment key={i}>
        {i ? <br /> : null}
        {inline(l, ctx)}
      </Fragment>
    ));
  return (
    <div className="md">
      {blocks(text).map((b, i) => {
        ctx.ref = defaultRef ?? null; // клип из абзаца не перетекает в следующий
        switch (b.type) {
          case "code":
            return (
              <pre key={i}>
                <code>{b.text}</code>
              </pre>
            );
          case "h":
            return (
              <div key={i} className={`md-h md-h${b.level}`}>
                {inline(b.text, ctx)}
              </div>
            );
          case "quote":
            return <blockquote key={i}>{withLines(b.lines)}</blockquote>;
          case "list": {
            const items = b.items.map((it, j) => {
              ctx.ref = defaultRef ?? null;
              return <li key={j}>{inline(it, ctx)}</li>;
            });
            return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
          }
          default:
            return <p key={i}>{withLines(b.lines)}</p>;
        }
      })}
    </div>
  );
}
