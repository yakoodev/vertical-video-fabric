import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HYP_STATUS_LABEL, METRIC_OPTIONS, projectsApi, type Hypothesis } from "@/api/projects";
import { formatSnapshotTime } from "@/api/stats";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { EmptyState, Loading } from "@/components/ui";

/** Подвкладка «Гипотезы» в «Лор и правила»: клипы с тегом против остальных по медиане. */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function groupLabel(group: Record<string, unknown>): string {
  if (typeof group.tag === "string") return `#${group.tag}`;
  if (typeof group.feature === "string") return `${group.feature} ${group.op} ${String(group.value)}`;
  return JSON.stringify(group);
}

export function ProjectHypothesesTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const hyps = useQuery({ queryKey: qk.channelHypotheses(projectId), queryFn: () => projectsApi.hypotheses(projectId) });
  const brief = useQuery({ queryKey: qk.channelBrief(projectId), queryFn: () => projectsApi.brief(projectId) });
  const [form, setForm] = useState({ statement: "", tag: "", metric: "views", min_n: "5", rule_text: "" });
  const knownTags = useMemo(
    () => Array.from(new Set((brief.data?.covered ?? []).flatMap((c) => c.tags))).sort(),
    [brief.data],
  );
  const reload = () => qc.invalidateQueries({ queryKey: qk.channelHypotheses(projectId) });

  const add = useMutation({
    mutationFn: () =>
      projectsApi.addHypothesis(projectId, {
        statement: form.statement,
        group: form.tag.trim(),
        metric: form.metric,
        min_n: Number(form.min_n) || 5,
        rule_text: form.rule_text,
      }),
    onSuccess: () => {
      setForm({ statement: "", tag: "", metric: "views", min_n: "5", rule_text: "" });
      reload();
    },
    onError: (e) => toast.error(errText(e, "Не удалось добавить гипотезу")),
  });
  const evaluate = useMutation({
    mutationFn: () => projectsApi.evaluate(projectId),
    onSuccess: () => {
      toast.success("Гипотезы пересчитаны");
      reload();
      qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId), "rules"] });
    },
    onError: (e) => toast.error(errText(e, "Не удалось пересчитать")),
  });
  const promote = useMutation({
    mutationFn: (hid: number) => projectsApi.promote(hid),
    onSuccess: (res) => {
      if (res.created) toast.success("Правило записано");
      else toast.push(res.question || res.note || "Правило не записано", "info");
      qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId), "rules"] });
    },
    onError: (e) => toast.error(errText(e, "Не удалось")),
  });
  const unlock = useMutation({
    mutationFn: (h: Hypothesis) => projectsApi.updateHypothesis(h.id, { status_locked: false }),
    onSuccess: reload,
  });
  const remove = useMutation({ mutationFn: (hid: number) => projectsApi.removeHypothesis(hid), onSuccess: reload });

  return (
    <div className="series-grid">
      <section className="panel series-panel">
        <h3>Новая гипотеза</h3>
        <p className="muted series-hint">
          Клипы с тегом сравниваются с остальными клипами проекта по медиане. Пока в группе меньше N клипов с
          числами — «мало данных».
        </p>
        <label className="field">
          <span>Утверждение</span>
          <input className="input" placeholder="Хук-вопрос в первые 2 с даёт больше просмотров" value={form.statement} onChange={(e) => setForm({ ...form, statement: e.target.value })} />
        </label>
        <div className="series-row">
          <input className="input wide" list="series-tags" placeholder="тег группы, например hook_question" value={form.tag} onChange={(e) => setForm({ ...form, tag: e.target.value })} />
          <datalist id="series-tags">
            {knownTags.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
          <select className="input" value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })}>
            {METRIC_OPTIONS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          <input className="input series-num" title="Минимум клипов в каждой группе" value={form.min_n} onChange={(e) => setForm({ ...form, min_n: e.target.value })} />
        </div>
        <label className="field">
          <span>Правило, если подтвердится (необязательно)</span>
          <input className="input" value={form.rule_text} onChange={(e) => setForm({ ...form, rule_text: e.target.value })} />
        </label>
        <button className="btn primary" disabled={!form.statement.trim() || !form.tag.trim() || add.isPending} onClick={() => add.mutate()}>
          Добавить
        </button>
      </section>
      <section className="panel series-panel">
        <div className="series-panel-head">
          <h3>Гипотезы</h3>
          <button className="btn sm" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
            {evaluate.isPending ? "Считаю…" : "Пересчитать"}
          </button>
        </div>
        {hyps.isLoading ? <Loading /> : null}
        {!hyps.isLoading && !(hyps.data ?? []).length ? (
          <EmptyState icon="🧪" title="Гипотез пока нет" hint="Агент предлагает их сам (add_hypothesis) или добавьте слева" />
        ) : null}
        <ul className="series-list">
          {(hyps.data ?? []).map((h) => (
            <li key={h.id}>
              <div className="series-li-main">
                <span className={`series-hyp series-hyp--${h.status}`}>{HYP_STATUS_LABEL[h.status]}</span>
                <strong>{h.statement}</strong>
                <span className="chip">{groupLabel(h.group)}</span>
                {h.status === "confirmed" ? (
                  <button className="btn sm" onClick={() => promote.mutate(h.id)}>
                    Сделать правилом
                  </button>
                ) : null}
                {h.status_locked ? (
                  <button className="btn ghost sm" title="Статус выставлен вручную — снова считать по числам" onClick={() => unlock.mutate(h)}>
                    🔒
                  </button>
                ) : null}
                <button className="btn ghost sm" title="Удалить" onClick={() => remove.mutate(h.id)}>
                  ✕
                </button>
              </div>
              <div className="muted series-li-sub">
                {h.conclusion || "ещё не считалась"}
                {h.evaluated_at ? ` · ${formatSnapshotTime(h.evaluated_at)}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
