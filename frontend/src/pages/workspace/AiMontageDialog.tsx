import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { clipPlansApi, type AiMontageProposal } from "@/api/clipPlans";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { formatDuration } from "@/components/ui";

const GOALS = ["короче", "сильнее хук", "больше мемности", "без пауз", "до 30 секунд"];

/**
 * 🤖 ИИ-монтаж — the agent re-edits the clip through its clip file.
 *
 * Propose → look at «было → стало» and the reasons → apply (optionally render).
 * Nothing changes until «Применить»; «Ещё вариант» asks again. After a render,
 * running it again feeds the render's QC report back to the agent.
 */
export function AiMontageDialog({
  clipPlanId,
  clipTitle,
  onClose,
  onApplied,
}: {
  clipPlanId: number;
  clipTitle: string;
  onClose: () => void;
  onApplied: (changes: string[], rendered: boolean) => void;
}) {
  const toast = useToast();
  const [goal, setGoal] = useState("");
  const [proposal, setProposal] = useState<AiMontageProposal | null>(null);

  const propose = useMutation({
    mutationFn: () => clipPlansApi.aiMontage(clipPlanId, goal.trim()),
    onSuccess: (p) => setProposal(p),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ИИ-монтаж не удался"),
  });

  const apply = useMutation({
    mutationFn: async (render: boolean) => {
      if (!proposal) throw new Error("нет предложения");
      const res = await clipPlansApi.putSpec(clipPlanId, proposal.spec);
      if (render) await clipPlansApi.render(clipPlanId);
      return { changes: res.changes, render };
    },
    onSuccess: ({ changes, render }) => {
      onApplied(changes, render);
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось применить"),
  });

  const busy = propose.isPending || apply.isPending;
  const d = proposal?.diff;
  const span = d
    ? Math.max(
        ...d.before.map((p) => p.end_sec),
        ...d.after.map((p) => p.end_sec),
      ) - Math.min(...d.before.map((p) => p.start_sec), ...d.after.map((p) => p.start_sec))
    : 0;
  const origin = d ? Math.min(...d.before.map((p) => p.start_sec), ...d.after.map((p) => p.start_sec)) : 0;

  const lane = (pieces: { start_sec: number; end_sec: number; title: string }[], kind: "before" | "after") => (
    <div className={`aim-lane aim-lane--${kind}`}>
      {pieces.map((p, i) => (
        <span
          key={i}
          className="aim-piece"
          style={{
            left: `${((p.start_sec - origin) / (span || 1)) * 100}%`,
            width: `max(4px, ${((p.end_sec - p.start_sec) / (span || 1)) * 100}%)`,
          }}
          title={`${i + 1}. ${p.title || "кусок"} · ${formatDuration(p.start_sec)}–${formatDuration(p.end_sec)}`}
        >
          <span className="aim-piece-n">{i + 1}</span>
        </span>
      ))}
    </div>
  );

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div className="modal aim-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="pub-head">
          <h3>🤖 ИИ-монтаж · {clipTitle}</h3>
          <button className="pub-x" title="Закрыть" onClick={onClose} disabled={busy}>
            ×
          </button>
        </div>

        <p className="muted aim-lead">
          ИИ читает речь клипа с таймкодами и паузами, ставит хук в начало, вырезает паузы и воду, режет длинные
          куски по паузам, подбирает переход и кадр обложки. Кадрирование переносится. Ничего не меняется, пока не
          нажмёте «Применить». После рендера запустите ещё раз — ИИ учтёт отчёт QC.
        </p>

        <div className="aim-goal">
          <input
            className="input"
            placeholder="Пожелание (необязательно): короче, больше мемности, закончить на панчлайне…"
            value={goal}
            maxLength={500}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !busy && propose.mutate()}
          />
          <div className="aim-chips">
            {GOALS.map((g) => (
              <button key={g} type="button" className="chip" onClick={() => setGoal((cur) => (cur ? `${cur}, ${g}` : g))}>
                {g}
              </button>
            ))}
          </div>
        </div>

        {propose.isPending ? (
          <div className="aim-wait">
            <span className="src-step-dot aim-spin" /> Монтирую… обычно 20–60 секунд
          </div>
        ) : null}

        {proposal && d && !propose.isPending ? (
          <div className="aim-result">
            <div className="aim-lanes">
              <div className="aim-lane-label">
                Было · {formatDuration(d.total_before)} · {d.before.length} к.
              </div>
              {lane(d.before, "before")}
              <div className="aim-lane-label">
                Стало · {formatDuration(d.total_after)} · {d.after.length} к.
              </div>
              {lane(d.after, "after")}
            </div>
            <ol className="aim-pieces">
              {d.after.map((p, i) => (
                <li key={i}>
                  <b>{p.title || `Кусок ${i + 1}`}</b>
                  <span className="muted mono">
                    {formatDuration(p.start_sec)}–{formatDuration(p.end_sec)} · {(p.end_sec - p.start_sec).toFixed(1)} с
                  </span>
                </li>
              ))}
            </ol>
            <div className="aim-meta muted">
              переход: <b>{d.transition}</b>
              {d.sfx !== "none" ? ` + ${d.sfx}` : ""} · субтитры: <b>{d.subtitles ? "вкл" : "выкл"}</b> ·{" "}
              {proposal.attempts > 1 ? `с ${proposal.attempts}-й попытки · ` : ""}
              {proposal.used_qc ? "учёл QC · " : ""}
              {proposal.has_transcript ? "" : "⚠ транскрипта нет — монтаж вслепую · "}
              {proposal.model}
            </div>
            {proposal.rationale.length ? (
              <ul className="aim-why">
                {proposal.rationale.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <div className="aim-actions">
          <button className="btn ghost" onClick={onClose} disabled={busy}>
            Отмена
          </button>
          <button className="btn" disabled={busy} onClick={() => propose.mutate()}>
            {proposal ? "↻ Ещё вариант" : "🤖 Предложить монтаж"}
          </button>
          {proposal ? (
            <>
              <button className="btn" disabled={busy} onClick={() => apply.mutate(false)}>
                Применить
              </button>
              <button className="btn primary" disabled={busy} onClick={() => apply.mutate(true)}>
                {apply.isPending ? "…" : "Применить и отрендерить"}
              </button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
