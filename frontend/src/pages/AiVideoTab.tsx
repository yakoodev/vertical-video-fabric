import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { aivApi, aivKey, type Overview, type Script } from "@/api/aivideo";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";
import { errText, FixedScript, rub, useInvalidate } from "./aivideo/shared";
import { BudgetBar, StageCards } from "./aivideo/BudgetCards";
import { ScriptSub } from "./aivideo/ScriptSub";
import { CharactersSub, LocationsSub } from "./aivideo/CastSubs";
import { BoardSub } from "./aivideo/BoardSub";
import { BlockingSub } from "./aivideo/BlockingSub";
import { AssembleSub } from "./aivideo/AssembleSub";
import "@/styles/aivideo.css";

/**
 * ИИ-видео (app/aivideo*.py): Сценарий / Персонажи / Локации / Блокинг / Раскадровка / Сборка. Одна и та же страница в двух
 * местах: «Материалы» проекта (стиль, персонажи, локации, бюджет, журнал трат) и страница выпуска (одна серия, шаги слева).
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

// В «Материалах» видно только то, что относится к проекту: Сценарий там это стиль и список серий.
const MATERIAL_SUBS: Sub[] = ["script", "characters", "locations"];

/** Форма типа в «Новое видео»: бриф и длина, дальше страница выпуска. */
export function AiVideoNew({ projectId, onCreated }: { projectId: number; onCreated: (script: Script) => void }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [brief, setBrief] = useState("");
  const [target, setTarget] = useState(30);
  const create = useMutation({
    mutationFn: () => aivApi.addScript(projectId, { brief, duration_target: target }),
    onSuccess: (s) => {
      invalidate();
      onCreated(s);
    },
    onError: (e) => toast.error(errText(e, "Не создано")),
  });
  return (
    <section className="panel series-panel">
      <h3>Новое ИИ-видео</h3>
      <p className="muted series-hint">
        Дальше по шагам: сценарий (бесплатно), персонажи, раскадровка, видео. Перед каждым платным шагом оценка и карточка с суммой, без «Разрешить» деньги не тратятся.
      </p>
      <label className="field">
        <span>О чём серия</span>
        <textarea className="input" rows={3} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="Сонная Мира хочет утренний кофе, а робот-пылесос Бип требует внимания" />
      </label>
      <div className="series-row">
        <label className="field">
          <span>Длина, с</span>
          <input className="input" type="number" min={10} max={120} value={target} onChange={(e) => setTarget(Number(e.target.value))} />
        </label>
        <button className="btn primary sm" disabled={brief.trim().length < 3 || create.isPending} onClick={() => create.mutate()}>
          ✨ Создать и перейти к шагам
        </button>
      </div>
    </section>
  );
}

export function AiVideoTab({ projectId, scriptId }: { projectId: number; scriptId?: number }) {
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
  const fixed = scriptId != null;
  // На странице серии — её карточки и общие на проект (листы, плиты). Карточки роликов других типов живут на своих страницах.
  const cards = fixed
    ? o.approvals.filter((c) => c.script_id === scriptId || (c.script_id == null && !["explainer", "stories"].includes(String(c.stage))))
    : o.approvals;
  // Старая ссылка ?sub=board из «Материалов»: подвкладка серии работает и там, но в ленте видна только по такой ссылке.
  const shownSubs = SUBS.filter(([key]) => MATERIAL_SUBS.includes(key) || key === sub);
  return (
    <FixedScript.Provider value={scriptId ?? null}>
      <div className="aiv">
        <BudgetBar budget={o.budget} />
        <StageCards projectId={projectId} cards={cards} />
        {fixed ? null : (
          <nav className="pipeline aiv-subtabs">
            {shownSubs.map(([key, label]) => (
              <button key={key} className={`tab${sub === key ? " active" : ""}`} onClick={() => setSub(key)}>
                {key === "script" ? "Стиль и серии" : label}
              </button>
            ))}
            <button className={`tab${raw === "ledger" ? " active" : ""}`} onClick={() => setParams((prev) => { const p = new URLSearchParams(prev); p.set("sub", "ledger"); return p; }, { replace: true })}>
              Журнал трат
            </button>
          </nav>
        )}
        {raw === "ledger" && !fixed ? (
          <Ledger projectId={projectId} />
        ) : (
          <>
            {sub === "script" ? <ScriptSub o={o} /> : null}
            {sub === "characters" ? <CharactersSub o={o} /> : null}
            {sub === "locations" ? <LocationsSub o={o} /> : null}
            {sub === "blocking" ? <BlockingSub o={o} /> : null}
            {sub === "board" ? <BoardSub o={o} /> : null}
            {sub === "assemble" ? <AssembleSub o={o} /> : null}
          </>
        )}
      </div>
    </FixedScript.Provider>
  );
}

function Ledger({ projectId }: { projectId: number }) {
  const ledger = useQuery({ queryKey: [...aivKey(projectId), "ledger"], queryFn: () => aivApi.ledger(projectId) });
  return (
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
  );
}
