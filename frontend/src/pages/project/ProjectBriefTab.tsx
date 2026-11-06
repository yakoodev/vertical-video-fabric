import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { PROJECT_KINDS, projectsApi, type Brief, type ChannelProject, type ProjectKind } from "@/api/projects";
import { sourcesApi } from "@/api/sources";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { Loading, formatDuration, plural } from "@/components/ui";
import { videoPath } from "@/lib/paths";

/** Подвкладка «Резюме» в «Лор и правила»: о проекте, его видео, лор и что уже нарезано. */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

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

export function ProjectBriefTab({ project }: { project: ChannelProject }) {
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
        body="Видео и клипы останутся, просто вне проектов. Резюме, правила, гипотезы и добавленные по ссылке публикации удалятся."
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
        <Link to={`${videoPath(clip.source_id, "candidates")}?clip=${clip.clip_id}`}>{clip.title}</Link>
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
        <h3>Видео проекта</h3>
        <select
          className="input series-add-source"
          value=""
          disabled={!free.length || move.isPending}
          onChange={(e) => e.target.value && move.mutate({ sourceId: Number(e.target.value), projectId: project.id })}
        >
          <option value="">{free.length ? "＋ Добавить видео…" : "Все видео уже в проектах"}</option>
          {free.map((s) => (
            <option key={s.id} value={s.id}>
              {s.display_name || s.original_filename || s.original_url || `Видео #${s.id}`}
            </option>
          ))}
        </select>
      </div>
      {project.sources?.length ? (
        <ul className="series-list">
          {project.sources.map((s) => (
            <li key={s.id} className="series-li-main">
              <Link to={videoPath(s.id)}>{s.display_name || s.original_filename || s.original_url || `Видео #${s.id}`}</Link>
              <span className="muted">
                {formatDuration(s.duration_sec)} · {plural(s.clip_cards_count ?? s.clips_count, "клип", "клипа", "клипов")}
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
        <p className="muted">Добавьте сюда стримы или серии этого проекта.</p>
      )}
    </section>
  );
}
