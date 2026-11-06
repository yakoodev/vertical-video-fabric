// ИИ-видео, раскадровка: кадры планов, видео и проверки.
import { useMutation } from "@tanstack/react-query";
import { CAMERA_LABEL, aivApi, type Overview, type Shot } from "@/api/aivideo";
import { useToast } from "@/components/Toast";
import { EpisodePicker, errText, Frame, rub, useEpisode, useInvalidate } from "./shared";
import { BudgetBar } from "./BudgetCards";
import { StageButton, Versions } from "./StageControls";

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

export function BoardSub({ o }: { o: Overview }) {
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
      toast.success(r.status === "awaiting_approval" ? `≈ ${rub(r.estimate.total_rub)} — ждёт «Разрешить»` : r.status === "approved" ? r.note : "Готово");
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
