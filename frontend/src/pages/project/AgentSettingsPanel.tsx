import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { accountsApi } from "@/api/accounts";
import { agentSettingsApi, agentSettingsKey, type AgentSettings } from "@/api/agentSettings";
import { PLATFORM_LABEL, PUBLISH_STATUS_LABEL } from "@/api/autopilot";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { useToast } from "@/components/Toast";
import { Loading, plural } from "@/components/ui";
import { useModuleOn } from "@/hooks/useModules";
import "@/styles/agent-settings.css";

/** «2026-11-06 12:30:00» (UTC) → «6 ноября». */
function shortDate(text: string): string {
  const moment = new Date(String(text || "").replace(" ", "T") + "Z");
  return Number.isNaN(moment.getTime()) ? "" : moment.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

const rub = (value: number) => `${Number(value || 0).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;

function Row({
  checked,
  disabled,
  title,
  hint,
  onChange,
  children,
}: {
  checked: boolean;
  disabled?: boolean;
  title: string;
  hint: React.ReactNode;
  onChange: (on: boolean) => void;
  children?: React.ReactNode;
}) {
  return (
    <div className={`agset-row${checked ? " on" : ""}`}>
      <label className="agset-switch">
        <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        <span className="agset-text">
          <strong>{title}</strong>
          <span className="agset-hint">{hint}</span>
        </span>
      </label>
      {children ? <div className="agset-more">{children}</div> : null}
    </div>
  );
}

/** Раздел страницы проекта «Как работает агент»: четыре простых переключателя (решение 15 концепции). */
export function AgentSettingsPanel({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const publishingOn = useModuleOn("publishing");
  const query = useQuery({ queryKey: agentSettingsKey(projectId), queryFn: () => agentSettingsApi.get(projectId) });
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list, enabled: publishingOn });
  const [draft, setDraft] = useState<AgentSettings | null>(null);
  useEffect(() => {
    if (query.data) setDraft(query.data.settings);
  }, [query.data]);
  const dirty = useMemo(
    () => Boolean(draft && query.data && JSON.stringify(draft) !== JSON.stringify(query.data.settings)),
    [draft, query.data],
  );
  const save = useMutation({
    mutationFn: () => agentSettingsApi.save(projectId, draft!),
    onSuccess: (view) => {
      qc.setQueryData(agentSettingsKey(projectId), view);
      const cancelled = view.cancelled_publications ?? 0;
      toast.success(cancelled ? `Сохранено. Снято из очереди: ${plural(cancelled, "публикация", "публикации", "публикаций")}` : "Сохранено");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не сохранилось"),
  });
  if (query.isLoading || !draft || !query.data) {
    return (
      <section className="panel agset" id="agent-settings">
        <Loading />
      </section>
    );
  }
  const view = query.data;
  const spend = draft.spend;
  const publish = draft.publish;
  const setSpend = (patch: Partial<AgentSettings["spend"]>) => setDraft({ ...draft, spend: { ...spend, ...patch } });
  const setPublish = (patch: Partial<AgentSettings["publish"]>) => setDraft({ ...draft, publish: { ...publish, ...patch } });
  const list = accounts.data ?? [];
  const toggleAccount = (id: number, on: boolean) => {
    const ids = on ? [...publish.account_ids, id].sort((a, b) => a - b) : publish.account_ids.filter((x) => x !== id);
    setPublish({ account_ids: ids, enabled: ids.length ? publish.enabled : false });
  };
  const spendBad = spend.enabled && (spend.per_run_rub <= 0 || spend.per_month_rub <= 0 || spend.per_run_rub > spend.per_month_rub);

  return (
    <section className="panel agset" id="agent-settings">
      <div className="agset-head">
        <div>
          <h3>Как работает агент</h3>
          <p className="muted">
            Только для этого проекта. Менять можешь только ты: агент эти настройки видит, держится их и изменить не может.
          </p>
        </div>
        <button type="button" className="btn primary sm" disabled={!dirty || spendBad || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Сохраняю…" : dirty ? "Сохранить" : "Сохранено"}
        </button>
      </div>

      <div className="agset-rows">
        <Row
          checked={draft.wait_answers}
          title="Ждать ответов на вопросы перед монтажом"
          hint={
            draft.wait_answers
              ? "Открытый вопрос держит новую нарезку и вырезы этого видео, пока ты не ответишь или не нажмёшь «Монтировать без ответов»."
              : "Агент спрашивает, но работает не дожидаясь ответов."
          }
          onChange={(on) => setDraft({ ...draft, wait_answers: on })}
        />

        <Row
          checked={draft.stop_at_clip_list}
          title="Останавливаться на списке клипов"
          hint={
            draft.stop_at_clip_list ? (
              <>
                Агент находит черновики и останавливается. Монтирует только те, что ты выбрал: ★ на вкладке «Черновики» видео.
                {view.drafts_waiting ? ` Сейчас ждут выбора: ${view.drafts_waiting}.` : ""}
              </>
            ) : (
              "Агент сам выбирает черновики, монтирует и рендерит их. Наблюдатель VOD идёт дальше до карточек публикации."
            )
          }
          onChange={(on) => setDraft({ ...draft, stop_at_clip_list: on })}
        />

        <Row
          checked={spend.enabled}
          title="Тратить без подтверждения"
          hint={
            spend.enabled
              ? "В пределах сумм этапы ИИ-видео, объяснялок и историй запускаются сразу. Сверх сумм придёт обычная карточка с «Разрешить»."
              : "Каждая платная генерация ждёт твоего «Разрешить» на карточке с суммой."
          }
          onChange={(on) => setSpend({ enabled: on })}
        >
          {spend.enabled ? (
            <>
              <div className="agset-fields">
                <label className="field">
                  <span>За раз, ₽</span>
                  <input className="input" type="number" min={0} step={10} value={spend.per_run_rub} onChange={(e) => setSpend({ per_run_rub: Number(e.target.value) })} />
                </label>
                <label className="field">
                  <span>За месяц, ₽</span>
                  <input className="input" type="number" min={0} step={50} value={spend.per_month_rub} onChange={(e) => setSpend({ per_month_rub: Number(e.target.value) })} />
                </label>
              </div>
              {spend.per_run_rub <= 0 || spend.per_month_rub <= 0 ? (
                <p className="agset-warn">Укажи обе суммы: с нулём тратить нечего.</p>
              ) : spend.per_run_rub > spend.per_month_rub ? (
                <p className="agset-warn">Сумма за раз больше суммы за месяц.</p>
              ) : null}
            </>
          ) : null}
          <p className="agset-note">
            В этом месяце по этой настройке: {rub(view.spend_state.spent_month_rub)}
            {view.spend_state.reserved_rub ? `, ещё ${rub(view.spend_state.reserved_rub)} в работе` : ""}. Потолок проекта на месяц:{" "}
            {rub(view.spend_state.project_month_cap_rub)}, он действует всегда.
            {view.spend_state.paid_enabled ? "" : " Платная генерация сейчас выключена в настройках сервиса: пока так, деньги не потратятся."}
          </p>
          {view.spend_state.recent.length ? (
            <ul className="agset-log">
              {view.spend_state.recent.map((s) => (
                <li key={s.approval_id}>
                  <Link to={`/inbox?tab=approve&id=${s.approval_id}`}>{s.title}</Link>
                  <span className="muted">{rub(s.amount_rub)} · {shortDate(s.decided_at)} · разрешила настройка</span>
                </li>
              ))}
            </ul>
          ) : null}
        </Row>

        <Row
          checked={publish.enabled}
          disabled={!publishingOn || (!publish.account_ids.length && !publish.enabled)}
          title="Публиковать самому"
          hint={
            !publishingOn
              ? "Модуль публикации выключен в настройках."
              : publish.enabled
                ? "Готовый клип уходит сам, только на отмеченные аккаунты и только с этой видимостью. Всё остальное придёт карточкой. В «Публикациях» такие задания помечены «Агент»."
                : publish.account_ids.length
                  ? "Выключено: клипы идут на одобрение."
                  : "Выключено: клипы идут на одобрение. Чтобы включить, сначала отметь хотя бы один аккаунт: без явного выбора агент не публикует."
          }
          onChange={(on) => setPublish({ enabled: on })}
        >
          {publishingOn ? (
            <>
              {list.length ? (
                <ul className="agset-accounts">
                  {list.map((account) => (
                    <li key={account.id}>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={publish.account_ids.includes(account.id)}
                          onChange={(e) => toggleAccount(account.id, e.target.checked)}
                        />
                        {PLATFORM_LABEL[account.platform] ?? account.platform} · {account.label}
                      </label>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="agset-note">
                  Аккаунтов пока нет. <Link to="/pubs?tab=accounts">Добавить аккаунт</Link>
                </p>
              )}
              {publish.account_ids.length ? (
                <div className="agset-fields">
                  <label className="field">
                    <span>Видимость</span>
                    <select className="input" value={publish.privacy} onChange={(e) => setPublish({ privacy: e.target.value as AgentSettings["publish"]["privacy"] })}>
                      <option value="private">Только для себя</option>
                      <option value="unlisted">По ссылке</option>
                      <option value="public">Для всех</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Не больше в день</span>
                    <input className="input" type="number" min={1} max={50} value={publish.per_day} onChange={(e) => setPublish({ per_day: Math.max(1, Number(e.target.value) || 1) })} />
                  </label>
                </div>
              ) : null}
              {publish.enabled ? <p className="agset-note">Время выхода берётся из слотов ниже. Выключишь: то, что агент уже поставил в очередь и что ещё не ушло, снимется.</p> : null}
              {view.publish_state.recent.length ? (
                <ul className="agset-log">
                  {view.publish_state.recent.map((p) => (
                    <li key={p.approval_id}>
                      {p.job_id ? <Link to={`/pubs/${p.job_id}`}>{p.title}</Link> : <span>{p.title}</span>}
                      <span className="muted">{PUBLISH_STATUS_LABEL[p.publish_status] ?? p.publish_status} · поставил агент</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}
        </Row>
      </div>

      <details className="agset-told">
        <summary>Что агент прочитает о проекте</summary>
        <ul>
          {view.summary.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
