import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RULE_KIND_LABEL, projectsApi, type AddRuleResult, type Rule, type RuleKind } from "@/api/projects";
import { formatSnapshotTime } from "@/api/stats";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";

/** Подвкладка «Правила» в «Лор и правила»: правила владельца с цитатой и историей. */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const today = () => new Date().toISOString().slice(0, 10);

export function ProjectRulesTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [history, setHistory] = useState(false);
  const status = history ? "all" : "active";
  const rules = useQuery({ queryKey: qk.channelRules(projectId, status), queryFn: () => projectsApi.rules(projectId, status) });
  const [form, setForm] = useState({ text: "", kind: "do" as RuleKind, scope: "project" as "project" | "global", quote: "", said_at: today() });
  const [pending, setPending] = useState<AddRuleResult | null>(null);
  const [revoking, setRevoking] = useState<Rule | null>(null);
  const [reason, setReason] = useState("");

  const refresh = () => qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId), "rules"] });
  const add = useMutation({
    mutationFn: (extra: { replaces?: number[]; force?: boolean }) =>
      projectsApi.addRule({ ...form, project_id: projectId, origin: "ui", ...extra }),
    onSuccess: (res) => {
      if (res.created) {
        toast.success("Правило записано");
        setForm((f) => ({ ...f, text: "", quote: "" }));
        setPending(null);
        refresh();
      } else if (res.needs_confirmation) {
        setPending(res);
      } else {
        toast.push(res.note || "Такое правило уже есть", "info");
      }
    },
    onError: (e) => toast.error(errText(e, "Не удалось записать правило")),
  });
  const revoke = useMutation({
    mutationFn: () => projectsApi.revokeRule(revoking!.id, reason),
    onSuccess: () => {
      setRevoking(null);
      setReason("");
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не удалось отменить")),
  });

  const list = rules.data ?? [];
  return (
    <div className="series-grid">
      <section className="panel series-panel">
        <h3>Новое правило</h3>
        <p className="muted series-hint">
          Из ответа владельца — с цитатой и датой. Похожее уже есть — не продублируется; противоречит старому —
          спросим, что делать.
        </p>
        <label className="field">
          <span>Правило</span>
          <input className="input" value={form.text} placeholder="Не брать политику" onChange={(e) => setForm({ ...form, text: e.target.value })} />
        </label>
        <div className="series-row">
          <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as RuleKind })}>
            {(Object.keys(RULE_KIND_LABEL) as RuleKind[]).map((k) => (
              <option key={k} value={k}>
                {RULE_KIND_LABEL[k]}
              </option>
            ))}
          </select>
          <select className="input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value as "project" | "global" })}>
            <option value="project">Для этого проекта</option>
            <option value="global">Для всех проектов</option>
          </select>
          <input className="input" type="date" value={form.said_at} onChange={(e) => setForm({ ...form, said_at: e.target.value })} />
        </div>
        <label className="field">
          <span>Цитата владельца</span>
          <textarea className="input" rows={2} value={form.quote} onChange={(e) => setForm({ ...form, quote: e.target.value })} />
        </label>
        <button className="btn primary" disabled={!form.text.trim() || add.isPending} onClick={() => add.mutate({})}>
          Записать
        </button>
        {pending ? (
          <div className="series-conflict">
            <strong>Противоречит действующему правилу</strong>
            {pending.conflicts.map((c) => (
              <p key={c.id}>
                #{c.id} «{c.text}»{c.said_at ? ` от ${c.said_at.slice(0, 10)}` : ""}
              </p>
            ))}
            <p className="muted">Заменить старое новым или оставить оба?</p>
            <div className="series-row">
              <button className="btn primary sm" onClick={() => add.mutate({ replaces: pending.conflicts.map((c) => c.id) })}>
                Заменить старое
              </button>
              <button className="btn sm" onClick={() => add.mutate({ force: true })}>
                Оставить оба
              </button>
              <button className="btn ghost sm" onClick={() => setPending(null)}>
                Отмена
              </button>
            </div>
          </div>
        ) : null}
      </section>
      <section className="panel series-panel">
        <div className="series-panel-head">
          <h3>{history ? "Все правила и история" : "Действующие правила"}</h3>
          <label className="check">
            <input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} /> история
          </label>
        </div>
        {rules.isLoading ? <Loading /> : null}
        {!rules.isLoading && !list.length ? <p className="muted">Правил пока нет.</p> : null}
        <ul className="series-list">
          {list.map((r) => (
            <li key={r.id} className={r.status === "active" ? "" : "series-li-off"}>
              <div className="series-li-main">
                <span className={`series-kind series-kind--${r.kind}`}>{RULE_KIND_LABEL[r.kind]}</span>
                <strong>{r.text}</strong>
                {r.scope === "global" ? <span className="chip">все проекты</span> : null}
                {r.needs_review ? <span className="chip series-review" title={r.review_note}>на пересмотр</span> : null}
                {r.status === "active" ? (
                  <button className="btn ghost sm" onClick={() => setRevoking(r)}>
                    Отменить
                  </button>
                ) : null}
              </div>
              {r.quote ? <blockquote className="series-quote">«{r.quote}»</blockquote> : null}
              <div className="muted series-li-sub">
                #{r.id} · {r.said_at.slice(0, 10)}
                {r.origin ? ` · ${r.origin}` : ""}
                {r.status === "revoked" ? ` · отменено ${formatSnapshotTime(r.revoked_at)}: ${r.revoke_reason || "без причины"}` : ""}
                {r.status === "superseded" ? ` · заменено правилом #${r.superseded_by}` : ""}
                {r.needs_review ? ` · ${r.review_note}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </section>
      <ConfirmDialog
        open={Boolean(revoking)}
        title="Отменить правило?"
        confirmLabel="Отменить правило"
        cancelLabel="Не отменять"
        body={
          <>
            <p>«{revoking?.text}» останется в истории, но агент перестанет его соблюдать.</p>
            <input className="input" autoFocus placeholder="Почему (например: пересмотрел, не работает)" value={reason} onChange={(e) => setReason(e.target.value)} />
          </>
        }
        busy={revoke.isPending}
        onCancel={() => setRevoking(null)}
        onConfirm={() => revoke.mutate()}
      />
    </div>
  );
}
