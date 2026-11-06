// ИИ-видео, сценарий: стиль-библия, серия и правка планов.
import { useContext, useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CAMERA_LABEL, CAMERA_MOVES, VIDEO_MODELS, aivApi, type Overview, type Shot } from "@/api/aivideo";
import { ApiError } from "@/api/client";
import { projectPath, newVideoPath } from "@/lib/paths";
import { useToast } from "@/components/Toast";
import { EpisodePicker, errText, FixedScript, useEpisode, useInvalidate } from "./shared";
import { Versions } from "./StageControls";

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

export function ScriptSub({ o }: { o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const ep = useEpisode(o);
  const fixed = useContext(FixedScript) != null;
  const [brief, setBrief] = useState("");
  useEffect(() => setBrief(ep.script?.brief ?? ""), [ep.script?.id, ep.script?.brief]);
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
    <div className={fixed ? "series-col" : "series-grid"}>
      {fixed ? (
        <p className="muted series-hint">
          Стиль, модели и бюджет общие на весь проект: <Link to={`${projectPath(o.project.id, "materials")}&mat=aiv`}>материалы проекта</Link>.
        </p>
      ) : (
        <div className="series-col">
          <StylePanel o={o} />
        </div>
      )}
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
            {fixed ? null : (
              <Link className="btn sm" to={newVideoPath(o.project.id, "aiv")}>
                ＋ Новая серия
              </Link>
            )}
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
