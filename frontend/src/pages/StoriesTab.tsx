import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CAMERA_LABEL, aivApi } from "@/api/aivideo";
import { audioTracksApi } from "@/api/assets";
import { ApiError } from "@/api/client";
import {
  EPISODE_ACTIVE,
  STORY_DURATIONS,
  STORY_FORMATS,
  STORY_TONES,
  storiesApi,
  storiesKey,
  storyEpisodeKey,
  type StoriesOverview,
  type StoryCharacter,
  type StoryEpisode,
  type StoryEngine,
  type StoryRenderer,
  type StoryFormat,
  type StoryLocation,
  type StoryScene,
  type StoryWorld,
} from "@/api/stories";
import { useToast } from "@/components/Toast";
import { TtsVoicePicker, type TtsVoiceValue } from "@/components/TtsVoicePicker";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import "@/styles/aivideo.css";
import "@/styles/explainer.css";
import "@/styles/stories.css";

/**
 * «Истории» (app/stories*.py): рассказчик за кадром и новая сцена на каждую фразу. Мир (стиль, голос,
 * субтитры, музыка), актёры с листами и локации с плитами (переиспользуются во всех эпизодах),
 * библиотека сцен, новый эпизод (завязка, длина, тон), ход конвейера, ОДНА карточка с суммой на эпизод
 * и ссылка на клип в редакторе.
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const rub = (v: number | null | undefined) => `${(v ?? 0).toFixed(v && v < 10 ? 2 : 0).replace(".", ",")} ₽`;
const STATUS_TONE: Record<string, string> = { done: "ok", failed: "bad", cancelled: "bad", awaiting_approval: "warn" };
const genToast = (r: { status: string }) =>
  r.status === "awaiting_approval" ? "Карточка ушла владельцу" : r.status === "nothing" ? "Уже есть" : "Готово";

function useInvalidate(projectId: number) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: storiesKey(projectId) });
    qc.invalidateQueries({ queryKey: ["stories", "episode"] });
  };
}

export function StoriesTab({ projectId }: { projectId: number }) {
  const [picked, setPicked] = useState<number | null>(null);
  const q = useQuery({
    queryKey: storiesKey(projectId),
    queryFn: () => storiesApi.overview(projectId),
    refetchInterval: (query) => {
      const o = query.state.data as StoriesOverview | undefined;
      const busy = o?.episodes.some((e) => EPISODE_ACTIVE.includes(e.status)) || o?.approvals.some((a) => a.status === "pending");
      return busy ? 4000 : false;
    },
  });
  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const o = q.data;
  const world = o.worlds.find((w) => w.id === picked) ?? o.worlds[0];
  return (
    <div className="aiv expl story">
      <ApprovalBanners o={o} />
      {o.provider === "none" ? (
        <div className="panel expl-note">
          Проект в <b>сухом прогоне</b> (провайдер none): сцены, листы и плиты — заглушки, озвучка — тон, 0 ₽. Настоящие
          картинки — провайдер Polza во вкладке «ИИ-видео».
        </div>
      ) : null}
      {o.worlds.length > 1 ? (
        <nav className="pipeline aiv-subtabs">
          {o.worlds.map((w) => (
            <button key={w.id} className={`tab${world?.id === w.id ? " active" : ""}`} onClick={() => setPicked(w.id)}>
              {w.name}
            </button>
          ))}
        </nav>
      ) : null}
      {world ? (
        <>
          <NewEpisode o={o} world={world} />
          <Episodes o={o} world={world} />
          <CastAndPlaces world={world} projectId={o.project.id} />
          <SceneLibrary world={world} projectId={o.project.id} />
          <WorldCard key={world.id} world={world} projectId={o.project.id} />
        </>
      ) : null}
      <NewWorld o={o} empty={!o.worlds.length} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Карточки владельцу
// --------------------------------------------------------------------------- //

function ApprovalBanners({ o }: { o: StoriesOverview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "reject" }) => aivApi.decide(id, action),
    onSuccess: (_d, v) => {
      toast.success(v.action === "approve" ? "Разрешено — сцены рисуются, эпизод соберётся сам" : "Отказано");
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
              ✅ Разрешить {rub(c.amount_rub)}
            </button>
            <button className="btn ghost sm" disabled={decide.isPending} onClick={() => decide.mutate({ id: c.id, action: "reject" })}>
              🗑 Отказ
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Новый эпизод и эпизоды
// --------------------------------------------------------------------------- //

function NewEpisode({ o, world }: { o: StoriesOverview; world: StoryWorld }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const initial = (fmt: StoryFormat) => ({
    premise: "",
    format: fmt,
    duration: STORY_DURATIONS[fmt][2],
    tone: fmt === "disaster" ? "напряжённо" : STORY_TONES[0],
    title_card: fmt === "disaster",
    render: false,
    engine: "images" as StoryEngine,
    renderer: "three" as StoryRenderer,
  });
  const [form, setForm] = useState(() => initial(world.format ?? "story"));
  const [lo, hi] = STORY_DURATIONS[form.format];
  const disaster = form.format === "disaster";
  // Как на сервере (stories_disaster.next_part): наибольший номер части мира + 1, отменённые не считаются.
  const nextPart =
    Math.max(0, ...o.episodes.filter((e) => e.world_id === world.id && e.options?.format === "disaster" && e.status !== "cancelled").map((e) => e.options.part ?? 0)) + 1;
  const create = useMutation({
    mutationFn: () => storiesApi.createEpisode(world.id, form),
    onSuccess: () => {
      toast.success("Эпизод в работе: сценарий → сцены → (карточка, если нужны новые картинки) → клип");
      setForm({ ...form, premise: "" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не создано")),
  });
  const ready = world.scenes.filter((s) => s.ready).length;
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Новая история · {world.name}</h3>
        <span className="muted">
          сцен в библиотеке {ready} — готовые бесплатны, новая ≈ 3–4,5 ₽; потолок эпизода {rub(o.episode_cap_rub)}
        </span>
      </div>
      <div className="series-row" role="radiogroup" aria-label="Жанр">
        {STORY_FORMATS.map((f) => (
          <button
            key={f.id}
            role="radio"
            aria-checked={form.format === f.id}
            className={`btn sm ${form.format === f.id ? "primary" : "ghost"}`}
            title={f.hint}
            onClick={() => setForm({ ...initial(f.id), premise: form.premise })}
          >
            {f.label}
          </button>
        ))}
        {disaster ? <span className="chip story-part">часть {nextPart}</span> : null}
        <label
          className="aiv-check"
          title="Свой 3D-движок: лоу-поли герой-игрушка по карточке актёра, кадр на каждую фразу озвучки, свет и звуки на словах. Без картинок сцен и без карточки — 0 ₽; рендер — минуты."
        >
          <input
            type="checkbox"
            checked={form.engine === "3d"}
            onChange={(e) => setForm({ ...form, engine: e.target.checked ? "3d" : "images" })}
          />{" "}
          🧊 3D-серия (движок, 0 ₽)
        </label>
        {form.engine === "3d" ? (
          <label
            className="aiv-check"
            title="Кино-качество: герой KayKit с мимикой и лип-синком, режиссура действий по фразам (Gemini), рендер Blender на компьютере владельца — сервис ставит задание в очередь, воркер tools/render_worker.py рендерит (≈ 1–2 с на кадр). Компьютер выключен — серия ждёт."
          >
            <input
              type="checkbox"
              checked={form.renderer === "blender"}
              onChange={(e) => setForm({ ...form, renderer: e.target.checked ? "blender" : "three" })}
            />{" "}
            🎬 Blender (рендер на компьютере)
          </label>
        ) : null}
      </div>
      <textarea
        className="input"
        rows={2}
        placeholder={
          disaster
            ? "Катастрофа и место: «блэкаут застал парня на 36 этаже, соседи стучат по перилам» — сценарий по формуле «Если … застала вас …»"
            : "Завязка: «заключённый год копает ложкой подкоп, а дверь камеры всё это время была открыта»"
        }
        value={form.premise}
        onChange={(e) => setForm({ ...form, premise: e.target.value })}
      />
      <div className="series-row">
        <label className="expl-field">
          <span className="muted">Длина: {form.duration} с</span>
          <input type="range" min={lo} max={hi} step={5} value={form.duration} onChange={(e) => setForm({ ...form, duration: Number(e.target.value) })} />
        </label>
        <select className="input" value={form.tone} onChange={(e) => setForm({ ...form, tone: e.target.value })} title="Тон рассказчика">
          {STORY_TONES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <label className="aiv-check" title="Название эпизода крупно в первые 2 секунды">
          <input type="checkbox" checked={form.title_card} onChange={(e) => setForm({ ...form, title_card: e.target.checked })} /> название в начале
        </label>
        <label className="aiv-check" title="После сборки: рендер, обложки, заголовок и описание">
          <input type="checkbox" checked={form.render} onChange={(e) => setForm({ ...form, render: e.target.checked })} /> сразу рендер
        </label>
        <button className="btn primary sm" disabled={form.premise.trim().length < 3 || create.isPending} onClick={() => create.mutate()}>
          ✨ Сделать историю
        </button>
      </div>
      <span className="muted">
        {form.engine === "3d"
          ? "3D-серия: лифт или квартира в лоу-поли, героя узнают по яркой вещи из карточки актёра; кадры, свет (блэкаут, мигание), тряска и звуки — по словам озвучки. Картинки сцен не рисуются. "
          : ""}
        {disaster
          ? "Серия — часть сериала мира: хук на «вы» до 12 с, цифры на экране как в игре (опасность красным, ресурсы жёлтым), в конце «ЧАСТЬ N+1 — …». Музыку не вшиваем — поставь её в TikTok тихо."
          : "Первый эпизод с новым составом ≈ 25–30 картинок (≈ 80–130 ₽, одна карточка); следующие дешевле — сцены, актёры и локации переиспользуются."}
      </span>
    </section>
  );
}

function Episodes({ o, world }: { o: StoriesOverview; world: StoryWorld }) {
  const episodes = o.episodes.filter((e) => e.world_id === world.id);
  if (!episodes.length) return null;
  return (
    <section className="panel series-panel">
      <h3>Эпизоды</h3>
      <div className="expl-videos">
        {episodes.map((e) => (
          <EpisodeRow key={e.id} e={e} projectId={o.project.id} world={world} />
        ))}
      </div>
    </section>
  );
}

function EpisodeRow({ e, projectId, world }: { e: StoryEpisode; projectId: number; world: StoryWorld }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [open, setOpen] = useState(false);
  const detail = useQuery({ queryKey: storyEpisodeKey(e.id), queryFn: () => storiesApi.episode(e.id), enabled: open });
  const act = useMutation({
    mutationFn: (what: "continue" | "cancel" | "delete" | "reassemble") =>
      what === "continue"
        ? storiesApi.continueEpisode(e.id)
        : what === "reassemble"
          ? storiesApi.continueEpisode(e.id, true)
          : what === "cancel"
            ? storiesApi.cancelEpisode(e.id)
            : storiesApi.deleteEpisode(e.id),
    onSuccess: () => invalidate(),
    onError: (err) => toast.error(errText(err, "Не удалось")),
  });
  const active = EPISODE_ACTIVE.includes(e.status);
  const d = detail.data;
  const beats = d?.timeline?.beats?.length ? d.timeline.beats : d?.beats ?? [];
  const byId = new Map(world.scenes.map((s) => [s.id, s]));
  return (
    <div className="expl-video">
      <div className="expl-video-head">
        <span className={`chip expl-status ${STATUS_TONE[e.status] ?? ""}`}>
          {active ? <span className="spinner sm" /> : null} {e.status_label}
        </span>
        <button className="link-btn expl-topic" onClick={() => setOpen(!open)} title="Сценарий и кадры">
          {e.title || e.premise}
        </button>
        {e.options?.format === "disaster" ? <span className="chip story-part">часть {e.options.part ?? 1}</span> : null}
        {e.options?.engine === "3d" ? <span className="chip">{e.options?.renderer === "blender" ? "3D · Blender" : "3D"}</span> : null}
        <span className="muted">
          {e.duration_target.toFixed(0)} с · {e.tone}
        </span>
        {e.estimate_rub > 0 ? <span className="muted">≈ {rub(e.estimate_rub)}</span> : null}
        <span className="expl-video-actions">
          {e.clip_id && e.editor_url ? (
            <Link className="btn sm primary" to={e.editor_url}>
              Открыть клип #{e.clip_id}
            </Link>
          ) : null}
          {e.status === "failed" || e.status === "cancelled" ? (
            <button className="btn sm" disabled={act.isPending} onClick={() => act.mutate("continue")}>
              🔁 Продолжить
            </button>
          ) : null}
          {e.status === "done" ? (
            <button className="btn ghost sm" disabled={act.isPending} onClick={() => act.mutate("reassemble")} title="Новый клип из тех же битов">
              Пересобрать
            </button>
          ) : null}
          {active ? (
            <button className="btn ghost sm" disabled={act.isPending} onClick={() => act.mutate("cancel")}>
              Отменить
            </button>
          ) : (
            <button className="btn ghost sm" disabled={act.isPending} onClick={() => window.confirm("Удалить эпизод? Клип останется.") && act.mutate("delete")}>
              🗑
            </button>
          )}
        </span>
      </div>
      {e.render3d && e.status === "assembling" ? (
        <div className="muted" title={e.render3d.worker ? `воркер: ${e.render3d.worker}` : undefined}>
          <progress max={1} value={e.render3d.progress} /> {e.render3d.note || e.render3d.label}
        </div>
      ) : null}
      {e.error ? <div className={e.status === "failed" ? "aiv-warn" : "muted"}>{e.error}</div> : null}
      {open ? (
        detail.isLoading ? (
          <Loading />
        ) : d ? (
          <div className="expl-detail">
            {d.estimate ? (
              <div className="aiv-est">
                <span className="chip">кадров {d.estimate.beats}</span>
                <span className="chip">сцен из библиотеки: {d.estimate.reused_scenes}</span>
                <span className="chip">
                  новых картинок: {d.estimate.images} ≈ {rub(d.estimate.images_rub)}
                </span>
                {d.estimate.sheets || d.estimate.plates ? (
                  <span className="chip">
                    из них листов {d.estimate.sheets}, плит {d.estimate.plates}
                  </span>
                ) : null}
                <span className="chip">озвучка ≈ {rub(d.estimate.narration_rub)}</span>
                {d.readiness?.held.length ? <span className="aiv-warn">{d.readiness.held[0]}</span> : null}
              </div>
            ) : null}
            {d.timeline ? (
              <div className="aiv-est">
                <span className="chip">{d.timeline.duration.toFixed(1)} с</span>
                <span className="chip">{d.timeline.shots_per_min} кадров/мин</span>
                <span className="chip">звуков {d.timeline.sfx}</span>
                {d.timeline.part_card ? <span className="chip">{d.timeline.part_card}</span> : null}
              </div>
            ) : null}
            {d.timeline?.hud?.length ? (
              <div className="aiv-est story-hud-list" title="Цифры на экране (HUD)">
                {d.timeline.hud.map((h, i) => (
                  <span key={i} className="story-hud" style={{ color: h.color }}>
                    {h.text} <span className="muted mono">{h.start.toFixed(1)} с</span>
                  </span>
                ))}
              </div>
            ) : null}
            {d.timeline?.publish ? (
              <p className="muted" title="Описание к публикации">
                {d.timeline.publish.description} {d.timeline.publish.hashtags.join(" ")}
              </p>
            ) : null}
            {d.script?.text ? <p className="expl-script">{d.script.text}</p> : null}
            {d.script?.warnings?.length ? <div className="aiv-warn">{d.script.warnings.join("; ")}</div> : null}
            {beats.length ? (
              <div className="expl-beats">
                {beats.map((b, n) => {
                  const scene = byId.get(b.scene_id);
                  return (
                    <div key={`${b.i}-${n}`} className="expl-beat">
                      <div className="expl-beat-img">
                        {scene?.url ? <img src={scene.url} alt="" loading="lazy" /> : <span>нарисуется</span>}
                        {b.sfx ? <span className="story-sfx" title={b.sfx.label}>🔊</span> : null}
                      </div>
                      <div className="expl-beat-text">
                        {b.start !== undefined ? <span className="mono muted">{b.start.toFixed(1)} с</span> : null}
                        <span>{b.text}</span>
                        <span className="muted">
                          {CAMERA_LABEL[b.camera ?? ""] ?? b.camera}
                          {b.scene_new ? " · новая" : ""}
                          {b.variant === "alt" ? " · та же сцена" : ""}
                          {b.sfx ? ` · ${b.sfx.label}` : ""}
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
// Актёры и локации
// --------------------------------------------------------------------------- //

function CastAndPlaces({ world, projectId }: { world: StoryWorld; projectId: number }) {
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Актёры · {world.characters.length}</h3>
        <span className="muted">лист — референс в каждой сцене: персонаж узнаваем из эпизода в эпизод</span>
      </div>
      <div className="story-cards">
        {world.characters.map((c) => (
          <CharacterCard key={c.id} c={c} projectId={projectId} />
        ))}
      </div>
      <AddEntity kind="character" worldId={world.id} projectId={projectId} />
      <div className="series-panel-head">
        <h3>Локации · {world.locations.length}</h3>
        <span className="muted">плита — фон без людей, референс сцен в этом месте</span>
      </div>
      <div className="story-cards">
        {world.locations.map((l) => (
          <LocationCard key={l.id} l={l} projectId={projectId} />
        ))}
      </div>
      <AddEntity kind="location" worldId={world.id} projectId={projectId} />
      {!world.characters.length && !world.locations.length ? (
        <span className="muted">Можно не заводить заранее: сценарист сам предложит актёров и локации, их листы и плиты войдут в карточку эпизода.</span>
      ) : null}
    </section>
  );
}

function CharacterCard({ c, projectId }: { c: StoryCharacter; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const up = useMutation({ mutationFn: (f: File) => storiesApi.uploadSheet(c.id, f), onSuccess: invalidate, onError: (e) => toast.error(errText(e, "Не загружено")) });
  const gen = useMutation({
    mutationFn: () => storiesApi.generateSheet(c.id, !!c.sheet),
    onSuccess: (r) => {
      toast.success(genToast(r));
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const del = useMutation({ mutationFn: () => storiesApi.deleteCharacter(c.id), onSuccess: invalidate, onError: (e) => toast.error(errText(e, "Не удалено")) });
  return (
    <figure className="story-card" title={c.identity_card}>
      <div className="story-card-img wide">{c.sheet ? <img src={c.sheet.url} alt={c.name} loading="lazy" /> : <span>листа нет — нарисуется в эпизоде</span>}</div>
      <figcaption>
        <b>{c.name}</b>
        <span className="expl-tags">{c.persona || c.identity_card}</span>
        <span className="expl-pose-actions">
          <label className="link-btn" title="Свой лист">
            ⬆
            <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && up.mutate(e.target.files[0])} />
          </label>
          <button className="link-btn" disabled={gen.isPending} onClick={() => gen.mutate()} title="Нарисовать лист (≈ 3 ₽ по карточке)">
            {c.sheet ? "↻" : "🎨"}
          </button>
          <button className="link-btn" disabled={del.isPending} onClick={() => window.confirm(`Убрать актёра «${c.name}»?`) && del.mutate()} title="Убрать">
            🗑
          </button>
        </span>
      </figcaption>
    </figure>
  );
}

function LocationCard({ l, projectId }: { l: StoryLocation; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const up = useMutation({ mutationFn: (f: File) => storiesApi.uploadPlate(l.id, f), onSuccess: invalidate, onError: (e) => toast.error(errText(e, "Не загружено")) });
  const gen = useMutation({
    mutationFn: () => storiesApi.generatePlate(l.id, !!l.plate),
    onSuccess: (r) => {
      toast.success(genToast(r));
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const del = useMutation({ mutationFn: () => storiesApi.deleteLocation(l.id), onSuccess: invalidate, onError: (e) => toast.error(errText(e, "Не удалено")) });
  return (
    <figure className="story-card" title={l.description}>
      <div className="story-card-img">{l.plate ? <img src={l.plate.url} alt={l.name} loading="lazy" /> : <span>плиты нет — нарисуется в эпизоде</span>}</div>
      <figcaption>
        <b>{l.name}</b>
        <span className="expl-tags">{l.description}</span>
        <span className="expl-pose-actions">
          <label className="link-btn" title="Своя плита">
            ⬆
            <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && up.mutate(e.target.files[0])} />
          </label>
          <button className="link-btn" disabled={gen.isPending} onClick={() => gen.mutate()} title="Нарисовать плиту (≈ 3 ₽ по карточке)">
            {l.plate ? "↻" : "🎨"}
          </button>
          <button className="link-btn" disabled={del.isPending} onClick={() => window.confirm(`Убрать локацию «${l.name}»?`) && del.mutate()} title="Убрать">
            🗑
          </button>
        </span>
      </figcaption>
    </figure>
  );
}

function AddEntity({ kind, worldId, projectId }: { kind: "character" | "location"; worldId: number; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [form, setForm] = useState({ name: "", text: "", persona: "" });
  const add = useMutation({
    mutationFn: async (): Promise<unknown> =>
      kind === "character"
        ? storiesApi.addCharacter(worldId, { name: form.name, identity_card: form.text, persona: form.persona })
        : storiesApi.addLocation(worldId, { name: form.name, description: form.text }),
    onSuccess: () => {
      toast.success(kind === "character" ? "Актёр добавлен — лист нарисуется в первом эпизоде" : "Локация добавлена");
      setForm({ name: "", text: "", persona: "" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  return (
    <details className="expl-add">
      <summary>＋ {kind === "character" ? "Добавить актёра" : "Добавить локацию"}</summary>
      <div className="series-row">
        <input className="input" placeholder={kind === "character" ? "Имя: «Боб»" : "Название: «Тюремная камера»"} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        {kind === "character" ? (
          <input className="input wide" placeholder="Характер для сценариста" value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} />
        ) : null}
      </div>
      <textarea
        className="input"
        rows={2}
        placeholder={
          kind === "character"
            ? "По-английски: a small round-headed toy prisoner, huge round beige head, dot eyes, orange jumpsuit #ff8a1f"
            : "По-английски: a cosy tiny prison cell, bunk bed, brick wall, small barred window, warm lamp"
        }
        value={form.text}
        onChange={(e) => setForm({ ...form, text: e.target.value })}
      />
      <div className="series-row">
        <button className="btn sm primary" disabled={!form.name.trim() || !form.text.trim() || add.isPending} onClick={() => add.mutate()}>
          Добавить
        </button>
      </div>
    </details>
  );
}

// --------------------------------------------------------------------------- //
// Библиотека сцен
// --------------------------------------------------------------------------- //

function SceneLibrary({ world, projectId }: { world: StoryWorld; projectId: number }) {
  const [filter, setFilter] = useState("");
  const locName = new Map(world.locations.map((l) => [l.id, l.name]));
  const charName = new Map(world.characters.map((c) => [c.id, c.name]));
  const words = filter.trim().toLowerCase();
  const shown = words
    ? world.scenes.filter((s) =>
        [s.title, s.action, s.accent, ...s.tags, locName.get(s.location_id ?? 0) ?? "", ...s.cast.map((c) => charName.get(c) ?? "")]
          .join(" ")
          .toLowerCase()
          .includes(words),
      )
    : world.scenes;
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Библиотека сцен · {world.scenes.length}</h3>
        <input className="input expl-filter" placeholder="Найти: место, актёр, тег…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      {shown.length ? (
        <div className="expl-poses">
          {shown.map((s) => (
            <SceneCard key={s.id} s={s} projectId={projectId} where={locName.get(s.location_id ?? 0)} who={s.cast.map((c) => charName.get(c) ?? `#${c}`)} />
          ))}
        </div>
      ) : (
        <EmptyState icon="🎬" title="Сцен нет" hint="Они появятся с первым эпизодом (по карточке) и дальше будут переиспользоваться." />
      )}
    </section>
  );
}

function SceneCard({ s, projectId, where, who }: { s: StoryScene; projectId: number; where?: string; who: string[] }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const regen = useMutation({
    mutationFn: () => storiesApi.regenerateScene(s.id),
    onSuccess: (r) => {
      toast.success(genToast(r));
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const del = useMutation({ mutationFn: () => storiesApi.deleteScene(s.id), onSuccess: invalidate, onError: (e) => toast.error(errText(e, "Не удалено")) });
  return (
    <figure className="expl-pose" title={`${s.action}${s.accent ? ` · ${s.accent}` : ""}`}>
      <div className="expl-pose-img">
        {s.url ? <img src={s.url} alt={s.title} loading="lazy" /> : <span>нарисуется в эпизоде</span>}
        {s.uses ? <span className="expl-uses">×{s.uses}</span> : null}
        {s.stale ? <span className="expl-badge" title="Поменялся стиль, актёр или локация">устар.</span> : null}
      </div>
      <figcaption>
        <b>{s.title || s.slug}</b>
        <span className="expl-tags">
          {[where, who.join(", ")].filter(Boolean).join(" · ") || "без места"}
          {s.tags.length ? ` · ${s.tags.slice(0, 4).join(" · ")}` : ""}
        </span>
        <span className="expl-pose-actions">
          <button className="link-btn" disabled={regen.isPending} onClick={() => regen.mutate()} title="Новая версия (≈ 3 ₽ по карточке)">
            ↻
          </button>
          <button className="link-btn" disabled={del.isPending} onClick={() => window.confirm(`Убрать сцену «${s.title}»?`) && del.mutate()} title="Убрать из библиотеки">
            🗑
          </button>
        </span>
      </figcaption>
    </figure>
  );
}

// --------------------------------------------------------------------------- //
// Мир: стиль, рассказчик, голос, музыка, субтитры
// --------------------------------------------------------------------------- //

function WorldCard({ world, projectId }: { world: StoryWorld; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const tracks = useQuery({ queryKey: ["audio-tracks"], queryFn: audioTracksApi.list, staleTime: 60_000 });
  const [w, setW] = useState({
    name: world.name,
    style_label: world.style_label,
    style_passport: world.style_passport,
    narrator: world.narrator,
    music_track_id: world.music_track_id ?? 0,
    format: world.format ?? "story",
    music: world.music ?? "",
  });
  const [caps, setCaps] = useState(world.captions);
  const [voice, setVoice] = useState<TtsVoiceValue>({ ...world.voice, voice: world.voice.voice ?? "", model: world.voice.model ?? "" });
  const save = useMutation({
    mutationFn: () => storiesApi.editWorld(world.id, { ...w, voice, captions: caps }),
    onSuccess: () => {
      toast.success("Мир сохранён");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  const styleUp = useMutation({ mutationFn: (f: File) => storiesApi.uploadStyleRef(world.id, f), onSuccess: invalidate, onError: (e) => toast.error(errText(e, "Не загружено")) });
  const styleGen = useMutation({
    mutationFn: () => storiesApi.generateStyle(world.id, world.style_refs.length > 0),
    onSuccess: (r) => {
      toast.success(genToast(r));
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>
          Мир «{world.name}» <span className="muted mono">{world.slug}</span>
        </h3>
      </div>
      <div className="expl-mascot">
        <div className="expl-sheet story-style">
          {world.style_refs.length ? (
            <div className="story-style-refs">
              {world.style_refs.map((r) => (
                <img key={r.ref_id} src={r.url} alt="референс стиля" />
              ))}
            </div>
          ) : (
            <span className="muted">Референсов стиля нет — кадр стиля нарисуется в карточке первого эпизода.</span>
          )}
          <div className="series-row">
            <label className="btn ghost sm">
              ⬆ Свой референс
              <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && styleUp.mutate(e.target.files[0])} />
            </label>
            <button className="btn ghost sm" disabled={styleGen.isPending} onClick={() => styleGen.mutate()} title="≈ 3 ₽ по карточке">
              {world.style_refs.length ? "↻ Кадр стиля" : "🎨 Кадр стиля"}
            </button>
          </div>
        </div>
        <div className="expl-mascot-form">
          <input className="input" value={w.name} onChange={(e) => setW({ ...w, name: e.target.value })} placeholder="Название мира" />
          <input className="input" value={w.style_label} onChange={(e) => setW({ ...w, style_label: e.target.value })} placeholder="Стиль по-русски: 3D-игрушки…" />
          <textarea
            className="input"
            rows={3}
            value={w.style_passport}
            onChange={(e) => setW({ ...w, style_passport: e.target.value })}
            placeholder="Стиль-паспорт по-английски — дословно в каждый промпт сцены"
          />
          <textarea className="input" rows={2} value={w.narrator} onChange={(e) => setW({ ...w, narrator: e.target.value })} placeholder="Рассказчик: как говорит, с какой иронией…" />
          <div className="series-row expl-colors">
            {(
              [
                ["color", "слово"],
                ["active", "звучащее"],
                ["stroke", "обводка"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="expl-color">
                <input type="color" value={caps[key]} onChange={(e) => setCaps({ ...caps, [key]: e.target.value })} />
                <span className="muted">{label}</span>
              </label>
            ))}
            <label className="expl-field" title="Высота субтитров (нижняя треть ≈ 74%)">
              <span className="muted">Субтитры: {Math.round(caps.y * 100)}%</span>
              <input type="range" min={0.6} max={0.84} step={0.01} value={caps.y} onChange={(e) => setCaps({ ...caps, y: Number(e.target.value) })} />
            </label>
            <select className="input" value={w.format} onChange={(e) => setW({ ...w, format: e.target.value as StoryFormat })} title="Жанр новых эпизодов">
              {STORY_FORMATS.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </select>
            <select
              className="input"
              value={w.music}
              onChange={(e) => setW({ ...w, music: e.target.value as "" | "auto" | "none" })}
              title="Вшивать ли музыку (сериал-катастрофа — по умолчанию нет: музыку ставят в TikTok из библиотеки)"
            >
              <option value="">музыка: как в жанре</option>
              <option value="none">музыку не вшивать</option>
              <option value="auto">музыку вшивать</option>
            </select>
            <select className="input" value={w.music_track_id} onChange={(e) => setW({ ...w, music_track_id: Number(e.target.value) })} title="Музыка под голосом">
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
            <button className="btn sm primary" disabled={save.isPending} onClick={() => save.mutate()}>
              Сохранить
            </button>
          </div>
          <span className="muted">Голос: Gemini — бесплатно; Polza (ElevenLabs) ≈ 3–4 ₽ за 50 с — в карточке эпизода.</span>
        </div>
      </div>
    </section>
  );
}

function NewWorld({ o, empty }: { o: StoriesOverview; empty: boolean }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [open, setOpen] = useState(empty);
  const [form, setForm] = useState<{ name: string; narrator: string; format: StoryFormat }>({ name: "", narrator: "", format: "story" });
  const add = useMutation({
    mutationFn: () => storiesApi.addWorld(o.project.id, { ...form, voice: { provider: "gemini" } }),
    onSuccess: () => {
      toast.success("Мир создан — стиль «3D-игрушки» по умолчанию, поменять можно в карточке мира");
      setForm({ name: "", narrator: "", format: "story" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не создано")),
  });
  if (!open) {
    return (
      <div className="series-row">
        <button className="btn ghost sm" onClick={() => setOpen(true)}>
          ＋ Новый мир историй
        </button>
      </div>
    );
  }
  return (
    <section className="panel series-panel">
      {empty ? <EmptyState icon="🧸" title="Миров пока нет" hint="Мир — стиль, рассказчик и голос; в нём живут актёры, локации и библиотека сцен." /> : null}
      <h3>Новый мир</h3>
      <input className="input" placeholder="Название: «Игрушечная тюрьма»" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      <select className="input" value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value as StoryFormat })} title="Жанр мира">
        {STORY_FORMATS.map((f) => (
          <option key={f.id} value={f.id}>
            {f.label} — {f.hint}
          </option>
        ))}
      </select>
      <textarea
        className="input"
        rows={2}
        placeholder={form.format === "disaster" ? "Рассказчик: пусто — спокойный напряжённый голос, хук на «вы», дальше 3-е лицо" : "Рассказчик: ироничный, на «ты», любит абсурд"}
        value={form.narrator}
        onChange={(e) => setForm({ ...form, narrator: e.target.value })}
      />
      <div className="series-row">
        <button className="btn primary sm" disabled={!form.name.trim() || add.isPending} onClick={() => add.mutate()}>
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
