// ИИ-видео: кнопка платного этапа с оценкой и версии картинок.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { STAGE_LABEL, aivApi, type AivRef, type Estimate, type Stage } from "@/api/aivideo";
import { useToast } from "@/components/Toast";
import { errText, rub, useInvalidate } from "./shared";

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

/** «Оценить» (бесплатно) → «Запросить» (карточка владельцу или сразу — сухой прогон). */
export function StageButton({
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

export function Versions({ refs, projectId, onPick }: { refs: AivRef[]; projectId: number; onPick?: () => void }) {
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
      toast.success(r.status === "awaiting_approval" ? `Перегенерация ≈ ${rub(r.estimate.total_rub)} — ждёт «Разрешить»` : r.status === "approved" ? r.note : "Новая версия готова");
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
