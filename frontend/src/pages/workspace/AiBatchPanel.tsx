import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clipPlansApi, type AiMontageJob } from "@/api/clipPlans";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { useToast } from "@/components/Toast";
import { formatDuration } from "@/components/ui";

const STATUS: Record<string, string> = {
  queued: "в очереди",
  thinking: "монтирую…",
  rendering: "рендер…",
  done: "готово",
  failed: "ошибка",
};

/**
 * 🤖 ИИ-монтаж всех ★ — runs in the background on the server (3 clips at a
 * time), applies each re-edit (the pre-AI version is kept: «↩» per clip) and
 * optionally renders. Progress survives a page reload: the panel asks the
 * server for the project's latest batch.
 */
export function AiBatchPanel({ sourceId, favCount }: { sourceId: string; favCount: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [goal, setGoal] = useState("");
  const [render, setRender] = useState(false);
  const [pickOpen, setPickOpen] = useState(false);
  const [pickCount, setPickCount] = useState(10);
  const [pickGoal, setPickGoal] = useState("");
  const [pickMontage, setPickMontage] = useState(true);

  const job = useQuery({
    queryKey: ["ai-montage-batch", sourceId],
    queryFn: () => clipPlansApi.aiMontageBatchStatus(sourceId),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });
  const data: AiMontageJob | null | undefined = job.data;
  const running = data?.status === "running";

  // Each finished clip changed on the server — refresh the moments as they land.
  const doneCount = data?.done ?? 0;
  useEffect(() => {
    if (doneCount) qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    if (data && data.status !== "running" && data.render) {
      qc.invalidateQueries({ queryKey: qk.clips(sourceId) });
    }
  }, [doneCount, data?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = useMutation({
    mutationFn: () => clipPlansApi.aiMontageBatch(sourceId, { goal: goal.trim(), render }),
    onSuccess: (j) => {
      qc.setQueryData(["ai-montage-batch", sourceId], j);
      setOpen(false);
      toast.success(`ИИ-монтаж запущен: ${j.total} клип(ов)`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить"),
  });

  const pick = useMutation({
    mutationFn: () =>
      clipPlansApi.aiPick(sourceId, { count: pickCount, goal: pickGoal.trim(), montage: pickMontage, render }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      if (r.montage_job) {
        qc.setQueryData(["ai-montage-batch", sourceId], r.montage_job);
      }
      setPickOpen(false);
      toast.success(
        `✨ Выбрал ${r.picks.length} из ${r.candidates}: они в ★` + (r.montage_job ? " · 🤖 ИИ-монтаж запущен" : ""),
      );
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ИИ не смог выбрать"),
  });

  return (
    <div className="aib">
      <button
        className="btn mo-big aib-btn aib-pick"
        disabled={pick.isPending || running}
        onClick={() => setPickOpen((v) => !v)}
        title="ИИ прочитает все моменты и отметит ★ самые вирусные — с оценкой и причиной на карточке"
      >
        {pick.isPending ? "✨ Выбираю… (~30 с)" : "✨ ИИ выберет лучшие"}
      </button>
      {pickOpen && !pick.isPending ? (
        <div className="aib-form">
          <div className="aib-row">
            <span>Сколько</span>
            <div className="seg" role="radiogroup" aria-label="Сколько выбрать">
              {[5, 10, 20].map((n) => (
                <button
                  key={n}
                  type="button"
                  role="radio"
                  aria-checked={pickCount === n}
                  className={`seg-item${pickCount === n ? " active" : ""}`}
                  onClick={() => setPickCount(n)}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
          <input
            className="input"
            placeholder="Что ищем (необязательно): смешное, эмоции, полезное…"
            value={pickGoal}
            maxLength={500}
            onChange={(e) => setPickGoal(e.target.value)}
          />
          <label className="switch">
            <input type="checkbox" checked={pickMontage} onChange={(e) => setPickMontage(e.target.checked)} />
            <span className="switch-track" />
            <span>и сразу 🤖 ИИ-монтаж выбранных</span>
          </label>
          {pickMontage ? (
            <label className="switch">
              <input type="checkbox" checked={render} onChange={(e) => setRender(e.target.checked)} />
              <span className="switch-track" />
              <span>и отрендерить</span>
            </label>
          ) : null}
          <p className="muted aib-note">Твои ★ не снимаются — ИИ только добавляет. Выбор ≈30 с, копейки.</p>
          <button className="btn primary" onClick={() => pick.mutate()}>
            ✨ Выбрать
          </button>
        </div>
      ) : null}
      <button
        className="btn mo-big aib-btn"
        disabled={running || !favCount}
        onClick={() => setOpen((v) => !v)}
        title={favCount ? "ИИ перемонтирует все избранные клипы: хук, без пауз, переход, обложка" : "Отметьте ★ моменты"}
      >
        {running ? `🤖 ИИ-монтаж… ${data?.done}/${data?.total}` : `🤖 ИИ-монтаж всех ★ (${favCount})`}
      </button>

      {open && !running ? (
        <div className="aib-form">
          <input
            className="input"
            placeholder="Пожелание для всех (необязательно): короче, больше мемности…"
            value={goal}
            maxLength={500}
            onChange={(e) => setGoal(e.target.value)}
          />
          <label className="switch">
            <input type="checkbox" checked={render} onChange={(e) => setRender(e.target.checked)} />
            <span className="switch-track" />
            <span>сразу отрендерить</span>
          </label>
          <p className="muted aib-note">
            Применяется сразу к {favCount} клипам, у каждого сохраняется версия до ИИ — откат кнопкой ↩. ~30–60 с на
            клип, по 3 параллельно.
          </p>
          <button className="btn primary" disabled={start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Запуск…" : "▶ Запустить"}
          </button>
        </div>
      ) : null}

    </div>
  );
}

/**
 * The batch report is its own full-width block under the player: a 10-clip run
 * crammed into the narrow stats column cut the titles and the statuses off.
 */
export function AiBatchReport({ sourceId, onOpenClip }: { sourceId: string; onOpenClip?: (planId: number) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [dismissed, setDismissed] = useState<string | null>(null);
  const job = useQuery({
    queryKey: ["ai-montage-batch", sourceId],
    queryFn: () => clipPlansApi.aiMontageBatchStatus(sourceId),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });
  const data: AiMontageJob | null | undefined = job.data;
  const running = data?.status === "running";

  const undo = useMutation({
    mutationFn: (planId: number) => clipPlansApi.aiMontageUndo(planId),
    onSuccess: () => {
      toast.success("Откатил клип к версии до ИИ");
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось откатить"),
  });

  if (!data || data.id === dismissed) return null;
  return (
    <div className="panel aib-report">
      <div className="aib-head">
        <b>
          {running ? "🤖 Монтирую" : "🤖 ИИ-монтаж"} · {data.done}/{data.total}
          {data.failed ? ` · ошибок ${data.failed}` : ""}
          {data.goal ? <span className="muted"> · «{data.goal}»</span> : null}
        </b>
        {!running ? (
          <button className="aib-x" title="Скрыть отчёт" onClick={() => setDismissed(data.id)}>
            ×
          </button>
        ) : null}
      </div>
      <div className="src-bar">
        <span style={{ width: `${data.total ? Math.round((data.done / data.total) * 100) : 0}%` }} />
      </div>
      <ul className="aib-items">
        {data.items.map((it) => (
          <li key={it.plan_id} className={`aib-item ${it.status}`}>
            <button
              className="aib-item-title"
              onClick={() => onOpenClip?.(it.plan_id)}
              title="Открыть этот клип в монтаже"
            >
              {it.title || `План #${it.plan_id}`}
            </button>
            <span className="aib-item-st">
              {it.status === "done" && it.total_before != null
                ? `${formatDuration(it.total_before)} → ${formatDuration(it.total_after ?? 0)}`
                : STATUS[it.status] ?? it.status}
            </span>
            <span className="aib-item-why muted">{it.error || (it.rationale || [])[0] || ""}</span>
            {it.status === "done" ? (
              <button
                className="aib-undo"
                title="Откатить этот клип к версии до ИИ"
                disabled={undo.isPending}
                onClick={() => undo.mutate(it.plan_id)}
              >
                ↩
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
