import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { aivApi } from "@/api/aivideo";
import { ApiError } from "@/api/client";
import {
  S3D_ACTIVE,
  s3dApi,
  s3dEpisodeKey,
  s3dKey,
  type S3dCatalog,
  type S3dEpisode,
  type S3dEpisodeBrief,
  type S3dJob,
  type S3dLine,
  type S3dOverview,
  type S3dPlan,
  type S3dPreset,
} from "@/api/series3d";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import "@/styles/aivideo.css";
import "@/styles/series3d.css";

/**
 * 3D-серия (app/s3d_*.py): раздел материалов проекта. Список — библиотека (персонажи, локации), серии со статусом и
 * «Новая серия» из сценария или темы. Серия (?ep=ID) — редактор плана: реплики подряд, у каждой кадры (ракурс и
 * движение камеры), игра (эмоции и жесты), события и переход; «Рендер» с оценкой; карточка проверок и лист кадров.
 */

const errText = (e: unknown, fallback: string) => {
  if (e instanceof ApiError) {
    const d = e.detail as { problems?: string[] } | null;
    return d && typeof d === "object" && d.problems?.length ? d.problems.slice(0, 4).join("; ") : e.message;
  }
  return fallback;
};
const rub = (v: number) => `${v.toFixed(v && v < 10 ? 2 : 0).replace(".", ",")} ₽`;
const TONE: Record<string, string> = { done: "ok", failed: "bad", cancelled: "bad", awaiting_approval: "warn" };
const nameOf = (v: unknown) => (typeof v === "string" ? v : v && typeof v === "object" ? String((v as { name?: string }).name ?? "") : "");

function useOverview(projectId: number) {
  return useQuery({
    queryKey: s3dKey(projectId),
    queryFn: () => s3dApi.overview(projectId),
    refetchInterval: (q) => {
      const o = q.state.data as S3dOverview | undefined;
      return o?.episodes.some((e) => S3D_ACTIVE.includes(e.status)) ? 4000 : false;
    },
  });
}

function useCatalog() {
  return useQuery({ queryKey: ["series3d", "catalog"], queryFn: s3dApi.catalog, staleTime: 3600_000 });
}

export function Series3dTab({ projectId }: { projectId: number }) {
  const [params, setParams] = useSearchParams();
  const ep = Number(params.get("ep") || 0);
  const open = (id: number | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (id) next.set("ep", String(id));
        else next.delete("ep");
        return next;
      },
      { replace: false },
    );
  const q = useOverview(projectId);
  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const o = q.data;
  return (
    <div className="aiv s3d">
      <VoiceCards o={o} />
      {ep ? (
        <EpisodeEditor o={o} episodeId={ep} onBack={() => open(null)} />
      ) : (
        <>
          <Library o={o} />
          <Episodes o={o} onOpen={open} />
          <NewEpisode o={o} onCreated={open} />
        </>
      )}
    </div>
  );
}

function VoiceCards({ o }: { o: S3dOverview }) {
  const qc = useQueryClient();
  const toast = useToast();
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "reject" }) => aivApi.decide(id, action),
    onSuccess: (_d, v) => {
      toast.success(v.action === "approve" ? "Разрешено: голос запишется, серия нарисуется сама" : "Отказано — рендер отменён");
      qc.invalidateQueries({ queryKey: ["series3d"] });
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const pending = o.approvals.filter((a) => a.status === "pending");
  if (!pending.length) return null;
  return (
    <div className="aiv-cards">
      {pending.map((c) => (
        <div key={c.id} className="panel aiv-card">
          <div className="aiv-card-text">
            <b>{c.title}</b>
            <pre className="aiv-q">{c.question}</pre>
          </div>
          <div className="series-row">
            <button className="btn primary sm" disabled={decide.isPending} onClick={() => decide.mutate({ id: c.id, action: "approve" })}>
              Разрешить · {rub(c.amount_rub)}
            </button>
            <button className="btn ghost sm" disabled={decide.isPending} onClick={() => decide.mutate({ id: c.id, action: "reject" })}>
              Отказать
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- библиотека

function Library({ o }: { o: S3dOverview }) {
  const qc = useQueryClient();
  const toast = useToast();
  const seed = useMutation({
    mutationFn: () => s3dApi.seed(o.project.id),
    onSuccess: (r) => {
      toast.success(r.added.length ? `Добавила готовых: ${r.added.length}` : "Готовые уже есть");
      qc.invalidateQueries({ queryKey: s3dKey(o.project.id) });
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const empty = !o.characters.length && !o.locations.length;
  return (
    <section className="panel s3d-lib">
      <div className="s3d-head">
        <h3>Кто и где снимается</h3>
        <button className="btn ghost sm" disabled={seed.isPending} onClick={() => seed.mutate()}>
          {empty ? "Добавить готовых персонажей и комнату" : "Добавить недостающих готовых"}
        </button>
      </div>
      {empty ? (
        <p className="muted">Библиотека пустая. Готовые: Ника, Вера, Алина, Женя, Кира и другие VRM-героини, робот Клод и комната стримерши.</p>
      ) : (
        <>
          <div className="s3d-chips">
            {o.characters.map((c) => (
              <span key={c.slug} className="s3d-chip" title={c.persona || c.type}>
                <b>{c.name}</b> <span className="muted">{c.slug} · {c.type === "vrm" ? `VRM ${c.vrm?.base}` : c.type}</span>
                {c.voice?.provider ? <span className="muted"> · голос {c.voice.provider} {c.voice.voice}</span> : null}
              </span>
            ))}
          </div>
          <div className="s3d-chips">
            {o.locations.map((l) => (
              <span key={l.slug} className="s3d-chip loc">
                <b>{l.name}</b> <span className="muted">{l.type === "plate" ? "комната стримерши" : `комната ${l.room?.preset}`}</span>
              </span>
            ))}
            {o.props.map((p) => (
              <span key={p.slug} className="s3d-chip prop">{p.name}</span>
            ))}
          </div>
          <p className="muted small">Перекрасить персонажа, дать ему голос или свою VRM-модель можно попросить агента (series3d_save_item).</p>
        </>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- серии

function Episodes({ o, onOpen }: { o: S3dOverview; onOpen: (id: number) => void }) {
  if (!o.episodes.length) return <EmptyState title="Серий пока нет" hint="Новая серия — из сценария или темы, ниже." />;
  return (
    <section className="panel">
      <h3>Серии</h3>
      <div className="s3d-eps">
        {o.episodes.map((e) => (
          <EpisodeRow key={e.id} e={e} onOpen={() => onOpen(e.id)} />
        ))}
      </div>
    </section>
  );
}

function EpisodeRow({ e, onOpen }: { e: S3dEpisodeBrief; onOpen: () => void }) {
  const job = e.job;
  const failed = job?.checks.filter((c) => !c.ok) ?? [];
  return (
    <button className="s3d-ep" onClick={onOpen}>
      {job?.sheet_url ? <img src={job.sheet_url} alt="" loading="lazy" /> : <span className="s3d-ep-ph">3D</span>}
      <span className="s3d-ep-text">
        <b>{e.title}</b>
        <span className={`s3d-status ${TONE[e.status] ?? ""}`}>
          {e.status_label}
          {job && S3D_ACTIVE.includes(job.status) && job.note ? ` — ${job.note}` : ""}
        </span>
        <span className="muted small">
          версия плана {e.plan_version} · {e.engine}
          {failed.length ? ` · проверки: ${failed.map((c) => c.label).join(", ")}` : ""}
        </span>
      </span>
    </button>
  );
}

function NewEpisode({ o, onCreated }: { o: S3dOverview; onCreated: (id: number) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<"script" | "topic">("script");
  const [form, setForm] = useState({ title: "", text: "", cast: "", duration: 30 });
  const vrm = o.characters.filter((c) => c.type === "vrm");
  const create = useMutation({
    mutationFn: () =>
      s3dApi.create(o.project.id, {
        title: form.title,
        duration: form.duration,
        cast: form.cast ? [form.cast] : undefined,
        ...(mode === "script" ? { script: form.text } : { topic: form.text }),
      }),
    onSuccess: (ep) => {
      toast.success(ep.notes?.length ? ep.notes[0] : "План готов — посмотрите и нажмите «Рендер»");
      qc.invalidateQueries({ queryKey: s3dKey(o.project.id) });
      onCreated(ep.id);
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  return (
    <section className="panel s3d-new">
      <h3>Новая серия</h3>
      <nav className="pills">
        <button className={`pill${mode === "script" ? " on" : ""}`} onClick={() => setMode("script")}>Из сценария</button>
        <button className={`pill${mode === "topic" ? " on" : ""}`} onClick={() => setMode("topic")}>По теме</button>
      </nav>
      <textarea
        className="input"
        rows={mode === "script" ? 6 : 2}
        placeholder={mode === "script" ? "Текст сценария. Строки «Ника: …» — реплики героини, остальное читает рассказчик." : "О чём серия — сценарий напишет Gemini"}
        value={form.text}
        onChange={(e) => setForm({ ...form, text: e.target.value })}
      />
      <div className="series-row">
        <input className="input" placeholder="Название (можно пусто)" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <select className="input" value={form.cast} onChange={(e) => setForm({ ...form, cast: e.target.value })}>
          <option value="">героиня — первая VRM в библиотеке</option>
          {vrm.map((c) => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        {mode === "topic" ? (
          <label className="s3d-num">
            <span>секунд</span>
            <input className="input" type="number" min={10} max={90} value={form.duration} onChange={(e) => setForm({ ...form, duration: Number(e.target.value) })} />
          </label>
        ) : null}
        <button className="btn primary" disabled={!form.text.trim() || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Пишу план…" : "Сделать план"}
        </button>
      </div>
      <p className="muted small">План пишет Gemini (бесплатно); без него — по правилам: реплика на фразу, ракурсы по кругу. Ничего не рисуется и не тратится, пока вы не нажмёте «Рендер».</p>
    </section>
  );
}

// --------------------------------------------------------------------------- редактор серии

function EpisodeEditor({ o, episodeId, onBack }: { o: S3dOverview; episodeId: number; onBack: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const cat = useCatalog();
  const q = useQuery({
    queryKey: s3dEpisodeKey(episodeId),
    queryFn: () => s3dApi.episode(episodeId),
    refetchInterval: (qq) => (S3D_ACTIVE.includes((qq.state.data as S3dEpisode | undefined)?.job?.status ?? "") ? 3000 : false),
  });
  const [plan, setPlan] = useState<S3dPlan | null>(null);
  const [dry, setDry] = useState(false);
  useEffect(() => {
    if (q.data) setPlan(structuredClone(q.data.plan));
  }, [q.data?.plan_version]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => {
    qc.invalidateQueries({ queryKey: s3dEpisodeKey(episodeId) });
    qc.invalidateQueries({ queryKey: s3dKey(o.project.id) });
  };
  const save = useMutation({
    mutationFn: () => s3dApi.savePlan(episodeId, plan as S3dPlan, "правка в редакторе"),
    onSuccess: (ep) => {
      toast.success(`Сохранено: версия ${ep.plan_version}`);
      refresh();
    },
    onError: (e) => toast.error(errText(e, "План не сохранён")),
  });
  const render = useMutation({
    mutationFn: () => s3dApi.render(episodeId, dry),
    onSuccess: (r) => {
      toast.success(r.estimate.total_rub > 0 ? `Рендер: сначала карточка за голос ${rub(r.estimate.total_rub)}` : "Рендер в очереди");
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не запустился")),
  });
  const cancel = useMutation({ mutationFn: () => s3dApi.cancel(episodeId), onSuccess: refresh });
  if (q.isLoading || !plan || !cat.data) return <Loading />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const ep = q.data;
  const dirty = JSON.stringify(plan) !== JSON.stringify(ep.plan);
  const busy = S3D_ACTIVE.includes(ep.job?.status ?? "");
  const who = ["narrator", ...plan.cast];
  const names = Object.fromEntries(o.characters.map((c) => [c.slug, c.name]));
  const setLine = (i: number, line: S3dLine) => setPlan({ ...plan, lines: plan.lines.map((l, k) => (k === i ? line : l)) });
  return (
    <>
      <section className="panel s3d-top">
        <button className="btn ghost sm" onClick={onBack}>← Все серии</button>
        <input className="input s3d-title" value={plan.title} onChange={(e) => setPlan({ ...plan, title: e.target.value })} />
        <span className="muted small">
          ≈ {ep.estimate_sec} с · {plan.lines.length} реплик · версия {ep.plan_version}
        </span>
        <div className="series-row">
          <button className="btn sm" disabled={!dirty || save.isPending || busy} onClick={() => save.mutate()}>
            Сохранить план
          </button>
          <label className="s3d-check">
            <input type="checkbox" checked={dry} onChange={(e) => setDry(e.target.checked)} /> черновик: тон вместо голоса
          </label>
          {busy ? (
            <button className="btn ghost sm" onClick={() => cancel.mutate()}>Отменить рендер</button>
          ) : (
            <button className="btn primary sm" disabled={dirty || render.isPending || !!ep.problems.length} onClick={() => render.mutate()}>
              Рендер · {dry ? "0 ₽" : ep.estimate.label}
            </button>
          )}
        </div>
        {dirty ? <p className="muted small">Сначала сохраните план — рендер идёт по сохранённой версии.</p> : null}
        {ep.problems.length ? <p className="s3d-bad">План не проходит проверку: {ep.problems.join("; ")}</p> : null}
        {ep.warnings.length ? <p className="muted small">{ep.warnings.join("; ")}</p> : null}
      </section>
      <JobCard job={ep.job} />
      <section className="panel s3d-look">
        <h3>Картинка серии</h3>
        <div className="s3d-look-row">
          <PresetPick label="Свет" items={cat.data.light} value={nameOf(plan.presets.light)} onChange={(v) => setPlan({ ...plan, presets: { ...plan.presets, light: v || undefined } })} />
          <PresetPick label="Настроение" items={cat.data.mood} value={nameOf(plan.presets.mood)} onChange={(v) => setPlan({ ...plan, presets: { ...plan.presets, mood: v || undefined } })} />
          <label className="field">
            <span>Где</span>
            <select className="input" value={plan.location} onChange={(e) => setPlan({ ...plan, location: e.target.value })}>
              {o.locations.map((l) => (
                <option key={l.slug} value={l.slug}>{l.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Читает за кадром</span>
            <select className="input" value={plan.narrator} onChange={(e) => setPlan({ ...plan, narrator: e.target.value })}>
              {who.map((w) => (
                <option key={w} value={w}>{w === "narrator" ? "рассказчик" : names[w] ?? w}</option>
              ))}
            </select>
          </label>
        </div>
      </section>
      <section className="panel">
        <h3>Реплики</h3>
        <div className="s3d-lines">
          {plan.lines.map((line, i) => (
            <LineEditor key={line.id} line={line} who={who} names={names} cat={cat.data} onChange={(l) => setLine(i, l)}
              onDelete={() => setPlan({ ...plan, lines: plan.lines.filter((_, k) => k !== i) })} />
          ))}
        </div>
        <button className="btn ghost sm" onClick={() => setPlan({ ...plan, lines: [...plan.lines, { id: `l${Date.now() % 100000}`, who: plan.narrator, text: "", shots: [{ camera: "face", move: "static" }] }] })}>
          + реплика
        </button>
      </section>
      {ep.versions.length > 1 ? (
        <section className="panel">
          <h3>Версии плана</h3>
          <ul className="s3d-versions">
            {ep.versions.slice(0, 8).map((v) => (
              <li key={v.id}>
                v{v.version} · {v.author} · {v.created_at}
                {v.note ? ` — ${v.note}` : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

function JobCard({ job }: { job: S3dJob | null }) {
  if (!job) return null;
  const active = S3D_ACTIVE.includes(job.status);
  return (
    <section className={`panel s3d-job ${TONE[job.status] ?? ""}`}>
      <div className="s3d-head">
        <h3>
          Рендер: {job.status_label}
          {job.dry_run ? " (черновик)" : ""}
        </h3>
        {job.clip_id ? (
          <Link className="btn primary sm" to={`/clips/${job.clip_id}`}>Открыть клип</Link>
        ) : null}
      </div>
      {active ? (
        <div className="s3d-progress">
          <div style={{ width: `${Math.round(job.progress * 100)}%` }} />
          <span>{job.note}</span>
        </div>
      ) : null}
      {job.error ? <p className="s3d-bad">{job.error}</p> : null}
      <div className="s3d-job-body">
        {job.sheet_url ? (
          <a href={job.sheet_url} target="_blank" rel="noreferrer" className="s3d-sheet">
            <img src={job.sheet_url} alt="лист кадров" />
          </a>
        ) : null}
        {job.checks.length ? (
          <ul className="s3d-checks">
            {job.checks.map((c) => (
              <li key={c.id} className={c.ok ? "ok" : "bad"}>
                <b>{c.ok ? "✓" : "✗"} {c.label}</b>
                <span>{c.detail}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {job.voices.length ? <p className="muted small">Голоса: {job.voices.map((v) => `${v.label} (${v.lines})`).join("; ")}</p> : null}
      {job.duration ? <p className="muted small">Длина {job.duration} с, 3D рисовалось {job.render_sec} с.</p> : null}
      {job.warnings.length ? <p className="muted small">{job.warnings.join("; ")}</p> : null}
    </section>
  );
}

function PresetPick({ label, items, value, onChange }: { label: string; items: S3dPreset[]; value: string; onChange: (v: string) => void }) {
  const cur = items.find((p) => p.name === value);
  return (
    <label className="field s3d-preset">
      <span>{label}</span>
      <div className="s3d-preset-row">
        {cur ? <img src={cur.thumb} alt="" onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")} /> : <span className="s3d-thumb-ph" />}
        <select className="input" value={value} onChange={(e) => onChange(e.target.value)} title={cur?.ru}>
          <option value="">—</option>
          {items.map((p) => (
            <option key={p.name} value={p.name}>{p.title}</option>
          ))}
        </select>
      </div>
    </label>
  );
}

function LineEditor({ line, who, names, cat, onChange, onDelete }: {
  line: S3dLine; who: string[]; names: Record<string, string>; cat: S3dCatalog; onChange: (l: S3dLine) => void; onDelete: () => void;
}) {
  const perf = useMemo(() => [...cat.emotion, ...cat.gesture], [cat]);
  const shots = line.shots ?? [];
  const cues = line.performance ?? [];
  const events = line.events ?? [];
  return (
    <div className="s3d-line">
      <div className="s3d-line-head">
        <span className="s3d-id">{line.id}</span>
        <select className="input" value={line.who} onChange={(e) => onChange({ ...line, who: e.target.value })}>
          {who.map((w) => (
            <option key={w} value={w}>{w === "narrator" ? "рассказчик" : names[w] ?? w}</option>
          ))}
        </select>
        <button className="btn ghost sm" onClick={onDelete} title="Убрать реплику">✕</button>
      </div>
      <textarea className="input" rows={2} value={line.text} onChange={(e) => onChange({ ...line, text: e.target.value })} />
      {shots.map((sh, i) => (
        <div key={i} className="s3d-row">
          <span className="s3d-tag">кадр</span>
          <select className="input" value={sh.camera} onChange={(e) => onChange({ ...line, shots: shots.map((s, k) => (k === i ? { ...s, camera: e.target.value } : s)) })}>
            {Object.entries(cat.framings).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <PresetSelect items={cat.moves} value={sh.move ?? ""} empty="наезд по умолчанию"
            onChange={(v) => onChange({ ...line, shots: shots.map((s, k) => (k === i ? { ...s, move: v || undefined } : s)) })} />
          <input className="input s3d-at" placeholder={i ? "с какого слова" : "с начала"} value={sh.at ?? ""} disabled={!i}
            onChange={(e) => onChange({ ...line, shots: shots.map((s, k) => (k === i ? { ...s, at: e.target.value || undefined } : s)) })} />
          {shots.length > 1 ? <button className="btn ghost sm" onClick={() => onChange({ ...line, shots: shots.filter((_, k) => k !== i) })}>✕</button> : null}
        </div>
      ))}
      {cues.map((c, i) => (
        <div key={`p${i}`} className="s3d-row">
          <span className="s3d-tag">игра</span>
          <PresetSelect items={perf} value={c.preset} onChange={(v) => onChange({ ...line, performance: cues.map((x, k) => (k === i ? { ...x, preset: v } : x)) })} />
          <input className="input s3d-at" placeholder="на слове" value={c.at ?? ""} onChange={(e) => onChange({ ...line, performance: cues.map((x, k) => (k === i ? { ...x, at: e.target.value || undefined } : x)) })} />
          <button className="btn ghost sm" onClick={() => onChange({ ...line, performance: cues.filter((_, k) => k !== i) })}>✕</button>
        </div>
      ))}
      {events.map((ev, i) => (
        <div key={`e${i}`} className="s3d-row">
          <span className="s3d-tag">{cat.events[ev.kind] ? ev.kind : "событие"}</span>
          {ev.kind === "comment" ? (
            <input className="input s3d-at" placeholder="ник" value={ev.user ?? ""} onChange={(e) => onChange({ ...line, events: events.map((x, k) => (k === i ? { ...x, user: e.target.value } : x)) })} />
          ) : null}
          {ev.kind === "title" || ev.kind === "comment" ? (
            <input className="input" value={ev.text ?? ""} onChange={(e) => onChange({ ...line, events: events.map((x, k) => (k === i ? { ...x, text: e.target.value } : x)) })} />
          ) : ev.kind === "sfx" ? (
            <select className="input" value={ev.sound ?? ""} onChange={(e) => onChange({ ...line, events: events.map((x, k) => (k === i ? { ...x, sound: e.target.value } : x)) })}>
              {cat.sounds.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          ) : (
            <span className="muted small">{cat.events[ev.kind] ?? ev.kind}</span>
          )}
          <button className="btn ghost sm" onClick={() => onChange({ ...line, events: events.filter((_, k) => k !== i) })}>✕</button>
        </div>
      ))}
      <div className="s3d-row s3d-add">
        <button className="btn ghost sm" onClick={() => onChange({ ...line, shots: [...shots, { camera: "face", move: "static", at: "" }] })}>+ кадр</button>
        <button className="btn ghost sm" onClick={() => onChange({ ...line, performance: [...cues, { preset: "happy" }] })}>+ эмоция/жест</button>
        <button className="btn ghost sm" onClick={() => onChange({ ...line, events: [...events, { kind: "title", text: "" }] })}>+ надпись</button>
        <button className="btn ghost sm" onClick={() => onChange({ ...line, events: [...events, { kind: "comment", user: "viewer", text: "" }] })}>+ комментарий</button>
        <button className="btn ghost sm" onClick={() => onChange({ ...line, events: [...events, { kind: "sfx", sound: "pop" }] })}>+ звук</button>
        <PresetSelect items={cat.transition} value={line.transition?.kind ?? ""} empty="без перехода"
          onChange={(v) => onChange({ ...line, transition: v ? { kind: v, duration: line.transition?.duration ?? 0.4 } : undefined })} />
      </div>
      {line.vtubika ? <p className="muted small">Есть события истории Витубики (монитор, телефон, копилка) — они исполняются как в истории.</p> : null}
    </div>
  );
}

function PresetSelect({ items, value, onChange, empty }: { items: S3dPreset[]; value: string; onChange: (v: string) => void; empty?: string }) {
  const cur = items.find((p) => p.name === value);
  return (
    <span className="s3d-preset-row">
      {cur ? <img src={cur.thumb} alt="" title={cur.ru} onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")} /> : null}
      <select className="input" value={value} title={cur?.ru} onChange={(e) => onChange(e.target.value)}>
        {empty !== undefined ? <option value="">{empty}</option> : null}
        {items.map((p) => (
          <option key={p.name} value={p.name}>{p.title}</option>
        ))}
      </select>
    </span>
  );
}
