import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  HYP_STATUS_LABEL,
  METRIC_OPTIONS,
  PROJECT_KINDS,
  RULE_KIND_LABEL,
  projectsApi,
  type AddRuleResult,
  type Brief,
  type ChannelProject,
  type Hypothesis,
  type NumbersInput,
  type ProjectKind,
  type Rule,
  type RuleKind,
  type StatsItem,
} from "@/api/projects";
import { sourcesApi } from "@/api/sources";
import { formatCount, formatSnapshotTime } from "@/api/stats";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading, formatDuration, plural } from "@/components/ui";
import { AutopilotTab, ProjectChatTab } from "@/pages/SeriesAutopilot";
import { AiVideoTab } from "@/pages/AiVideoTab";
import { ExplainerTab } from "@/pages/ExplainerTab";
import { ExplainerChannelTab } from "@/pages/ExplainerChannelTab";
import { useModules } from "@/hooks/useModules";

/**
 * Проект-серия/канал (app/projects.py): лор, правила владельца, статистика и гипотезы.
 * Маршрут /series/:projectId — /projects/:id занят источниками («проект» в старом смысле).
 */

type Tab = "aiv" | "channel" | "expl" | "brief" | "rules" | "stats" | "hyp" | "autopilot" | "chat";
// Третий элемент — модуль вкладки (app/modules.py): выключен — вкладки нет.
const TABS: [Tab, string, string?][] = [
  // Только у проекта вида «ИИ-видео» (app/aivideo.py) — см. фильтр ниже.
  ["aiv", "ИИ-видео", "ai_video"],
  // Канал объяснялок (app/explainer_channel.py): маскот ведёт канал — темы, расписание, автопилот.
  ["channel", "Канал", "explainer"],
  // Объяснялки (app/explainer.py): маскот объясняет тему — у «ИИ-видео» и «канала объяснялок».
  ["expl", "Объяснялки", "explainer"],
  ["brief", "Резюме"],
  ["rules", "Правила"],
  ["stats", "Статистика"],
  ["hyp", "Гипотезы"],
  ["autopilot", "Автопилот", "autopilot"],
  ["chat", "Чат", "agent_chat"],
];

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const today = () => new Date().toISOString().slice(0, 10);
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1).replace(".", ",")}%`);

export function SeriesPage() {
  const { projectId } = useParams();
  const id = Number(projectId);
  // Вкладка — в адресе (?tab=expl): перезагрузка и ссылка открывают то же место.
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") as Tab | null;
  const setTab = (next: Tab) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set("tab", next);
        p.delete("sub");
        return p;
      },
      { replace: true },
    );
  const project = useQuery({ queryKey: qk.channelProject(id), queryFn: () => projectsApi.get(id), enabled: id > 0 });
  const { on } = useModules();
  const isAiv = project.data?.kind === "ai_video";
  const isChannel = project.data?.kind === "explainer";
  const tabs = TABS.filter(
    ([key, , mod]) =>
      (!mod || on(mod)) &&
      (key === "aiv" ? isAiv : key === "expl" || key === "channel" ? isAiv || isChannel : true),
  );
  const fallback: Tab = isChannel && tabs.some(([key]) => key === "channel")
    ? "channel"
    : isAiv && tabs.some(([key]) => key === "aiv") ? "aiv" : "brief";
  const shown: Tab = tab && tabs.some(([key]) => key === tab) ? tab : fallback;

  if (project.isLoading) return <Loading />;
  if (project.isError || !project.data) return <ErrorState error={project.error} onRetry={() => project.refetch()} />;
  const p = project.data;

  return (
    <>
      <div className="series-head">
        <Link to="/projects" className="muted">
          ← Все проекты
        </Link>
        <h1>{p.name}</h1>
        <div className="series-meta muted">
          <span className="chip">{p.kind_label}</span>
          <span>{plural(p.sources?.length ?? 0, "источник", "источника", "источников")}</span>
          {p.brand ? <span>бренд «{p.brand.name}»</span> : null}
        </div>
      </div>
      <nav className="pipeline series-tabs">
        {tabs.map(([key, label]) => (
          <button key={key} className={`tab${shown === key ? " active" : ""}`} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>
      <div className="series-body">
        {shown === "aiv" ? <AiVideoTab projectId={id} /> : null}
        {shown === "channel" ? <ExplainerChannelTab projectId={id} /> : null}
        {shown === "expl" ? <ExplainerTab projectId={id} /> : null}
        {shown === "brief" ? <BriefTab project={p} /> : null}
        {shown === "rules" ? <RulesTab projectId={id} /> : null}
        {shown === "stats" ? <StatsTab projectId={id} /> : null}
        {shown === "hyp" ? <HypothesesTab projectId={id} /> : null}
        {shown === "autopilot" ? <AutopilotTab projectId={id} /> : null}
        {shown === "chat" ? <ProjectChatTab projectId={id} /> : null}
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- //
// Резюме: о проекте, источники, лор, что уже нарезано
// --------------------------------------------------------------------------- //

type SectionKey = "people" | "arcs" | "jokes" | "taboo";
const SECTIONS: { key: SectionKey; title: string; fields: { name: string; label: string; wide?: boolean }[] }[] = [
  {
    key: "people",
    title: "Персонажи и люди",
    fields: [
      { name: "name", label: "Имя" },
      { name: "who", label: "Кто это" },
      { name: "traits", label: "Приметы", wide: true },
      { name: "aliases", label: "Как ещё зовут (через запятую)" },
    ],
  },
  {
    key: "arcs",
    title: "Арки и сквозные темы",
    fields: [
      { name: "title", label: "Название" },
      { name: "about", label: "О чём", wide: true },
    ],
  },
  {
    key: "jokes",
    title: "Внутренние шутки и мемы",
    fields: [
      { name: "text", label: "Шутка" },
      { name: "about", label: "Откуда / когда звучит", wide: true },
    ],
  },
  {
    key: "taboo",
    title: "Запретные темы",
    fields: [
      { name: "topic", label: "Тема" },
      { name: "why", label: "Почему", wide: true },
    ],
  },
];

type Row = Record<string, string>;
type Draft = { summary: string; notes: string } & Record<SectionKey, Row[]>;

function toDraft(brief: Brief): Draft {
  const rows = (items: object[]) =>
    items.map((item) =>
      Object.fromEntries(
        Object.entries(item).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v ?? "")]),
      ),
    );
  return {
    summary: brief.summary,
    notes: brief.notes,
    people: rows(brief.people),
    arcs: rows(brief.arcs),
    jokes: rows(brief.jokes),
    taboo: rows(brief.taboo),
  };
}

function fromDraft(draft: Draft) {
  const keyOf: Record<SectionKey, string> = { people: "name", arcs: "title", jokes: "text", taboo: "topic" };
  const clean = (key: SectionKey) => draft[key].filter((row) => (row[keyOf[key]] ?? "").trim());
  return {
    summary: draft.summary,
    notes: draft.notes,
    people: clean("people").map((r) => ({ ...r, aliases: (r.aliases ?? "").split(",").map((a) => a.trim()).filter(Boolean) })),
    arcs: clean("arcs"),
    jokes: clean("jokes"),
    taboo: clean("taboo"),
    replace_sections: ["people", "arcs", "jokes", "taboo"],
  };
}

function BriefTab({ project }: { project: ChannelProject }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const id = project.id;
  const brief = useQuery({ queryKey: qk.channelBrief(id), queryFn: () => projectsApi.brief(id) });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (brief.data && !dirty) setDraft(toDraft(brief.data.brief));
  }, [brief.data, dirty]);

  const save = useMutation({
    mutationFn: () => projectsApi.updateBrief(id, fromDraft(draft!), brief.data?.brief_rev),
    onSuccess: () => {
      setDirty(false);
      toast.success("Резюме сохранено");
      qc.invalidateQueries({ queryKey: qk.channelBrief(id) });
    },
    onError: (e) => toast.error(errText(e, "Не удалось сохранить резюме")),
  });
  const remove = useMutation({
    mutationFn: () => projectsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.channelProjects });
      qc.invalidateQueries({ queryKey: qk.sources });
      navigate("/projects");
    },
    onError: (e) => toast.error(errText(e, "Не удалось удалить")),
  });

  const edit = (patch: Partial<Draft>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setDirty(true);
  };

  return (
    <div className="series-grid">
      <div className="series-col">
        <ProjectSettings project={project} />
        <SourcesPanel project={project} />
        <section className="panel series-panel">
          <div className="series-panel-head">
            <h3>Что уже нарезано</h3>
            <span className="muted">{brief.data ? plural(brief.data.covered_total, "клип", "клипа", "клипов") : ""}</span>
          </div>
          <p className="muted series-hint">Агент видит этот список и не режет те же моменты второй раз. Теги — признаки для гипотез.</p>
          {brief.data?.covered.length ? (
            <ul className="series-list">
              {brief.data.covered.map((c) => (
                <CoveredRow key={c.clip_id} clip={c} projectId={id} />
              ))}
            </ul>
          ) : (
            <p className="muted">Пока ничего: клипы появятся здесь после рендера, ★ или публикации.</p>
          )}
        </section>
        <button className="btn danger sm series-delete" onClick={() => setDeleting(true)}>
          Удалить проект
        </button>
      </div>
      <section className="panel series-panel">
        <div className="series-panel-head">
          <h3>Резюме и лор</h3>
          <button className="btn primary sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Сохраняю…" : dirty ? "Сохранить" : "Сохранено"}
          </button>
        </div>
        <p className="muted series-hint">Агент читает это перед нарезкой и названиями (MCP get_project_brief).</p>
        {!draft ? (
          <Loading />
        ) : (
          <>
            <label className="field">
              <span>Кратко о проекте</span>
              <textarea className="input" rows={4} value={draft.summary} onChange={(e) => edit({ summary: e.target.value })} />
            </label>
            {SECTIONS.map((section) => (
              <div key={section.key} className="series-section">
                <div className="series-section-head">
                  <strong>{section.title}</strong>
                  <button
                    className="btn ghost sm"
                    onClick={() => edit({ [section.key]: [...draft[section.key], {}] } as Partial<Draft>)}
                  >
                    ＋ Добавить
                  </button>
                </div>
                {draft[section.key].length === 0 ? <p className="muted">—</p> : null}
                {draft[section.key].map((row, index) => (
                  <div key={index} className="series-row">
                    {section.fields.map((f) => (
                      <input
                        key={f.name}
                        className={`input${f.wide ? " wide" : ""}`}
                        placeholder={f.label}
                        value={row[f.name] ?? ""}
                        onChange={(e) => {
                          const rows = draft[section.key].map((r, i) => (i === index ? { ...r, [f.name]: e.target.value } : r));
                          edit({ [section.key]: rows } as Partial<Draft>);
                        }}
                      />
                    ))}
                    <button
                      className="btn ghost sm"
                      title="Убрать"
                      onClick={() => edit({ [section.key]: draft[section.key].filter((_, i) => i !== index) } as Partial<Draft>)}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            ))}
            <label className="field">
              <span>Заметки</span>
              <textarea className="input" rows={3} value={draft.notes} onChange={(e) => edit({ notes: e.target.value })} />
            </label>
          </>
        )}
      </section>
      <ConfirmDialog
        open={deleting}
        title="Удалить проект?"
        body="Источники и клипы останутся — просто без проекта. Резюме, правила, гипотезы и добавленные по ссылке публикации удалятся."
        busy={remove.isPending}
        onCancel={() => setDeleting(false)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

function CoveredRow({ clip, projectId }: { clip: { clip_id: number; title: string; source_id: number; pieces: [number, number][]; published: boolean; favorite: boolean; tags: string[] }; projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [tag, setTag] = useState("");
  const tags = useMutation({
    mutationFn: (body: { add?: string[]; remove?: string[] }) => projectsApi.setClipTags(clip.clip_id, body),
    onSuccess: () => {
      setTag("");
      qc.invalidateQueries({ queryKey: qk.channelBrief(projectId) });
    },
    onError: (e) => toast.error(errText(e, "Не удалось поменять теги")),
  });
  return (
    <li>
      <div className="series-li-main">
        <Link to={`/projects/${clip.source_id}/candidates?clip=${clip.clip_id}`}>{clip.title}</Link>
        <span className="muted mono">
          {clip.pieces.map(([a, b]) => `${formatDuration(a)}–${formatDuration(b)}`).join(", ")}
        </span>
        {clip.favorite ? <span title="Избранное">★</span> : null}
        {clip.published ? <span className="chip">опубликован</span> : null}
      </div>
      <div className="series-tags">
        {clip.tags.map((t) => (
          <button key={t} className="chip" title="Убрать тег" onClick={() => tags.mutate({ remove: [t] })}>
            #{t} ✕
          </button>
        ))}
        <input
          className="input series-tag-input"
          placeholder="＋ тег"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && tag.trim() && tags.mutate({ add: [tag.trim()] })}
        />
      </div>
    </li>
  );
}

function ProjectSettings({ project }: { project: ChannelProject }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(project.name);
  const [kind, setKind] = useState<ProjectKind>(project.kind);
  const [description, setDescription] = useState(project.description);
  const [links, setLinks] = useState(project.links.map((l) => l.url).join("\n"));
  const changed =
    name !== project.name ||
    kind !== project.kind ||
    description !== project.description ||
    links !== project.links.map((l) => l.url).join("\n");
  const save = useMutation({
    mutationFn: () =>
      projectsApi.update(project.id, {
        name,
        kind,
        description,
        links: links.split("\n").map((u) => u.trim()).filter(Boolean).map((url) => ({ label: "", url })),
      }),
    onSuccess: () => {
      toast.success("Проект сохранён");
      qc.invalidateQueries({ queryKey: qk.channelProjects });
    },
    onError: (e) => toast.error(errText(e, "Не удалось сохранить")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>О проекте</h3>
        <button className="btn sm" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
          Сохранить
        </button>
      </div>
      <div className="series-row">
        <input className="input wide" value={name} onChange={(e) => setName(e.target.value)} placeholder="Название" />
        <select className="input" value={kind} onChange={(e) => setKind(e.target.value as ProjectKind)}>
          {PROJECT_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </div>
      <label className="field">
        <span>Описание</span>
        <textarea className="input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="field">
        <span>Канал и ссылки (по одной в строке)</span>
        <textarea className="input mono" rows={2} value={links} onChange={(e) => setLinks(e.target.value)} />
      </label>
    </section>
  );
}

function SourcesPanel({ project }: { project: ChannelProject }) {
  const qc = useQueryClient();
  const toast = useToast();
  const all = useQuery({ queryKey: qk.sources, queryFn: sourcesApi.list });
  const free = (all.data ?? []).filter((s) => !s.project_id);
  const move = useMutation({
    mutationFn: ({ sourceId, projectId }: { sourceId: number; projectId: number | null }) =>
      projectsApi.setSourceProject(sourceId, projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.channelProject(project.id) });
      qc.invalidateQueries({ queryKey: qk.channelProjects });
      qc.invalidateQueries({ queryKey: qk.sources });
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Источники</h3>
        <select
          className="input series-add-source"
          value=""
          disabled={!free.length || move.isPending}
          onChange={(e) => e.target.value && move.mutate({ sourceId: Number(e.target.value), projectId: project.id })}
        >
          <option value="">{free.length ? "＋ Добавить видео…" : "Все видео уже в проектах"}</option>
          {free.map((s) => (
            <option key={s.id} value={s.id}>
              {s.original_filename || s.original_url || `Источник #${s.id}`}
            </option>
          ))}
        </select>
      </div>
      {project.sources?.length ? (
        <ul className="series-list">
          {project.sources.map((s) => (
            <li key={s.id} className="series-li-main">
              <Link to={`/projects/${s.id}`}>{s.original_filename || s.original_url || `Источник #${s.id}`}</Link>
              <span className="muted">
                {formatDuration(s.duration_sec)} · {plural(s.clips_count, "клип", "клипа", "клипов")}
              </span>
              <button
                className="btn ghost sm"
                title="Убрать из проекта (видео останется)"
                onClick={() => move.mutate({ sourceId: s.id, projectId: null })}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Добавьте сюда стримы или серии этого канала.</p>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Правила владельца
// --------------------------------------------------------------------------- //

function RulesTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [history, setHistory] = useState(false);
  const status = history ? "all" : "active";
  const rules = useQuery({ queryKey: qk.channelRules(projectId, status), queryFn: () => projectsApi.rules(projectId, status) });
  const [form, setForm] = useState({ text: "", kind: "do" as RuleKind, scope: "project" as "project" | "global", quote: "", said_at: today() });
  const [pending, setPending] = useState<AddRuleResult | null>(null);
  const [revoking, setRevoking] = useState<Rule | null>(null);
  const [reason, setReason] = useState("");

  const refresh = () => qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId), "rules"] });
  const add = useMutation({
    mutationFn: (extra: { replaces?: number[]; force?: boolean }) =>
      projectsApi.addRule({ ...form, project_id: projectId, origin: "ui", ...extra }),
    onSuccess: (res) => {
      if (res.created) {
        toast.success("Правило записано");
        setForm((f) => ({ ...f, text: "", quote: "" }));
        setPending(null);
        refresh();
      } else if (res.needs_confirmation) {
        setPending(res);
      } else {
        toast.push(res.note || "Такое правило уже есть", "info");
      }
    },
    onError: (e) => toast.error(errText(e, "Не удалось записать правило")),
  });
  const revoke = useMutation({
    mutationFn: () => projectsApi.revokeRule(revoking!.id, reason),
    onSuccess: () => {
      setRevoking(null);
      setReason("");
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не удалось отменить")),
  });

  const list = rules.data ?? [];
  return (
    <div className="series-grid">
      <section className="panel series-panel">
        <h3>Новое правило</h3>
        <p className="muted series-hint">
          Из ответа владельца — с цитатой и датой. Похожее уже есть — не продублируется; противоречит старому —
          спросим, что делать.
        </p>
        <label className="field">
          <span>Правило</span>
          <input className="input" value={form.text} placeholder="Не брать политику" onChange={(e) => setForm({ ...form, text: e.target.value })} />
        </label>
        <div className="series-row">
          <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as RuleKind })}>
            {(Object.keys(RULE_KIND_LABEL) as RuleKind[]).map((k) => (
              <option key={k} value={k}>
                {RULE_KIND_LABEL[k]}
              </option>
            ))}
          </select>
          <select className="input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value as "project" | "global" })}>
            <option value="project">Для этого проекта</option>
            <option value="global">Для всех проектов</option>
          </select>
          <input className="input" type="date" value={form.said_at} onChange={(e) => setForm({ ...form, said_at: e.target.value })} />
        </div>
        <label className="field">
          <span>Цитата владельца</span>
          <textarea className="input" rows={2} value={form.quote} onChange={(e) => setForm({ ...form, quote: e.target.value })} />
        </label>
        <button className="btn primary" disabled={!form.text.trim() || add.isPending} onClick={() => add.mutate({})}>
          Записать
        </button>
        {pending ? (
          <div className="series-conflict">
            <strong>Противоречит действующему правилу</strong>
            {pending.conflicts.map((c) => (
              <p key={c.id}>
                #{c.id} «{c.text}»{c.said_at ? ` от ${c.said_at.slice(0, 10)}` : ""}
              </p>
            ))}
            <p className="muted">Заменить старое новым или оставить оба?</p>
            <div className="series-row">
              <button className="btn primary sm" onClick={() => add.mutate({ replaces: pending.conflicts.map((c) => c.id) })}>
                Заменить старое
              </button>
              <button className="btn sm" onClick={() => add.mutate({ force: true })}>
                Оставить оба
              </button>
              <button className="btn ghost sm" onClick={() => setPending(null)}>
                Отмена
              </button>
            </div>
          </div>
        ) : null}
      </section>
      <section className="panel series-panel">
        <div className="series-panel-head">
          <h3>{history ? "Все правила и история" : "Действующие правила"}</h3>
          <label className="check">
            <input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} /> история
          </label>
        </div>
        {rules.isLoading ? <Loading /> : null}
        {!rules.isLoading && !list.length ? <p className="muted">Правил пока нет.</p> : null}
        <ul className="series-list">
          {list.map((r) => (
            <li key={r.id} className={r.status === "active" ? "" : "series-li-off"}>
              <div className="series-li-main">
                <span className={`series-kind series-kind--${r.kind}`}>{RULE_KIND_LABEL[r.kind]}</span>
                <strong>{r.text}</strong>
                {r.scope === "global" ? <span className="chip">все проекты</span> : null}
                {r.needs_review ? <span className="chip series-review" title={r.review_note}>на пересмотр</span> : null}
                {r.status === "active" ? (
                  <button className="btn ghost sm" onClick={() => setRevoking(r)}>
                    Отменить
                  </button>
                ) : null}
              </div>
              {r.quote ? <blockquote className="series-quote">«{r.quote}»</blockquote> : null}
              <div className="muted series-li-sub">
                #{r.id} · {r.said_at.slice(0, 10)}
                {r.origin ? ` · ${r.origin}` : ""}
                {r.status === "revoked" ? ` · отменено ${formatSnapshotTime(r.revoked_at)}: ${r.revoke_reason || "без причины"}` : ""}
                {r.status === "superseded" ? ` · заменено правилом #${r.superseded_by}` : ""}
                {r.needs_review ? ` · ${r.review_note}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </section>
      <ConfirmDialog
        open={Boolean(revoking)}
        title="Отменить правило?"
        confirmLabel="Отменить правило"
        cancelLabel="Не отменять"
        body={
          <>
            <p>«{revoking?.text}» останется в истории, но агент перестанет его соблюдать.</p>
            <input className="input" autoFocus placeholder="Почему (например: пересмотрел, не работает)" value={reason} onChange={(e) => setReason(e.target.value)} />
          </>
        }
        busy={revoke.isPending}
        onCancel={() => setRevoking(null)}
        onConfirm={() => revoke.mutate()}
      />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Статистика и опубликованное руками
// --------------------------------------------------------------------------- //

function StatsTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const stats = useQuery({ queryKey: qk.channelStats(projectId), queryFn: () => projectsApi.stats(projectId) });
  const pubs = useQuery({ queryKey: qk.channelPublications(projectId), queryFn: () => projectsApi.publications(projectId) });
  const brief = useQuery({ queryKey: qk.channelBrief(projectId), queryFn: () => projectsApi.brief(projectId) });
  const [form, setForm] = useState({ url: "", clip: "", title: "", published_at: "", views: "", likes: "", retention: "" });
  const [numbersFor, setNumbersFor] = useState<number | null>(null);
  const [numbers, setNumbers] = useState({ views: "", likes: "", retention: "" });

  const reload = () => {
    qc.invalidateQueries({ queryKey: qk.channelStats(projectId) });
    qc.invalidateQueries({ queryKey: qk.channelPublications(projectId) });
  };
  const num = (v: string) => (v.trim() ? Number(v.replace(",", ".")) : null);
  const toNumbers = (n: { views: string; likes: string; retention: string }): NumbersInput | undefined => {
    const out: NumbersInput = { views: num(n.views), likes: num(n.likes), avg_retention: num(n.retention) };
    return Object.values(out).some((v) => v != null) ? out : undefined;
  };
  const add = useMutation({
    mutationFn: () => {
      const statsIn = toNumbers({ views: form.views, likes: form.likes, retention: form.retention });
      return projectsApi.addPublication(projectId, {
        url: form.url.trim(),
        clip_plan_id: form.clip ? Number(form.clip) : null,
        title: form.title,
        published_at: form.published_at,
        stats: statsIn,
        fetch: !statsIn,
      });
    },
    onSuccess: () => {
      toast.success("Публикация добавлена");
      setForm({ url: "", clip: "", title: "", published_at: "", views: "", likes: "", retention: "" });
      reload();
    },
    onError: (e) => toast.error(errText(e, "Не удалось добавить")),
  });
  const refresh = useMutation({
    mutationFn: (pubId: number) => projectsApi.refreshPublication(pubId),
    onSuccess: (res) => {
      if (!res.ok) toast.push(res.error || "Числа не получены", "info");
      reload();
    },
  });
  const saveNumbers = useMutation({
    mutationFn: () => projectsApi.publicationNumbers(numbersFor!, toNumbers(numbers) ?? {}),
    onSuccess: () => {
      setNumbersFor(null);
      reload();
    },
    onError: (e) => toast.error(errText(e, "Не удалось сохранить числа")),
  });
  const remove = useMutation({ mutationFn: (pubId: number) => projectsApi.removePublication(pubId), onSuccess: reload });

  if (stats.isLoading) return <Loading />;
  if (stats.isError || !stats.data) return <ErrorState error={stats.error} onRetry={() => stats.refetch()} />;
  const s = stats.data;
  const t = s.totals;
  return (
    <div className="series-col">
      <div className="series-tiles">
        <Tile label="Просмотры" value={formatCount(t.views)} />
        <Tile label="Медиана на клип" value={formatCount(t.median_views == null ? null : Math.round(t.median_views))} />
        <Tile label="Лайки" value={formatCount(t.likes)} />
        <Tile label="Удержание" value={pct(t.avg_retention)} />
        <Tile label="Публикаций" value={String(t.publications)} sub={`${t.items_with_stats} из ${t.items} с числами`} />
      </div>
      <p className="muted series-hint">{s.note}</p>
      <div className="series-grid">
        <TopList title="Лучшие" items={s.best} empty="Пока нет клипов с числами." />
        <TopList title="Худшие" items={s.worst} empty="Пока не с чем сравнивать — мало клипов с числами." />
      </div>
      <section className="panel series-panel">
        <h3>Выложено руками</h3>
        <p className="muted series-hint">
          Видео, опубликованное не через сервис: YouTube и TikTok подтянут числа сами (тем же сбором, что
          «Публикации»), Instagram — только вручную.
        </p>
        <div className="series-row">
          <input className="input wide" placeholder="Ссылка на TikTok / YouTube / Shorts / Reels" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
          <select className="input" value={form.clip} onChange={(e) => setForm({ ...form, clip: e.target.value })}>
            <option value="">Без клипа (к проекту)</option>
            {(brief.data?.covered ?? []).map((c) => (
              <option key={c.clip_id} value={c.clip_id}>
                {c.title}
              </option>
            ))}
          </select>
        </div>
        <div className="series-row">
          <input className="input wide" placeholder="Название (необязательно)" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <input className="input" type="date" value={form.published_at} onChange={(e) => setForm({ ...form, published_at: e.target.value })} />
          <input className="input series-num" placeholder="просмотры" value={form.views} onChange={(e) => setForm({ ...form, views: e.target.value })} />
          <input className="input series-num" placeholder="лайки" value={form.likes} onChange={(e) => setForm({ ...form, likes: e.target.value })} />
          <input className="input series-num" placeholder="удерж. %" value={form.retention} onChange={(e) => setForm({ ...form, retention: e.target.value })} />
          <button className="btn primary" disabled={!form.url.trim() || add.isPending} onClick={() => add.mutate()}>
            Добавить
          </button>
        </div>
        <ul className="series-list">
          {(pubs.data ?? []).map((p) => (
            <li key={p.id}>
              <div className="series-li-main">
                <span className="chip">{p.platform || "ссылка"}</span>
                <a href={p.url} target="_blank" rel="noreferrer">
                  {p.title || p.clip_title || p.url}
                </a>
                <span className="mono">{formatCount(p.stats?.views ?? null)} просм.</span>
                <button className="btn ghost sm" title="Подтянуть числа с площадки" disabled={refresh.isPending} onClick={() => refresh.mutate(p.id)}>
                  ↻
                </button>
                <button className="btn ghost sm" onClick={() => setNumbersFor(numbersFor === p.id ? null : p.id)}>
                  Числа
                </button>
                <button className="btn ghost sm" title="Убрать" onClick={() => remove.mutate(p.id)}>
                  ✕
                </button>
              </div>
              <div className="muted series-li-sub">
                {p.clip_title ? `клип «${p.clip_title}» · ` : ""}
                {p.stats?.captured_at ? `сняты ${formatSnapshotTime(p.stats.captured_at)} (${p.stats.origin === "manual" ? "вручную" : "с площадки"})` : "чисел нет"}
                {p.stats?.error ? ` · ${p.stats.error}` : ""}
              </div>
              {numbersFor === p.id ? (
                <div className="series-row">
                  <input className="input series-num" placeholder="просмотры" value={numbers.views} onChange={(e) => setNumbers({ ...numbers, views: e.target.value })} />
                  <input className="input series-num" placeholder="лайки" value={numbers.likes} onChange={(e) => setNumbers({ ...numbers, likes: e.target.value })} />
                  <input className="input series-num" placeholder="удерж. %" value={numbers.retention} onChange={(e) => setNumbers({ ...numbers, retention: e.target.value })} />
                  <button className="btn sm primary" disabled={!toNumbers(numbers) || saveNumbers.isPending} onClick={() => saveNumbers.mutate()}>
                    Сохранить
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="panel series-tile">
      <span className="muted">{label}</span>
      <strong>{value}</strong>
      {sub ? <small className="muted">{sub}</small> : null}
    </div>
  );
}

function TopList({ title, items, empty }: { title: string; items: StatsItem[]; empty: string }) {
  return (
    <section className="panel series-panel">
      <h3>{title}</h3>
      {!items.length ? <p className="muted">{empty}</p> : null}
      <ol className="series-list">
        {items.map((i) => (
          <li key={i.key} className="series-li-main">
            <span>{i.title || i.key}</span>
            <span className="mono">{formatCount(i.views)}</span>
            <span className="muted">{i.views_per_day != null ? `${formatCount(Math.round(i.views_per_day))}/день` : ""}</span>
            {i.tags.map((t) => (
              <span key={t} className="chip">
                #{t}
              </span>
            ))}
          </li>
        ))}
      </ol>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Гипотезы
// --------------------------------------------------------------------------- //

function groupLabel(group: Record<string, unknown>): string {
  if (typeof group.tag === "string") return `#${group.tag}`;
  if (typeof group.feature === "string") return `${group.feature} ${group.op} ${String(group.value)}`;
  return JSON.stringify(group);
}

function HypothesesTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const hyps = useQuery({ queryKey: qk.channelHypotheses(projectId), queryFn: () => projectsApi.hypotheses(projectId) });
  const brief = useQuery({ queryKey: qk.channelBrief(projectId), queryFn: () => projectsApi.brief(projectId) });
  const [form, setForm] = useState({ statement: "", tag: "", metric: "views", min_n: "5", rule_text: "" });
  const knownTags = useMemo(
    () => Array.from(new Set((brief.data?.covered ?? []).flatMap((c) => c.tags))).sort(),
    [brief.data],
  );
  const reload = () => qc.invalidateQueries({ queryKey: qk.channelHypotheses(projectId) });

  const add = useMutation({
    mutationFn: () =>
      projectsApi.addHypothesis(projectId, {
        statement: form.statement,
        group: form.tag.trim(),
        metric: form.metric,
        min_n: Number(form.min_n) || 5,
        rule_text: form.rule_text,
      }),
    onSuccess: () => {
      setForm({ statement: "", tag: "", metric: "views", min_n: "5", rule_text: "" });
      reload();
    },
    onError: (e) => toast.error(errText(e, "Не удалось добавить гипотезу")),
  });
  const evaluate = useMutation({
    mutationFn: () => projectsApi.evaluate(projectId),
    onSuccess: () => {
      toast.success("Гипотезы пересчитаны");
      reload();
      qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId), "rules"] });
    },
    onError: (e) => toast.error(errText(e, "Не удалось пересчитать")),
  });
  const promote = useMutation({
    mutationFn: (hid: number) => projectsApi.promote(hid),
    onSuccess: (res) => {
      if (res.created) toast.success("Правило записано");
      else toast.push(res.question || res.note || "Правило не записано", "info");
      qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId), "rules"] });
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const unlock = useMutation({
    mutationFn: (h: Hypothesis) => projectsApi.updateHypothesis(h.id, { status_locked: false }),
    onSuccess: reload,
  });
  const remove = useMutation({ mutationFn: (hid: number) => projectsApi.removeHypothesis(hid), onSuccess: reload });

  return (
    <div className="series-grid">
      <section className="panel series-panel">
        <h3>Новая гипотеза</h3>
        <p className="muted series-hint">
          Клипы с тегом сравниваются с остальными клипами проекта по медиане. Пока в группе меньше N клипов с
          числами — «мало данных».
        </p>
        <label className="field">
          <span>Утверждение</span>
          <input className="input" placeholder="Хук-вопрос в первые 2 с даёт больше просмотров" value={form.statement} onChange={(e) => setForm({ ...form, statement: e.target.value })} />
        </label>
        <div className="series-row">
          <input className="input wide" list="series-tags" placeholder="тег группы, например hook_question" value={form.tag} onChange={(e) => setForm({ ...form, tag: e.target.value })} />
          <datalist id="series-tags">
            {knownTags.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
          <select className="input" value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })}>
            {METRIC_OPTIONS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          <input className="input series-num" title="Минимум клипов в каждой группе" value={form.min_n} onChange={(e) => setForm({ ...form, min_n: e.target.value })} />
        </div>
        <label className="field">
          <span>Правило, если подтвердится (необязательно)</span>
          <input className="input" value={form.rule_text} onChange={(e) => setForm({ ...form, rule_text: e.target.value })} />
        </label>
        <button className="btn primary" disabled={!form.statement.trim() || !form.tag.trim() || add.isPending} onClick={() => add.mutate()}>
          Добавить
        </button>
      </section>
      <section className="panel series-panel">
        <div className="series-panel-head">
          <h3>Гипотезы</h3>
          <button className="btn sm" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
            {evaluate.isPending ? "Считаю…" : "Пересчитать"}
          </button>
        </div>
        {hyps.isLoading ? <Loading /> : null}
        {!hyps.isLoading && !(hyps.data ?? []).length ? (
          <EmptyState icon="🧪" title="Гипотез пока нет" hint="Агент предлагает их сам (add_hypothesis) или добавьте слева" />
        ) : null}
        <ul className="series-list">
          {(hyps.data ?? []).map((h) => (
            <li key={h.id}>
              <div className="series-li-main">
                <span className={`series-hyp series-hyp--${h.status}`}>{HYP_STATUS_LABEL[h.status]}</span>
                <strong>{h.statement}</strong>
                <span className="chip">{groupLabel(h.group)}</span>
                {h.status === "confirmed" ? (
                  <button className="btn sm" onClick={() => promote.mutate(h.id)}>
                    Сделать правилом
                  </button>
                ) : null}
                {h.status_locked ? (
                  <button className="btn ghost sm" title="Статус выставлен вручную — снова считать по числам" onClick={() => unlock.mutate(h)}>
                    🔒
                  </button>
                ) : null}
                <button className="btn ghost sm" title="Удалить" onClick={() => remove.mutate(h.id)}>
                  ✕
                </button>
              </div>
              <div className="muted series-li-sub">
                {h.conclusion || "ещё не считалась"}
                {h.evaluated_at ? ` · ${formatSnapshotTime(h.evaluated_at)}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
