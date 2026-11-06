// ИИ-видео, деньги: полоса бюджета и карточки этапов.
import { useMutation } from "@tanstack/react-query";
import { STAGE_LABEL, aivApi, type Budget, type StageCard } from "@/api/aivideo";
import { useToast } from "@/components/Toast";
import { errText, rub, useInvalidate } from "./shared";

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

export function BudgetBar({ budget, episode }: { budget: Budget; episode?: boolean }) {
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

export function StageCards({ projectId, cards }: { projectId: number; cards: StageCard[] }) {
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
