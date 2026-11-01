import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { aivApi } from "@/api/aivideo";
import { audioTracksApi } from "@/api/assets";
import { ApiError } from "@/api/client";
import {
  ACTIVE,
  TONES,
  explApi,
  explKey,
  explVideoKey,
  type ExplainerOverview,
  type ExplainerVideo,
  type Mascot,
  type Pose,
} from "@/api/explainer";
import { useToast } from "@/components/Toast";
import { TtsVoicePicker, type TtsVoiceValue } from "@/components/TtsVoicePicker";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import "@/styles/aivideo.css";
import "@/styles/explainer.css";

/**
 * «Объяснялки» (app/explainer*.py): маскот объясняет тему. Карточка маскота (лист, голос с пробой,
 * цвета, музыка), библиотека поз (теги, перерисовать, убрать), новый ролик (тема, длительность,
 * тон), ход конвейера, карточка с суммой (новые позы + платная озвучка) и ссылка на клип в редакторе.
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const rub = (v: number | null | undefined) => `${(v ?? 0).toFixed(v && v < 10 ? 2 : 0).replace(".", ",")} ₽`;
const STATUS_TONE: Record<string, string> = { done: "ok", failed: "bad", cancelled: "bad", awaiting_approval: "warn" };

function useInvalidate(projectId: number) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: explKey(projectId) });
    qc.invalidateQueries({ queryKey: ["explainer", "video"] });
  };
}

export function ExplainerTab({ projectId }: { projectId: number }) {
  const [picked, setPicked] = useState<number | null>(null);
  const q = useQuery({
    queryKey: explKey(projectId),
    queryFn: () => explApi.overview(projectId),
    refetchInterval: (query) => {
      const o = query.state.data as ExplainerOverview | undefined;
      const busy = o?.videos.some((v) => ACTIVE.includes(v.status)) || o?.approvals.some((a) => a.status === "pending");
      return busy ? 4000 : false;
    },
  });
  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const o = q.data;
  const mascot = o.mascots.find((m) => m.id === picked) ?? o.mascots[0];
  return (
    <div className="aiv expl">
      <ApprovalBanners o={o} />
      {o.provider === "none" ? (
        <div className="panel expl-note">
          Проект в <b>сухом прогоне</b> (провайдер none): позы — заглушки, озвучка — тон, 0 ₽. Настоящие картинки —
          провайдер Polza во вкладке «ИИ-видео».
        </div>
      ) : null}
      {o.mascots.length > 1 ? (
        <nav className="pipeline aiv-subtabs">
          {o.mascots.map((m) => (
            <button key={m.id} className={`tab${mascot?.id === m.id ? " active" : ""}`} onClick={() => setPicked(m.id)}>
              {m.name}
            </button>
          ))}
        </nav>
      ) : null}
      {mascot ? (
        <>
          <NewVideo o={o} mascot={mascot} />
          <Videos o={o} mascot={mascot} />
          <MascotCard key={mascot.id} mascot={mascot} projectId={o.project.id} />
          <PoseLibrary mascot={mascot} projectId={o.project.id} />
        </>
      ) : null}
      <NewMascot o={o} empty={!o.mascots.length} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Карточки владельцу (новые позы + платная озвучка)
// --------------------------------------------------------------------------- //

function ApprovalBanners({ o }: { o: ExplainerOverview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "reject" }) => aivApi.decide(id, action),
    onSuccess: (_d, v) => {
      toast.success(v.action === "approve" ? "Разрешено — позы рисуются, ролик соберётся сам" : "Отказано");
      invalidate();
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
            <b>
              {c.title} · {c.count} шт.
            </b>
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

// --------------------------------------------------------------------------- //
// Новый ролик и ролики
// --------------------------------------------------------------------------- //

function NewVideo({ o, mascot }: { o: ExplainerOverview; mascot: Mascot }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [form, setForm] = useState({ topic: "", duration: 45, tone: TONES[0], render: false });
  const create = useMutation({
    mutationFn: () => explApi.createVideo(mascot.id, form),
    onSuccess: () => {
      toast.success("Ролик в работе: сценарий → позы → (карточка, если нужны новые позы) → клип");
      setForm({ ...form, topic: "" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не создано")),
  });
  const ready = mascot.poses.filter((p) => p.ready).length;
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Новый ролик: {mascot.name} объясняет</h3>
        <span className="muted">в библиотеке {ready} поз — готовые бесплатны, новая ≈ 3 ₽</span>
      </div>
      <textarea
        className="input"
        rows={2}
        placeholder="Тема: «почему ты не высыпаешься», «как перестать откладывать», «3 ошибки при беге»…"
        value={form.topic}
        onChange={(e) => setForm({ ...form, topic: e.target.value })}
      />
      <div className="series-row">
        <label className="expl-field">
          <span className="muted">Длина: {form.duration} с</span>
          <input type="range" min={30} max={60} step={5} value={form.duration} onChange={(e) => setForm({ ...form, duration: Number(e.target.value) })} />
        </label>
        <select className="input" value={form.tone} onChange={(e) => setForm({ ...form, tone: e.target.value })} title="Тон сценария">
          {TONES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <label className="aiv-check" title="После сборки: рендер, обложки, заголовок и описание">
          <input type="checkbox" checked={form.render} onChange={(e) => setForm({ ...form, render: e.target.checked })} /> сразу рендер и обложка
        </label>
        <button className="btn primary sm" disabled={form.topic.trim().length < 3 || !mascot.sheet || create.isPending} onClick={() => create.mutate()}>
          ✨ Сделать ролик
        </button>
      </div>
      {!mascot.sheet ? <span className="aiv-warn">Сначала лист маскота (ниже): без него новые позы не нарисовать.</span> : null}
    </section>
  );
}

function Videos({ o, mascot }: { o: ExplainerOverview; mascot: Mascot }) {
  const videos = o.videos.filter((v) => v.mascot_id === mascot.id);
  if (!videos.length) return null;
  return (
    <section className="panel series-panel">
      <h3>Ролики</h3>
      <div className="expl-videos">
        {videos.map((v) => (
          <VideoRow key={v.id} v={v} projectId={o.project.id} poses={mascot.poses} />
        ))}
      </div>
    </section>
  );
}

/**
 * Ошибка ролика: первая часть — что случилось (крупно), хвост после « — » — подсказка агенту
 * (имена инструментов вроде explainer_set_script) — мельче и приглушённо.
 */
function VideoError({ text, failed }: { text: string; failed: boolean }) {
  const at = text.indexOf(" — ");
  const main = at > 0 ? text.slice(0, at) : text;
  const hint = at > 0 ? text.slice(at + 3) : "";
  return (
    <div className="expl-error">
      <span className={failed ? "aiv-warn" : "muted"}>{main}</span>
      {hint ? <span className="muted expl-error-hint">{hint}</span> : null}
    </div>
  );
}

function VideoRow({ v, projectId, poses }: { v: ExplainerVideo; projectId: number; poses: Pose[] }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [open, setOpen] = useState(false);
  const detail = useQuery({ queryKey: explVideoKey(v.id), queryFn: () => explApi.video(v.id), enabled: open });
  const act = useMutation({
    mutationFn: (what: "continue" | "cancel" | "delete" | "reassemble") =>
      what === "continue"
        ? explApi.continueVideo(v.id)
        : what === "reassemble"
          ? explApi.continueVideo(v.id, true)
          : what === "cancel"
            ? explApi.cancelVideo(v.id)
            : explApi.deleteVideo(v.id),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const active = ACTIVE.includes(v.status);
  const d = detail.data;
  const beats = d?.timeline?.beats?.length ? d.timeline.beats : d?.beats ?? [];
  const byId = new Map(poses.map((p) => [p.id, p]));
  return (
    <div className="expl-video">
      <div className="expl-video-head">
        <span className={`chip expl-status ${STATUS_TONE[v.status] ?? ""}`}>
          {active ? <span className="spinner sm" /> : null} {v.status_label}
        </span>
        <b className="expl-topic" title={v.topic}>
          {v.title || v.topic}
        </b>
        <span className="muted">{v.duration_target.toFixed(0)} с{v.tone ? ` · ${v.tone}` : ""}</span>
        {v.estimate_rub > 0 ? <span className="muted">≈ {rub(v.estimate_rub)}</span> : null}
        <span className="expl-video-actions">
          {v.clip_id && v.editor_url ? (
            <Link className="btn sm primary" to={v.editor_url}>
              Открыть клип #{v.clip_id}
            </Link>
          ) : null}
          {v.status === "failed" || v.status === "cancelled" ? (
            <button className="btn sm" disabled={act.isPending} onClick={() => act.mutate("continue")}>
              ↻ Продолжить
            </button>
          ) : null}
          {v.status === "done" ? (
            <button className="btn ghost sm" disabled={act.isPending} onClick={() => act.mutate("reassemble")} title="Новый клип из тех же битов">
              Пересобрать
            </button>
          ) : null}
          {active ? (
            <button className="btn ghost sm" disabled={act.isPending} onClick={() => act.mutate("cancel")}>
              Отменить
            </button>
          ) : (
            <button className="btn ghost sm" disabled={act.isPending} onClick={() => window.confirm("Удалить ролик? Клип останется.") && act.mutate("delete")} title="Удалить ролик (клип останется)">
              Удалить
            </button>
          )}
        </span>
      </div>
      {v.error ? <VideoError text={v.error} failed={v.status === "failed"} /> : null}
      {/* Раньше сценарий раскрывался кликом по названию — догадаться было нельзя; теперь явная кнопка. */}
      <button type="button" className="expl-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "▾ Скрыть сценарий" : "▸ Сценарий и биты"}
      </button>
      {open ? (
        detail.isLoading ? (
          <Loading />
        ) : d ? (
          <div className="expl-detail">
            {d.estimate ? (
              <div className="aiv-est">
                <span className="chip">поз из библиотеки: {d.estimate.reused_poses}</span>
                <span className="chip">новых: {d.estimate.new_poses} ≈ {rub(d.estimate.poses_rub)}</span>
                <span className="chip">озвучка ≈ {rub(d.estimate.narration_rub)}</span>
                {d.readiness?.held.length ? <span className="aiv-warn">{d.readiness.held[0]}</span> : null}
              </div>
            ) : null}
            {d.script?.text ? <p className="expl-script">{d.script.text}</p> : null}
            {d.script?.warnings?.length ? <div className="aiv-warn">{d.script.warnings.join("; ")}</div> : null}
            {beats.length ? (
              <div className="expl-beats">
                {beats.map((b, n) => {
                  const pose = byId.get(b.pose_id);
                  return (
                    <div key={`${b.i}-${n}`} className="expl-beat">
                      <div className="expl-beat-img">
                        {pose?.url ? <img src={pose.url} alt="" loading="lazy" /> : <span>нарисуется</span>}
                        {b.badge ? <span className="expl-badge">{b.badge}</span> : null}
                      </div>
                      <div className="expl-beat-text">
                        {b.start !== undefined ? <span className="mono muted">{b.start.toFixed(1)} с</span> : null}
                        <span>{b.text}</span>
                        <span className="muted">
                          {b.pose_title || pose?.title}
                          {b.pose_new ? " · новая" : ""}
                          {b.variant === "punch" ? " · наезд" : ""}
                          {b.chip?.text ? ` · чип «${b.chip.text}»` : ""}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}
            {d.notes?.length ? (
              <ul className="expl-notes muted">
                {d.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Маскот
// --------------------------------------------------------------------------- //

function MascotCard({ mascot, projectId }: { mascot: Mascot; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const tracks = useQuery({ queryKey: ["audio-tracks"], queryFn: audioTracksApi.list, staleTime: 60_000 });
  const [m, setM] = useState({
    name: mascot.name,
    identity_card: mascot.identity_card,
    persona: mascot.persona,
    style_passport: mascot.style_passport,
    bg_color: mascot.bg_color,
    accent_color: mascot.accent_color,
    chip_color: mascot.chip_color,
    music_track_id: mascot.music_track_id ?? 0,
  });
  const [voice, setVoice] = useState<TtsVoiceValue>({ ...mascot.voice, voice: mascot.voice.voice ?? "", model: mascot.voice.model ?? "" });
  const save = useMutation({
    mutationFn: () => explApi.editMascot(mascot.id, { ...m, voice }),
    onSuccess: () => {
      toast.success("Маскот сохранён");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  const sheet = useMutation({
    mutationFn: (f: File) => explApi.uploadSheet(mascot.id, f),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(errText(e, "Не загружено")),
  });
  const gen = useMutation({
    mutationFn: () => explApi.generateSheet(mascot.id, !!mascot.sheet),
    onSuccess: (r) => {
      toast.success(r.status === "awaiting_approval" ? "Карточка на лист ушла владельцу" : "Лист готов");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3 title={`id для агентов: ${mascot.slug}`}>Маскот «{mascot.name}»</h3>
        {/* «Сохранить» — в шапке карточки (было в строке голоса, терялось среди полей). */}
        <button className="btn sm primary" disabled={save.isPending} onClick={() => save.mutate()}>
          Сохранить
        </button>
      </div>
      <div className="expl-mascot">
        <div className="expl-sheet" style={{ background: mascot.bg_color }}>
          {mascot.sheet ? <img src={mascot.sheet.url} alt="лист маскота" /> : <span>листа нет</span>}
          <div className="series-row">
            <label className="btn ghost sm">
              ⬆ Загрузить лист
              <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && sheet.mutate(e.target.files[0])} />
            </label>
            <button className="btn ghost sm" disabled={gen.isPending} onClick={() => gen.mutate()} title="Модель картинок, ≈ 3 ₽ по карточке">
              {mascot.sheet ? "↻ Перерисовать" : "Нарисовать лист"}
            </button>
          </div>
        </div>
        <div className="expl-mascot-form">
          <input className="input" value={m.name} onChange={(e) => setM({ ...m, name: e.target.value })} placeholder="Имя" />
          <textarea
            className="input"
            rows={3}
            value={m.identity_card}
            onChange={(e) => setM({ ...m, identity_card: e.target.value })}
            placeholder="Карта личности по-английски: кто, форма, цвета hex, одежда, деталь — дословно в каждый промпт позы"
          />
          <textarea
            className="input"
            rows={2}
            value={m.persona}
            onChange={(e) => setM({ ...m, persona: e.target.value })}
            placeholder="Характер для сценариста: как говорит, на «ты», с юмором…"
          />
          <input className="input" value={m.style_passport} onChange={(e) => setM({ ...m, style_passport: e.target.value })} placeholder="Стиль: Flat 2D vector cartoon…" />
          <div className="series-row expl-colors">
            {(
              [
                ["bg_color", "фон"],
                ["accent_color", "акцент"],
                ["chip_color", "чипы"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="expl-color">
                <input type="color" value={m[key]} onChange={(e) => setM({ ...m, [key]: e.target.value })} />
                <span className="muted">{label}</span>
              </label>
            ))}
            <select className="input" value={m.music_track_id} onChange={(e) => setM({ ...m, music_track_id: Number(e.target.value) })} title="Музыка под голосом">
              <option value={0}>без музыки</option>
              {(tracks.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div className="series-row">
            <TtsVoicePicker value={voice} showModel onChange={(next) => setVoice({ ...voice, ...next })} />
          </div>
          <span className="muted">Голос: Gemini — бесплатно; Polza (ElevenLabs) ≈ 3 ₽ за 45 с — в карточке ролика.</span>
        </div>
      </div>
    </section>
  );
}

function NewMascot({ o, empty }: { o: ExplainerOverview; empty: boolean }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [open, setOpen] = useState(empty);
  const [form, setForm] = useState({ name: "", identity_card: "", persona: "", bg_color: "#2a1b47" });
  const add = useMutation({
    mutationFn: () => explApi.addMascot(o.project.id, { ...form, voice: { provider: "gemini", voice: "Puck" } }),
    onSuccess: () => {
      toast.success("Маскот создан — загрузите или нарисуйте лист");
      setForm({ name: "", identity_card: "", persona: "", bg_color: "#2a1b47" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не создано")),
  });
  const pilot = useMutation({
    mutationFn: () => explApi.importPilot(o.project.id),
    onSuccess: (r) => {
      toast.success(`Пилот «Ух»: добавлено поз ${r.added.length}`);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не импортировано")),
  });
  const pilotButton = o.pilot_dir_ready && !o.mascots.some((m) => m.slug === "uh") ? (
    <button className="btn sm" disabled={pilot.isPending} onClick={() => pilot.mutate()}>
      Импортировать пилот «Ух» (18 поз)
    </button>
  ) : null;
  if (!open) {
    return (
      <div className="series-row">
        <button className="btn ghost sm" onClick={() => setOpen(true)}>
          ＋ Новый маскот
        </button>
        {pilotButton}
      </div>
    );
  }
  return (
    <section className="panel series-panel">
      {empty ? (
        <EmptyState icon="🦉" title="Маскота пока нет" hint="Создайте своего или возьмите сову «Ух» из пилота (клип 887)." actions={pilotButton} />
      ) : null}
      <h3>Новый маскот</h3>
      <div className="series-row">
        <input className="input" placeholder="Имя" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <label className="expl-color">
          <input type="color" value={form.bg_color} onChange={(e) => setForm({ ...form, bg_color: e.target.value })} />
          <span className="muted">фон</span>
        </label>
      </div>
      <textarea
        className="input"
        rows={2}
        placeholder="Карта личности по-английски: a chubby round midnight-blue owl with lavender belly, huge yellow eyes, grey hoodie…"
        value={form.identity_card}
        onChange={(e) => setForm({ ...form, identity_card: e.target.value })}
      />
      <textarea className="input" rows={2} placeholder="Характер для сценариста" value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} />
      <div className="series-row">
        <button className="btn primary sm" disabled={!form.name || !form.identity_card || add.isPending} onClick={() => add.mutate()}>
          Создать
        </button>
        {!empty ? (
          <button className="btn ghost sm" onClick={() => setOpen(false)}>
            Скрыть
          </button>
        ) : null}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Библиотека поз
// --------------------------------------------------------------------------- //

function PoseLibrary({ mascot, projectId }: { mascot: Mascot; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [filter, setFilter] = useState("");
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", tags: "" });
  const [file, setFile] = useState<File | null>(null);
  const add = useMutation({
    mutationFn: async () => {
      if (file) return explApi.uploadPose(mascot.id, file, form);
      const tags = form.tags.split(",").map((t) => t.trim()).filter(Boolean);
      return explApi.addPose(mascot.id, { description: form.description, title: form.title, tags, generate: true });
    },
    onSuccess: () => {
      toast.success(file ? "Поза добавлена" : "Поза добавлена — карточка на картинку ушла владельцу");
      setForm({ title: "", description: "", tags: "" });
      setFile(null);
      setAdding(false);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  const words = filter.trim().toLowerCase();
  const shown = words
    ? mascot.poses.filter((p) => [p.title, p.description, ...p.tags].join(" ").toLowerCase().includes(words))
    : mascot.poses;
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Библиотека поз · {mascot.poses.length}</h3>
        <div className="series-row expl-lib-tools">
          <input className="input expl-filter" placeholder="Найти по тегам…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          {/* «Добавить позу» было внизу, под всеми карточками, — теперь рядом с поиском. */}
          <button className="btn sm" onClick={() => setAdding(!adding)} aria-expanded={adding}>
            {adding ? "Скрыть" : "＋ Добавить позу"}
          </button>
        </div>
      </div>
      {adding ? (
        <div className="expl-add">
          <div className="series-row">
            <input className="input" placeholder="Название: «пьёт кофе»" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            <input className="input wide" placeholder="Теги через запятую: кофе, coffee, кружка" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} />
          </div>
          <textarea
            className="input"
            rows={2}
            placeholder="Поза по-английски: happily sipping from a huge steaming coffee mug held in both wings"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
          <div className="series-row">
            <label className="btn ghost sm">
              {file ? `Файл: ${file.name}` : "⬆ Своя картинка (иначе нарисовать)"}
              <input type="file" accept="image/*" hidden onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <button className="btn sm primary" disabled={!form.description.trim() || add.isPending} onClick={() => add.mutate()}>
              {file ? "Добавить" : "Добавить и нарисовать (≈ 3 ₽)"}
            </button>
          </div>
        </div>
      ) : null}
      {shown.length ? (
        <div className="expl-poses" style={{ ["--expl-bg" as string]: mascot.bg_color }}>
          {shown.map((p) => (
            <PoseCard key={p.id} p={p} projectId={projectId} />
          ))}
        </div>
      ) : (
        <EmptyState icon="🧩" title="Поз нет" hint="Они появятся сами с первым роликом (по карточке) — или добавьте свои." />
      )}
    </section>
  );
}

function PoseCard({ p, projectId }: { p: Pose; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [edit, setEdit] = useState(false);
  const [tags, setTags] = useState(p.tags.join(", "));
  const save = useMutation({
    mutationFn: () => explApi.editPose(p.id, { tags: tags.split(",").map((t) => t.trim()).filter(Boolean) }),
    onSuccess: () => {
      setEdit(false);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  const regen = useMutation({
    mutationFn: () => explApi.regeneratePose(p.id),
    onSuccess: (r) => {
      toast.success(r.status === "awaiting_approval" ? "Карточка на новую версию ушла владельцу" : "Новая версия готова");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const del = useMutation({
    mutationFn: () => explApi.deletePose(p.id),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(errText(e, "Не удалено")),
  });
  return (
    <figure className="expl-pose" title={p.description}>
      <div className="expl-pose-img">
        {p.url ? <img src={p.url} alt={p.title} loading="lazy" /> : <span>нарисуется в ролике</span>}
        {p.number_shown ? <span className="expl-badge">#{p.number_shown}</span> : null}
        {p.uses ? <span className="expl-uses">×{p.uses}</span> : null}
      </div>
      <figcaption>
        <b>{p.title || p.slug}</b>
        {edit ? (
          <span className="series-row">
            <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
            <button className="btn sm" disabled={save.isPending} onClick={() => save.mutate()}>
              ✓
            </button>
          </span>
        ) : (
          <span className="expl-tags" onClick={() => setEdit(true)} title="Править теги">
            {p.tags.slice(0, 6).join(" · ") || "без тегов"}
          </span>
        )}
        <span className="expl-pose-actions">
          <button className="btn ghost sm" disabled={regen.isPending} onClick={() => regen.mutate()} title="Новая версия (≈ 3 ₽ по карточке)" aria-label="Перерисовать позу">
            ↻
          </button>
          <button className="btn ghost sm" disabled={del.isPending} onClick={() => window.confirm(`Убрать позу «${p.title}»?`) && del.mutate()} title="Убрать из библиотеки" aria-label="Убрать позу">
            ✕
          </button>
        </span>
      </figcaption>
    </figure>
  );
}
