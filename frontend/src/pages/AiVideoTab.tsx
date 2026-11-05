import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  CAMERA_LABEL,
  CAMERA_MOVES,
  STAGE_LABEL,
  VIDEO_MODELS,
  aivApi,
  aivKey,
  aivScriptKey,
  type AivCharacter,
  type AivLocation,
  type AivRef,
  type Budget,
  type Estimate,
  type Overview,
  type Script,
  type Shot,
  type Stage,
  type StageCard,
} from "@/api/aivideo";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { TtsVoicePicker, type TtsVoiceValue } from "@/components/TtsVoicePicker";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import "@/styles/aivideo.css";

/**
 * Проект «ИИ-видео» (app/aivideo*.py): Сценарий / Персонажи / Локации / Блокинг / Раскадровка / Сборка.
 * Платное — по этапам: «Оценить» (бесплатно) → «Запросить» → карточка с суммой (тут же сверху,
 * в «Одобрении» и Telegram) → «Разрешить» → генерация. Сухой прогон (провайдер none) — сразу, 0 ₽.
 */

type Sub = "script" | "characters" | "locations" | "blocking" | "board" | "assemble";
const SUBS: [Sub, string][] = [
  ["script", "Сценарий"],
  ["characters", "Персонажи"],
  ["locations", "Локации"],
  ["blocking", "Блокинг"],
  ["board", "Раскадровка"],
  ["assemble", "Сборка"],
];

// Подписи версий по-русски (раньше — служебные turnaround / expressions / keyframe).
const KIND_LABEL: Record<string, string> = {
  upload: "владелец",
  turnaround: "разворот",
  expressions: "эмоции",
  look: "облик",
  style: "стиль",
  plate: "плита",
  keyframe: "старт",
  endframe: "конец",
  shot_video: "видео",
  blocking_start: "блокинг: старт",
  blocking_end: "блокинг: конец",
};
// Откуда раскладка блокинга (app/aivideo_blocking.py: source).
const BLOCKING_SOURCE: Record<string, string> = { auto: "авто по состоянию", gemini: "Gemini", agent: "агент", owner: "вручную" };

/** Место и состав плана по-человечески: имена вместо служебных slug (kitchen · mira → Кухня ночью · Мира). */
function placeOf(o: Overview, shot: Shot): string {
  return o.locations.find((l) => l.slug === shot.location)?.name ?? (shot.location || "—");
}
function castOf(o: Overview, shot: Shot): string {
  return shot.cast
    .map((c) => {
      const ch = o.characters.find((x) => x.slug === c.id);
      const look = c.look ? ch?.looks.find((l) => l.slug === c.look)?.name ?? c.look : "";
      return (ch?.name ?? c.id) + (look ? ` (${look})` : "");
    })
    .join(", ");
}

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const rub = (v: number | null | undefined) => `${(v ?? 0).toFixed(v && v < 10 ? 2 : 0).replace(".", ",")} ₽`;

function useInvalidate(projectId: number) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: aivKey(projectId) });
    qc.invalidateQueries({ queryKey: ["ai-video", "script"] });
  };
}

export function AiVideoTab({ projectId }: { projectId: number }) {
  // Подвкладка — в адресе (?sub=board), как вкладка серии: перезагрузка не сбрасывает на «Сценарий».
  const [params, setParams] = useSearchParams();
  const raw = params.get("sub");
  const sub: Sub = SUBS.some(([key]) => key === raw) ? (raw as Sub) : "script";
  const setSub = (next: Sub) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set("sub", next);
        return p;
      },
      { replace: true },
    );
  const q = useQuery({
    queryKey: aivKey(projectId),
    queryFn: () => aivApi.overview(projectId),
    // Пока что-то генерируется или ждёт решения — освежаем.
    refetchInterval: (query) => {
      const jobs = (query.state.data as Overview | undefined)?.jobs ?? {};
      return (jobs.approved ?? 0) + (jobs.polling ?? 0) + (jobs.submitting ?? 0) + (jobs.recovering ?? 0) + (jobs.awaiting ?? 0) > 0
        ? 4000
        : false;
    },
  });
  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const o = q.data;
  return (
    <div className="aiv">
      <BudgetBar budget={o.budget} />
      <StageCards projectId={projectId} cards={o.approvals} />
      <nav className="pipeline aiv-subtabs">
        {SUBS.map(([key, label]) => (
          <button key={key} className={`tab${sub === key ? " active" : ""}`} onClick={() => setSub(key)}>
            {label}
          </button>
        ))}
      </nav>
      {sub === "script" ? <ScriptSub o={o} /> : null}
      {sub === "characters" ? <CharactersSub o={o} /> : null}
      {sub === "locations" ? <LocationsSub o={o} /> : null}
      {sub === "blocking" ? <BlockingSub o={o} /> : null}
      {sub === "board" ? <BoardSub o={o} /> : null}
      {sub === "assemble" ? <AssembleSub o={o} /> : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Деньги: полоса бюджета и карточки этапов
// --------------------------------------------------------------------------- //

function Meter({ label, spent, reserved, cap }: { label: string; spent: number; reserved: number; cap: number }) {
  const pct = (v: number) => `${Math.min(100, cap > 0 ? (v / cap) * 100 : 0)}%`;
  return (
    <div className="aiv-meter">
      <div className="aiv-meter-label">
        <span>{label}</span>
        <span className="muted">
          {rub(spent)} {reserved > 0 ? `+ ${rub(reserved)} одобрено/ждёт` : ""} из {rub(cap)}
        </span>
      </div>
      <div className="aiv-meter-bar">
        <span className="aiv-meter-spent" style={{ width: pct(spent) }} />
        <span className="aiv-meter-res" style={{ width: pct(reserved), left: pct(spent) }} />
      </div>
    </div>
  );
}

function BudgetBar({ budget, episode }: { budget: Budget; episode?: boolean }) {
  // В серии — только полоса серии: месяц проекта и цены уже наверху вкладки (раньше шли дважды).
  if (episode) {
    return budget.episode ? (
      <Meter
        label={`Серия (${budget.episode.quality === "quality" ? "качество" : "черновик"})`}
        spent={budget.episode.spent_rub}
        reserved={budget.episode.reserved_rub}
        cap={budget.episode.cap_rub}
      />
    ) : null;
  }
  return (
    <section className="panel aiv-budget">
      <div className="series-row">
        <span className={`chip${budget.paid ? "" : " active"}`}>
          {budget.paid ? "Polza — платно" : "сухой прогон — 0 ₽"}
        </span>
        {budget.paid && !budget.enabled ? (
          <span className="aiv-warn">AIVIDEO_ENABLED=0: одобренное не запустится, пока владелец не включит</span>
        ) : null}
        <span className="muted">цены от {budget.price_checked}: замер — как есть; без замера: видео ×1,25, картинки ×2 к прайсу</span>
      </div>
      <Meter label="Месяц проекта" spent={budget.month.spent_rub} reserved={budget.month.reserved_rub} cap={budget.month.cap_rub} />
    </section>
  );
}

function StageCards({ projectId, cards }: { projectId: number; cards: StageCard[] }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "reject" }) => aivApi.decide(id, action),
    onSuccess: (_d, v) => {
      toast.success(v.action === "approve" ? "Разрешено — запуски переданы работнику" : "Отказано");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const run = useMutation({
    mutationFn: (id: number) => aivApi.run(id),
    onSuccess: () => {
      toast.success("Запуски продолжены");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const retry = useMutation({
    mutationFn: (id: number) => aivApi.retryCard(id),
    onSuccess: (r) => {
      toast.success(`Повтор: ${r.retried.length} шт. (та же карточка)`);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const pending = cards.filter((c) => c.status === "pending");
  const held = cards.filter((c) => c.status === "approved" && (c.jobs.held ?? 0) > 0);
  const flaky = cards.filter((c) => c.status === "approved" && (c.retryable ?? 0) > 0);
  if (!pending.length && !held.length && !flaky.length) return null;
  return (
    <div className="aiv-cards">
      {pending.map((c) => (
        <div key={c.id} className="panel aiv-card">
          <div className="aiv-card-text">
            <b>
              {STAGE_LABEL[c.stage]}: {c.count} шт. ≈ {rub(c.amount_rub)}
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
      {held.map((c) => (
        <div key={c.id} className="panel aiv-card">
          <div className="aiv-card-text">
            <b>
              {STAGE_LABEL[c.stage]}: остановлено ({c.jobs.held} шт.), потрачено {rub(c.spent_rub)} из {rub(c.amount_rub)}
            </b>
            <span className="muted">Упёрлись в потолок или генерация выключена — см. «Сборка → журнал».</span>
          </div>
          <button className="btn sm" disabled={run.isPending} onClick={() => run.mutate(c.id)}>
            🔁 Продолжить
          </button>
        </div>
      ))}
      {flaky.map((c) => (
        <div key={`r${c.id}`} className="panel aiv-card">
          <div className="aiv-card-text">
            <b>
              {STAGE_LABEL[c.stage]}: сбой сети ({c.retryable} шт.) — не оплачено
            </b>
            <span className="muted">Запрос не ушёл или ответ не дошёл, а баланс не изменился. Повтор — в пределах этой карточки.</span>
          </div>
          <button className="btn sm" disabled={retry.isPending} onClick={() => retry.mutate(c.id)}>
            🔁 Повторить
          </button>
        </div>
      ))}
    </div>
  );
}

/** «Оценить» (бесплатно) → «Запросить» (карточка владельцу или сразу — сухой прогон). */
function StageButton({
  projectId,
  stage,
  scriptId,
  items,
  label,
}: {
  projectId: number;
  stage: Stage;
  scriptId?: number;
  items?: string[];
  label?: string;
}) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [est, setEst] = useState<Estimate | null>(null);
  const estimate = useMutation({
    mutationFn: () => aivApi.estimate(projectId, stage, scriptId, items),
    onSuccess: setEst,
    onError: (e) => toast.error(errText(e, "Не удалось оценить")),
  });
  const request = useMutation({
    mutationFn: () => aivApi.request(projectId, stage, scriptId, items),
    onSuccess: (r) => {
      setEst(null);
      toast.success(r.status === "awaiting_approval" ? `Карточка на ${rub(r.estimate.total_rub)} — ждёт «Разрешить»` : r.note);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не запрошено")),
  });
  return (
    <div className="aiv-stage">
      <button className="btn sm" disabled={estimate.isPending} onClick={() => estimate.mutate()}>
        {label ?? `${STAGE_LABEL[stage]}: оценить`}
      </button>
      {est ? (
        <div className="aiv-est">
          <span>
            {est.count ? `${est.count} шт. ≈ ${rub(est.total_rub)}` : "всё уже есть"} {est.paid ? "" : "(сухой прогон)"}
          </span>
          {est.problems.map((p) => (
            <span key={p} className="aiv-warn">
              {p}
            </span>
          ))}
          {(est.notes ?? []).map((n) => (
            <span key={n} className="muted">
              {n}
            </span>
          ))}
          {(est.continuity ?? [])
            .filter((f) => !f.ok && !f.outdated)
            .map((f) => (
              <span key={`c${f.idx}`} className="aiv-warn">
                Склейка плана {f.idx}: {f.mismatches.map((m) => m.detail).join("; ") || `оценка ${f.score}`}
              </span>
            ))}
          {est.count ? (
            <button className="btn primary sm" disabled={request.isPending} onClick={() => request.mutate()}>
              {est.paid ? `Запросить одобрение ≈ ${rub(est.total_rub)}` : "Сделать (0 ₽)"}
            </button>
          ) : null}
          <button className="btn ghost sm" onClick={() => setEst(null)} title="Скрыть оценку">
            Скрыть
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Versions({ refs, projectId, onPick }: { refs: AivRef[]; projectId: number; onPick?: () => void }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const select = useMutation({
    mutationFn: (id: number) => aivApi.select(id),
    onSuccess: () => {
      invalidate();
      onPick?.();
    },
  });
  const regen = useMutation({
    mutationFn: (id: number) => aivApi.regenerate(id),
    onSuccess: (r) => {
      toast.success(r.status === "awaiting_approval" ? `Перегенерация ≈ ${rub(r.estimate.total_rub)} — ждёт «Разрешить»` : "Новая версия готова");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не запрошено")),
  });
  if (!refs.length) return <span className="muted">нет картинок</span>;
  return (
    <div className="aiv-versions">
      {refs.map((r) => (
        <figure key={r.id} className={`aiv-thumb${r.selected ? " sel" : ""}${r.stale ? " stale" : ""}`}>
          {r.url ? (
            <a href={r.url} target="_blank" rel="noreferrer">
              {r.kind === "shot_video" ? (
                <video src={r.url} muted playsInline preload="metadata" />
              ) : (
                <img src={r.url} alt={`${r.kind} v${r.version}`} loading="lazy" />
              )}
            </a>
          ) : null}
          <figcaption>
            <span>
              {KIND_LABEL[r.kind] ?? r.kind} v{r.version}
              {r.stale ? " · устарело" : ""}
              {r.score && "min" in r.score && r.score.min != null ? ` · ${r.score.min}/10` : ""}
              {r.score && "kind" in r.score && r.score.kind === "video_qa" && r.score.score != null
                ? ` · ${r.score.ok ? "ок" : "проверь"} ${r.score.score}/10`
                : ""}
            </span>
            {!r.selected ? (
              <button className="btn ghost sm" onClick={() => select.mutate(r.id)} title="Выбрать эту версию">
                ✓
              </button>
            ) : null}
            {r.kind !== "upload" ? (
              <button className="btn ghost sm" disabled={regen.isPending} onClick={() => regen.mutate(r.id)} title="Перегенерировать (своя оценка и карточка)">
                ↻
              </button>
            ) : null}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Сценарий: стиль-библия и серии
// --------------------------------------------------------------------------- //

function StylePanel({ o }: { o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const s = o.style;
  const [draft, setDraft] = useState({
    style_kind: s.style_kind,
    passport_text: s.passport_text,
    palette: s.palette.join(" "),
    quality: s.quality,
    provider: s.provider,
    image_model: s.image_model,
    video_model: s.video_model_set === false ? "" : s.video_model,
    video_resolution: s.video_resolution,
    month_cap_rub: s.month_cap_rub == null ? "" : String(s.month_cap_rub),
    captions_style: s.captions_style,
  });
  const save = useMutation({
    mutationFn: () =>
      aivApi.updateStyle(o.project.id, {
        ...draft,
        palette: draft.palette.split(/[\s,]+/).filter(Boolean),
        month_cap_rub: draft.month_cap_rub === "" ? null : Number(draft.month_cap_rub),
      } as never),
    onSuccess: () => {
      toast.success("Стиль сохранён");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Стиль-библия</h3>
        <button className="btn sm" disabled={save.isPending} onClick={() => save.mutate()}>
          Сохранить
        </button>
      </div>
      <div className="series-row">
        <select className="input" value={draft.style_kind} onChange={(e) => setDraft({ ...draft, style_kind: e.target.value as never })}>
          <option value="anime">Аниме / 2D-toon</option>
          <option value="photoreal">Фотореализм</option>
        </select>
        <select className="input" value={draft.quality} onChange={(e) => setDraft({ ...draft, quality: e.target.value as never })}>
          <option value="draft">Черновик (1K, серия ≤ 350 ₽)</option>
          <option value="quality">Качество (2K, серия ≤ 2 500 ₽)</option>
        </select>
        <select className="input" value={draft.provider} onChange={(e) => setDraft({ ...draft, provider: e.target.value as never })}>
          <option value="none">Сухой прогон (0 ₽)</option>
          <option value="polza">Polza (платно, по карточкам)</option>
        </select>
        <select className="input" value={draft.image_model} onChange={(e) => setDraft({ ...draft, image_model: e.target.value })}>
          {["google/gemini-nano-banana-2.1", "bytedance/seedream-4.5", "openai/gpt-image-2.5-flare", "qwen/image-2.1", "black-forest-labs/flux.2-pro"].map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <select className="input" value={draft.video_model} onChange={(e) => setDraft({ ...draft, video_model: e.target.value })} title="Модель видео планов (этап «Видео»)">
          {VIDEO_MODELS.map(([m, label]) => (
            <option key={m} value={m}>
              {label}
            </option>
          ))}
        </select>
        <select className="input" value={draft.video_resolution} onChange={(e) => setDraft({ ...draft, video_resolution: e.target.value as never })}>
          <option value="720p">видео 720p (H3 — 768p)</option>
          <option value="1080p">видео 1080p (Seedance 2.0)</option>
          <option value="480p">видео 480p</option>
        </select>
      </div>
      <label className="field">
        <span>Стиль-паспорт (дословно в каждый промпт)</span>
        <textarea className="input" rows={2} value={draft.passport_text} onChange={(e) => setDraft({ ...draft, passport_text: e.target.value })} />
      </label>
      <div className="series-row">
        <label className="field wide">
          <span>Палитра (hex через пробел)</span>
          <input className="input mono" value={draft.palette} onChange={(e) => setDraft({ ...draft, palette: e.target.value })} />
        </label>
        <label className="field">
          <span>Потолок месяца, ₽ (пусто — общий)</span>
          <input className="input" type="number" min={0} value={draft.month_cap_rub} onChange={(e) => setDraft({ ...draft, month_cap_rub: e.target.value })} />
        </label>
        <label className="field">
          <span>Стиль субтитров</span>
          <input className="input" value={draft.captions_style} onChange={(e) => setDraft({ ...draft, captions_style: e.target.value })} />
        </label>
      </div>
      {o.style_refs.length ? <Versions refs={o.style_refs} projectId={o.project.id} /> : null}
    </section>
  );
}

function useEpisode(o: Overview) {
  const [id, setId] = useState<number | null>(o.scripts[0]?.id ?? null);
  useEffect(() => {
    if (id == null && o.scripts.length) setId(o.scripts[0].id);
  }, [o.scripts, id]);
  const q = useQuery({ queryKey: aivScriptKey(id ?? 0), queryFn: () => aivApi.script(id as number), enabled: id != null });
  return { id, setId, script: q.data, q };
}

function EpisodePicker({ o, id, setId }: { o: Overview; id: number | null; setId: (v: number) => void }) {
  if (!o.scripts.length) return <span className="muted">Серий пока нет — создайте на вкладке «Сценарий».</span>;
  return (
    <select className="input" value={id ?? ""} onChange={(e) => setId(Number(e.target.value))}>
      {o.scripts.map((s) => (
        <option key={s.id} value={s.id}>
          Серия {s.episode_no}: {s.title} ({s.shots_count ?? 0} планов)
        </option>
      ))}
    </select>
  );
}

function ScriptSub({ o }: { o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const ep = useEpisode(o);
  const [brief, setBrief] = useState("");
  const [target, setTarget] = useState(30);
  useEffect(() => setBrief(ep.script?.brief ?? ""), [ep.script?.id, ep.script?.brief]);
  const create = useMutation({
    mutationFn: () => aivApi.addScript(o.project.id, { brief, duration_target: target }),
    onSuccess: (s) => {
      ep.setId(s.id);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не создано")),
  });
  const write = useMutation({
    mutationFn: () => aivApi.writeScript(ep.id as number, brief),
    onSuccess: (s) => {
      toast.success(`Сценарий: ${s.shots?.length ?? 0} планов`);
      (s.warnings ?? []).forEach((w) => toast.error(w));
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Gemini не ответил — напишите сценарий агентом (aiv_update_script)")),
  });
  return (
    <div className="series-grid">
      <div className="series-col">
        <StylePanel o={o} />
      </div>
      <div className="series-col">
        <section className="panel series-panel">
          <div className="series-panel-head">
            <h3>Серия</h3>
            <EpisodePicker o={o} id={ep.id} setId={ep.setId} />
          </div>
          <label className="field">
            <span>О чём серия (бриф для сценария)</span>
            <textarea className="input" rows={3} value={brief} onChange={(e) => setBrief(e.target.value)} />
          </label>
          <div className="series-row">
            <label className="field">
              <span>Длина, с</span>
              <input className="input" type="number" min={10} max={120} value={target} onChange={(e) => setTarget(Number(e.target.value))} />
            </label>
            <button className="btn sm" disabled={create.isPending} onClick={() => create.mutate()}>
              ＋ Новая серия
            </button>
            <button className="btn primary sm" disabled={!ep.id || write.isPending} onClick={() => write.mutate()}>
              {write.isPending ? "Gemini пишет…" : "✨ Сценарий (Gemini, бесплатно)"}
            </button>
          </div>
          {ep.script ? (
            <p className="muted">
              {ep.script.logline} · планов {ep.script.shots?.length ?? 0} · {ep.script.duration_total} с из {ep.script.duration_target} с
            </p>
          ) : null}
        </section>
        {ep.script?.shots?.map((s) => <ShotEditor key={`${s.id}-${ep.script?.id}`} shot={s} o={o} />)}
      </div>
    </div>
  );
}

function ShotEditor({ shot, o }: { shot: Shot; o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [d, setD] = useState({
    duration: shot.duration,
    move: shot.camera.move,
    location: shot.location,
    cast: shot.cast.map((c) => (c.look ? `${c.id}:${c.look}` : c.id)).join(", "),
    action: shot.action,
    dialogue: shot.dialogue.map((l) => `${l.who}: ${l.text}`).join("\n"),
    needs_motion: shot.needs_motion,
    continues_from_prev: shot.continues_from_prev,
    motion_prompt: shot.motion_prompt ?? "",
    sound: shot.sound ?? "",
    state: JSON.stringify(shot.state ?? {}),
    end_state: JSON.stringify(shot.end_state ?? {}),
  });
  const save = useMutation({
    mutationFn: () => {
      let state: unknown;
      let endState: unknown;
      try {
        state = d.state.trim() ? JSON.parse(d.state) : {};
        endState = d.end_state.trim() ? JSON.parse(d.end_state) : {};
      } catch {
        throw new Error("state / end_state — JSON: {\"characters\": [{\"id\", \"pose\", \"facing\", \"holding\", \"clothing\"}], \"props\": [{\"name\", \"state\", \"count\"}]}");
      }
      return aivApi.editShot(shot.id, {
        needs_motion: d.needs_motion,
        continues_from_prev: d.continues_from_prev,
        motion_prompt: d.motion_prompt,
        sound: d.sound,
        state,
        end_state: endState,
        duration: d.duration,
        camera: { ...shot.camera, move: d.move },
        location: d.location,
        characters: d.cast
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean)
          .map((x) => {
            const [id, look] = x.split(":");
            return { id, look: look ?? "" };
          }),
        action: d.action,
        dialogue: d.dialogue
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => {
            const at = line.indexOf(":");
            return at > 0 ? { who: line.slice(0, at).trim(), text: line.slice(at + 1).trim() } : { who: "narrator", text: line };
          }),
      });
    },
    onSuccess: (s) => {
      toast.success(`План ${shot.idx} сохранён`);
      (s.warnings ?? []).forEach((w: string) => toast.error(w));
      invalidate();
    },
    onError: (e) => toast.error(e instanceof Error && !(e instanceof ApiError) ? e.message : errText(e, "Не сохранено")),
  });
  return (
    <section className="panel series-panel aiv-shot-edit">
      <div className="series-panel-head">
        <b>План {shot.idx}</b>
        <button className="btn sm" disabled={save.isPending} onClick={() => save.mutate()}>
          Сохранить
        </button>
      </div>
      <div className="series-row">
        <input className="input aiv-num" type="number" min={2} max={8} step={0.5} value={d.duration} onChange={(e) => setD({ ...d, duration: Number(e.target.value) })} />
        <select className="input" value={d.move} onChange={(e) => setD({ ...d, move: e.target.value })}>
          {CAMERA_MOVES.map((m) => (
            <option key={m} value={m}>
              {CAMERA_LABEL[m]}
            </option>
          ))}
        </select>
        <select className="input" value={d.location} onChange={(e) => setD({ ...d, location: e.target.value })}>
          <option value="">без локации</option>
          {o.locations.map((l) => (
            <option key={l.id} value={l.slug}>
              {l.name}
            </option>
          ))}
        </select>
        <input className="input wide" placeholder="персонажи: mira:pajamas, kot" value={d.cast} onChange={(e) => setD({ ...d, cast: e.target.value })} />
      </div>
      <input className="input" placeholder="действие в кадре" value={d.action} onChange={(e) => setD({ ...d, action: e.target.value })} />
      <textarea className="input" rows={2} placeholder="кто: реплика (по строке)" value={d.dialogue} onChange={(e) => setD({ ...d, dialogue: e.target.value })} />
      <div className="series-row">
        <label className="aiv-check">
          <input type="checkbox" checked={d.needs_motion} onChange={(e) => setD({ ...d, needs_motion: e.target.checked })} /> видео (оживить план)
        </label>
        <label className="aiv-check" title="Та же сцена: старт плана = конечный кадр прошлого плана (один общий кадр)">
          <input
            type="checkbox"
            checked={d.continues_from_prev}
            disabled={shot.idx === 1}
            onChange={(e) => setD({ ...d, continues_from_prev: e.target.checked })}
          />{" "}
          продолжает прошлый план
        </label>
      </div>
      {d.needs_motion ? (
        <div className="series-row">
          <input className="input wide" placeholder="движение: как двигаются тело и камера" value={d.motion_prompt} onChange={(e) => setD({ ...d, motion_prompt: e.target.value })} />
          <input className="input wide" placeholder="звук: фон и шумы действия (без музыки и речи)" value={d.sound} onChange={(e) => setD({ ...d, sound: e.target.value })} />
        </div>
      ) : null}
      {/* Состояния — JSON для постоянства кадров; обычно их пишет сценарист (Gemini/агент) — свёрнуты. */}
      <details className="aiv-more">
        <summary>Состояние кадра (JSON){d.needs_motion ? ": начало и конец" : ": начало"}</summary>
        <label className="field">
          <span>Начало плана: поза, взгляд, руки, одежда; реквизит со счётом count</span>
          <textarea className="input mono" rows={2} value={d.state} onChange={(e) => setD({ ...d, state: e.target.value })} />
        </label>
        {d.needs_motion ? (
          <label className="field">
            <span>Конец плана — результат одного действия (конечный кадр; у продолжения — его старт)</span>
            <textarea className="input mono" rows={2} value={d.end_state} onChange={(e) => setD({ ...d, end_state: e.target.value })} />
          </label>
        ) : null}
      </details>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Персонажи
// --------------------------------------------------------------------------- //

function CharactersSub({ o }: { o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [form, setForm] = useState({ name: "", identity_card: "", origin: "invented" });
  const add = useMutation({
    mutationFn: () => aivApi.addCharacter(o.project.id, form as never),
    onSuccess: () => {
      setForm({ name: "", identity_card: "", origin: "invented" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  return (
    <div className="series-col">
      <section className="panel series-panel">
        <div className="series-panel-head">
          <h3>Новый персонаж</h3>
          <StageButton projectId={o.project.id} stage="refs" label="Листы и референсы: оценить" />
        </div>
        <div className="series-row">
          <input className="input" placeholder="Имя" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select className="input" value={form.origin} onChange={(e) => setForm({ ...form, origin: e.target.value })}>
            <option value="invented">придуманный</option>
            <option value="owner">мой витубер/стример (свои картинки)</option>
          </select>
          <button className="btn sm" disabled={!form.name || !form.identity_card || add.isPending} onClick={() => add.mutate()}>
            ＋ Добавить
          </button>
        </div>
        <textarea
          className="input"
          rows={2}
          placeholder="Карта личности: возраст, волосы, глаза, рост и пропорции, 2–3 цвета hex, отличительная деталь"
          value={form.identity_card}
          onChange={(e) => setForm({ ...form, identity_card: e.target.value })}
        />
      </section>
      {o.characters.length ? (
        o.characters.map((c) => <CharacterCard key={c.id} c={c} projectId={o.project.id} />)
      ) : (
        <EmptyState title="Персонажей нет" hint="Добавьте или дайте сценаристу завести их." />
      )}
    </div>
  );
}

function CharacterCard({ c, projectId }: { c: AivCharacter; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [card, setCard] = useState(c.identity_card);
  const [physics, setPhysics] = useState({ constraints: c.constraints ?? "", stand_in: c.stand_in ?? "" });
  const [voice, setVoice] = useState<TtsVoiceValue & { sfx?: string }>({
    ...c.voice,
    model: c.voice.model ?? "",
    voice: c.voice.voice ?? "",
    style: c.voice.style ?? "",
  });
  const [speech, setSpeech] = useState({ speech: c.speech ?? "voice", speech_captions: c.speech_captions ?? true });
  const [look, setLook] = useState({ name: "", outfit_text: "", beat: "" });
  const save = useMutation({
    mutationFn: () => aivApi.editCharacter(c.id, { identity_card: card, voice, ...speech, ...physics } as never),
    onSuccess: () => {
      toast.success("Сохранено (зависимые кадры — «устарело»)");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  const addLook = useMutation({
    mutationFn: () => aivApi.addLook(c.id, look),
    onSuccess: () => {
      setLook({ name: "", outfit_text: "", beat: "" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  const upload = useMutation({
    mutationFn: (f: File) => aivApi.uploadCharacterRef(c.id, f),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(errText(e, "Не загружено")),
  });
  const items = [`character:${c.id}`, ...c.looks.map((l) => `look:${l.id}`)];
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>
          {c.name} <span className="muted mono">{c.slug}</span> {c.origin === "owner" ? <span className="chip">свой</span> : null}
        </h3>
        <div className="series-row">
          <StageButton projectId={projectId} stage="refs" items={items} label="Листы персонажа: оценить" />
          {/* «Сохранить» — в шапке карточки, как у плана: сохраняет всё (карта, физика, речь, голос). */}
          <button className="btn sm" disabled={save.isPending} onClick={() => save.mutate()}>
            Сохранить
          </button>
        </div>
      </div>
      <textarea className="input" rows={2} value={card} onChange={(e) => setCard(e.target.value)} />
      <div className="series-row">
        <input
          className="input wide"
          placeholder="физика: «плоский диск на полу, без рук и ног, ровно один» — дословно в кадры, видео и проверку"
          value={physics.constraints}
          onChange={(e) => setPhysics({ ...physics, constraints: e.target.value })}
        />
        <select className="input" value={physics.stand_in} title="Манекен в блокинге" onChange={(e) => setPhysics({ ...physics, stand_in: e.target.value })}>
          <option value="">манекен: по карте</option>
          <option value="human">человек (капсула)</option>
          <option value="disc">плоский диск (пылесос)</option>
          <option value="quadruped">зверь на четырёх лапах</option>
          <option value="small">маленький (шар)</option>
        </select>
      </div>
      <div className="series-row">
        <select
          className="input"
          value={speech.speech}
          title="Как персонаж «говорит» в сценарии и озвучке"
          onChange={(e) => setSpeech({ ...speech, speech: e.target.value as AivCharacter["speech"] })}
        >
          <option value="voice">говорит голосом</option>
          <option value="sfx">не говорит — звуки (писк по эмоции)</option>
          <option value="none">молчит</option>
        </select>
        {speech.speech === "sfx" ? (
          <label className="aiv-check" title="Показывать текст его реплик («Бип-бип?..») субтитрами">
            <input type="checkbox" checked={speech.speech_captions} onChange={(e) => setSpeech({ ...speech, speech_captions: e.target.checked })} />{" "}
            подписи к звукам
          </label>
        ) : null}
      </div>
      {speech.speech === "voice" ? (
        <div className="series-row">
          <TtsVoicePicker value={voice} emptyLabel="TTS: Polza (по умолчанию, платно)" showModel onChange={(next) => setVoice({ ...voice, ...next })} />
        </div>
      ) : null}
      <div className="series-section-head">
        <b>Листы и референсы</b>
        <label className="btn ghost sm">
          ⬆ Своя картинка
          <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
        </label>
      </div>
      <Versions refs={c.refs} projectId={projectId} />
      <div className="series-section">
        <b>Облики по сюжету</b>
        {c.looks.map((l) => (
          <div key={l.id} className="aiv-look">
            <span>
              <b>{l.name}</b> <span className="muted">{l.slug}</span> — {l.outfit_text} {l.beat ? <i className="muted">({l.beat})</i> : null}
            </span>
            <Versions refs={l.refs} projectId={projectId} />
          </div>
        ))}
        <div className="series-row">
          <input className="input" placeholder="Облик" value={look.name} onChange={(e) => setLook({ ...look, name: e.target.value })} />
          <input className="input wide" placeholder="одежда/состояние" value={look.outfit_text} onChange={(e) => setLook({ ...look, outfit_text: e.target.value })} />
          <input className="input" placeholder="где в сюжете" value={look.beat} onChange={(e) => setLook({ ...look, beat: e.target.value })} />
          <button className="btn sm" disabled={!look.name || !look.outfit_text || addLook.isPending} onClick={() => addLook.mutate()}>
            ＋ Облик
          </button>
        </div>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Локации
// --------------------------------------------------------------------------- //

function LocationsSub({ o }: { o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [form, setForm] = useState({ name: "", description: "" });
  const add = useMutation({
    mutationFn: () => aivApi.addLocation(o.project.id, form),
    onSuccess: () => {
      setForm({ name: "", description: "" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  return (
    <div className="series-col">
      <section className="panel series-panel">
        <div className="series-row">
          <input className="input" placeholder="Локация" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input wide" placeholder="описание: что, время суток, свет" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <button className="btn sm" disabled={!form.name || add.isPending} onClick={() => add.mutate()}>
            ＋ Добавить
          </button>
          <StageButton projectId={o.project.id} stage="refs" items={o.locations.map((l) => `location:${l.id}`)} label="Плиты локаций: оценить" />
        </div>
      </section>
      {o.locations.map((l) => (
        <LocationCard key={l.id} l={l} projectId={o.project.id} />
      ))}
    </div>
  );
}

function LocationCard({ l, projectId }: { l: AivLocation; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [desc, setDesc] = useState(l.description);
  const save = useMutation({
    mutationFn: () => aivApi.editLocation(l.id, { description: desc }),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>
          {l.name} <span className="muted mono">{l.slug}</span>
        </h3>
        <button className="btn sm" disabled={desc === l.description || save.isPending} onClick={() => save.mutate()}>
          Сохранить
        </button>
      </div>
      <textarea className="input" rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} />
      <Versions refs={l.refs} projectId={projectId} />
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Раскадровка
// --------------------------------------------------------------------------- //

function Frame({ ref_, label, empty, badge }: { ref_?: AivRef | null; label: string; empty: string; badge?: string }) {
  return (
    <figure className="aiv-frame">
      <div className="aiv-shot-img">
        {ref_?.url ? <img src={ref_.url} alt={label} loading="lazy" /> : <span className="muted">{empty}</span>}
        {ref_?.stale ? <span className="aiv-badge">устарело</span> : null}
        {badge ? <span className="aiv-badge review">{badge}</span> : null}
      </div>
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function BoardSub({ o }: { o: Overview }) {
  const ep = useEpisode(o);
  const toast = useToast();
  const check = useMutation({
    mutationFn: (refId: number) => aivApi.check(refId),
    onSuccess: (r) => toast.success(r.note),
    onError: (e) => toast.error(errText(e, "Проверка не удалась")),
  });
  const invalidate = useInvalidate(o.project.id);
  const make = useMutation({
    mutationFn: (target: string) => aivApi.regenerateTarget(target),
    onSuccess: (r) => {
      toast.success(r.status === "awaiting_approval" ? `≈ ${rub(r.estimate.total_rub)} — ждёт «Разрешить»` : "Готово");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не запрошено")),
  });
  const continuity = useMutation({
    mutationFn: (scriptId: number) => aivApi.checkContinuity(scriptId),
    onSuccess: (r) => {
      (r.flagged?.length ? toast.error : toast.success)(r.note);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Проверка кадров не удалась")),
  });
  const checkVideo = useMutation({
    mutationFn: (refId: number) => aivApi.checkVideo(refId),
    onSuccess: (r) => {
      (r.needs_review ? toast.error : toast.success)(r.note);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Проверка видео не удалась")),
  });
  const s = ep.script;
  return (
    <div className="series-col">
      <section className="panel series-panel">
        <div className="series-row">
          <EpisodePicker o={o} id={ep.id} setId={ep.setId} />
        </div>
        {/* Этапы серии по порядку конвейера: блокинг → пары кадров (+ проверка) → видео. */}
        {s ? (
          <div className="series-row aiv-steps">
            <StageButton projectId={o.project.id} stage="blocking" scriptId={s.id} label="1. Блокинг (0 ₽): оценить" />
            <StageButton projectId={o.project.id} stage="keyframes" scriptId={s.id} label="2. Пары кадров: оценить" />
            <button className="btn ghost sm" disabled={continuity.isPending} onClick={() => continuity.mutate(s.id)}
                    title="Gemini сравнит склейки (старт плана с концом прошлого) и пары кадров (старт → конец) — бесплатно">
              {continuity.isPending ? "Сравниваю…" : "Проверить кадры"}
            </button>
            <StageButton projectId={o.project.id} stage="video" scriptId={s.id} label="3. Видео планов: оценить" />
          </div>
        ) : null}
        <span className="muted">
          Видео: {o.style.video_caps?.label ?? o.style.video_model}. План с видео — пара кадров (старт → конец), модель
          только переводит один в другой; конец плана — он же старт следующего, если тот продолжает сцену.
        </span>
        {s?.budget ? <BudgetBar budget={s.budget} episode /> : null}
        {s?.video_review?.length ? (
          <span className="aiv-warn">
            Видео с проблемами (Gemini): {s.video_review.map((r) => `план ${r.idx}`).join(", ")} — перегенерация кнопкой «↻ Видео» у плана
            (своя карточка).
          </span>
        ) : null}
      </section>
      {s ? (
        <div className="aiv-board">
          {s.shots?.map((shot) => {
            const start = shot.start_frame ?? shot.keyframes?.find((k) => k.selected) ?? null;
            const end = shot.end_frame ?? shot.endframes?.find((k) => k.selected) ?? null;
            const vid = shot.videos?.find((v) => v.selected) ?? shot.videos?.[shot.videos.length - 1];
            const cont = shot.continuity;
            const qa = shot.video_qa;
            const shared = shot.start_shared_from;
            return (
              <article key={shot.id} className="panel aiv-shot aiv-shot-wide">
                <div className="aiv-frames">
                  <Frame ref_={start} label={shared ? `старт = конец плана ${shared}` : "старт"}
                         empty={shared ? `ждёт конец плана ${shared}` : "кадра нет"}
                         badge={shot.needs_review ? "проверь: не похож" : undefined} />
                  {shot.needs_motion ? <Frame ref_={end} label="конец" empty="конца нет" /> : null}
                  {vid?.url ? (
                    <figure className="aiv-frame">
                      <div className="aiv-shot-img">
                        <video src={vid.url} poster={start?.url ?? undefined} controls muted loop playsInline preload="none" />
                        {vid.stale ? <span className="aiv-badge">устарело</span> : null}
                        {qa && !qa.ok ? <span className="aiv-badge review">проверь видео</span> : null}
                      </div>
                      <figcaption>видео</figcaption>
                    </figure>
                  ) : null}
                </div>
                <div className="aiv-shot-meta">
                  <b>
                    План {shot.idx} · {shot.duration} с · {CAMERA_LABEL[shot.camera.move] ?? shot.camera.move}
                  </b>
                  <span className="muted">
                    {placeOf(o, shot)} · {castOf(o, shot) || "без персонажей"}
                    {shot.spent_rub ? ` · ${rub(shot.spent_rub)}` : ""}
                  </span>
                  <span className="muted">
                    {shot.needs_motion ? "видео" : "стоп-кадр"}
                    {shot.continues_from_prev && shot.idx > 1 ? " · продолжает прошлый" : ""}
                    {shot.action ? ` · ${shot.action}` : ""}
                  </span>
                  {cont && !cont.ok ? (
                    <span className="aiv-warn">
                      Кадры: {(cont.mismatches ?? []).map((m) => (m.check === "pair" ? "пара: " : "склейка: ") + m.detail).join("; ") ||
                        `оценка ${cont.score}`}
                    </span>
                  ) : cont?.ok && !cont.chain ? (
                    <span className="muted">кадры ок{cont.score != null ? ` · ${cont.score}/10` : ""}</span>
                  ) : null}
                  {qa && !qa.ok ? (
                    <span className="aiv-warn">
                      Видео ({qa.score}/10): {qa.issues.map((i) => `${i.what}: ${i.detail}${i.at != null ? ` (${i.at} с)` : ""}`).join("; ")}
                    </span>
                  ) : qa?.ok ? (
                    <span className="muted">видео ок · {qa.score}/10</span>
                  ) : null}
                  {shot.dialogue.map((l, i) => (
                    <span key={i} className="aiv-line">
                      <b>{o.characters.find((ch) => ch.slug === l.who)?.name ?? l.who}:</b> {l.text}
                    </span>
                  ))}
                  <div className="series-row">
                    {!shared ? (
                      <button className="btn sm" disabled={make.isPending} onClick={() => make.mutate(`shot:${shot.id}:keyframe`)}
                              title="Стартовый кадр: своя маленькая оценка и карточка (сухой прогон — сразу)">
                        {start ? "↻ Старт" : "Старт"}
                      </button>
                    ) : null}
                    {shot.needs_motion ? (
                      <button className="btn sm" disabled={make.isPending} onClick={() => make.mutate(`shot:${shot.id}:endframe`)}
                              title="Конечный кадр (он же старт следующего плана, если тот продолжает сцену)">
                        {end ? "↻ Конец" : "Конец"}
                      </button>
                    ) : null}
                    {start && shot.cast.length ? (
                      <button className="btn ghost sm" disabled={check.isPending} onClick={() => check.mutate(start.id)} title="Gemini сравнит с каноном">
                        Проверить сходство
                      </button>
                    ) : null}
                    {shot.needs_motion && start ? (
                      <button className={`btn sm${qa && !qa.ok ? " primary" : ""}`} disabled={make.isPending}
                              onClick={() => make.mutate(`shot:${shot.id}:shot_video`)}
                              title="Видео этого плана: своя оценка и карточка — платное только после «Разрешить»">
                        {vid ? "↻ Видео" : "Видео"}
                      </button>
                    ) : null}
                    {vid ? (
                      <button className="btn ghost sm" disabled={checkVideo.isPending} onClick={() => checkVideo.mutate(vid.id)}
                              title="Gemini посмотрит 4–6 кадров видео: счёт предметов, дизайн, опора, склейки, конец (бесплатно)">
                        Проверить видео
                      </button>
                    ) : null}
                  </div>
                  {shot.keyframes && shot.keyframes.length > 1 ? <Versions refs={shot.keyframes} projectId={o.project.id} /> : null}
                  {shot.endframes && shot.endframes.length > 1 ? <Versions refs={shot.endframes} projectId={o.project.id} /> : null}
                  {shot.videos && shot.videos.length > 1 ? <Versions refs={shot.videos} projectId={o.project.id} /> : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Блокинг: 3D-макет плана в нашем движке сцен (0 ₽) — композиция для ключевых кадров
// --------------------------------------------------------------------------- //

function BlockingSub({ o }: { o: Overview }) {
  const ep = useEpisode(o);
  const s = ep.script;
  return (
    <div className="series-col">
      <section className="panel series-panel">
        <div className="series-row">
          <EpisodePicker o={o} id={ep.id} setId={ep.setId} />
          {s ? <StageButton projectId={o.project.id} stage="blocking" scriptId={s.id} label="Блокинг серии (0 ₽): оценить" /> : null}
        </div>
        <span className="muted">
          Gemini раскладывает планы простыми манекенами (капсула с «носом» — человек, плоский диск — робот-пылесос, коробки —
          мебель), движок рисует старт и конец плана. Скриншот идёт первым референсом кадра: композиция, позы, места, ракурс.
        </span>
      </section>
      {s ? (
        <div className="series-col">
          {s.shots?.map((shot) => <BlockingShot key={shot.id} shot={shot} projectId={o.project.id} />)}
        </div>
      ) : null}
    </div>
  );
}

function BlockingShot({ shot, projectId }: { shot: Shot; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const qc = useQueryClient();
  const key = ["ai-video", "blocking", String(shot.id)];
  const q = useQuery({ queryKey: key, queryFn: () => aivApi.blocking(shot.id) });
  const [text, setText] = useState<string | null>(null);
  const done = (msg: string) => {
    toast.success(msg);
    setText(null);
    qc.invalidateQueries({ queryKey: key });
    invalidate();
  };
  const save = useMutation({
    mutationFn: () => {
      let spec: unknown;
      try {
        spec = JSON.parse(text ?? "{}");
      } catch {
        throw new Error("раскладка — JSON: {room, objects, start: {actors, props}, end: {…}}");
      }
      return aivApi.setBlocking(shot.id, { spec });
    },
    onSuccess: () => done(`План ${shot.idx}: блокинг отрисован (0 ₽)`),
    onError: (e) => toast.error(e instanceof Error && !(e instanceof ApiError) ? e.message : errText(e, "Не сохранено")),
  });
  const rewrite = useMutation({
    mutationFn: () => aivApi.setBlocking(shot.id, { rewrite: true }),
    onSuccess: () => done(`План ${shot.idx}: раскладка пересоздана`),
    onError: (e) => toast.error(errText(e, "Не пересоздано")),
  });
  const b = q.data;
  const startKf = shot.start_frame ?? null;
  const endKf = shot.end_frame ?? null;
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <b>
          План {shot.idx} · {shot.action || "—"}
        </b>
        <span className="muted">
          {b ? `раскладка: ${BLOCKING_SOURCE[b.source] ?? b.source}${b.outdated ? " (устарела — состояние плана поменялось)" : ""}` : ""}
          {b?.legend ? ` · ${b.legend}` : ""}
        </span>
      </div>
      <div className="aiv-frames">
        <Frame ref_={b?.frames.start ?? shot.blocking_frames?.start} label="блокинг: старт" empty="не отрисован" />
        <Frame ref_={startKf} label="кадр: старт" empty="кадра нет" />
        {shot.needs_motion ? (
          <>
            <Frame ref_={b?.frames.end ?? shot.blocking_frames?.end} label="блокинг: конец" empty="не отрисован" />
            <Frame ref_={endKf} label="кадр: конец" empty="кадра нет" />
          </>
        ) : null}
      </div>
      <div className="series-row">
        <button className="btn ghost sm" disabled={rewrite.isPending} onClick={() => rewrite.mutate()}
                title="Gemini заново разложит план по состоянию и концу плана (без ключа — по состоянию)">
          ↻ Пересоздать раскладку
        </button>
      </div>
      {/* JSON раскладки нужен редко (правка вручную) — свёрнут, чтобы три плана не были тремя простынями. */}
      <details className="aiv-more" open={text != null}>
        <summary>Править раскладку вручную (JSON)</summary>
        <label className="field">
          <span>Сантиметры: x вправо, z к камере; facing 0 — в камеру, 90 — вправо</span>
          <textarea className="input mono" rows={6} value={text ?? (b ? JSON.stringify(b.spec, null, 1) : "")}
                    onChange={(e) => setText(e.target.value)} />
        </label>
        <div className="series-row">
          <button className="btn sm" disabled={save.isPending || text == null} onClick={() => save.mutate()}>
            Сохранить и отрисовать
          </button>
        </div>
      </details>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Сборка и журнал трат
// --------------------------------------------------------------------------- //

function AssembleSub({ o }: { o: Overview }) {
  const ep = useEpisode(o);
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [music, setMusic] = useState("");
  const [result, setResult] = useState<{ clip_id: number; duration: number; editor_url: string; notes: string[] } | null>(null);
  const assemble = useMutation({
    mutationFn: () => aivApi.assemble(ep.id as number, music ? { music_track_id: Number(music) } : {}),
    onSuccess: (r) => {
      setResult(r);
      toast.success(`Клип #${r.clip_id}: ${r.duration} с`);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не собрано")),
  });
  const ledger = useQuery({ queryKey: [...aivKey(o.project.id), "ledger"], queryFn: () => aivApi.ledger(o.project.id) });
  const s: Script | undefined = ep.script;
  const missing = useMemo(
    () => (s?.shots ?? []).filter((x) => !x.keyframes?.length && !x.videos?.length).map((x) => x.idx),
    [s],
  );
  return (
    <div className="series-grid">
      <div className="series-col">
        <section className="panel series-panel">
          <div className="series-row">
            <EpisodePicker o={o} id={ep.id} setId={ep.setId} />
          </div>
          {s ? (
            <>
              <StageButton projectId={o.project.id} stage="voice" scriptId={s.id} label="Озвучка реплик: оценить" />
              {missing.length ? <span className="aiv-warn">Нет кадров у планов {missing.join(", ")} — «Раскадровка».</span> : null}
              <div className="series-row">
                <input className="input" placeholder="id музыки (необязательно)" value={music} onChange={(e) => setMusic(e.target.value)} />
                <button className="btn primary sm" disabled={!!missing.length || assemble.isPending} onClick={() => assemble.mutate()}>
                  🎬 Собрать клип
                </button>
              </div>
              {s.clip_plan_id ? <span className="muted">Последняя сборка: клип #{s.clip_plan_id}</span> : null}
              {result ? (
                <div className="aiv-est">
                  <Link to={result.editor_url}>Открыть в редакторе →</Link>
                  {result.notes.map((n) => (
                    <span key={n} className="muted">
                      {n}
                    </span>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </section>
      </div>
      <div className="series-col">
        <section className="panel series-panel">
          <h3>Журнал трат</h3>
          {ledger.data?.ledger.length ? (
            <table className="aiv-ledger">
              <tbody>
                {ledger.data.ledger.map((l) => (
                  <tr key={l.id}>
                    <td className="muted">{l.created_at}</td>
                    <td>{l.label || l.stage}</td>
                    <td className="mono">{l.model}</td>
                    <td>{rub(l.amount_rub)}</td>
                    <td className="muted">{l.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">Трат не было.</p>
          )}
        </section>
      </div>
    </div>
  );
}
