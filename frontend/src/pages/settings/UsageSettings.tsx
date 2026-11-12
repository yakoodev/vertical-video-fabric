import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { usageApi, usageKeys, type UsageGroupBy, type UsageSums } from "@/api/usage";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { localDayStart, requestsWord, rub, tokens, usd } from "@/lib/usage";

/** «Настройки → Траты Gemini»: сегодня / 7 дней / 30 дней, по назначению, по модели, последние запросы, цены. */

const PURPOSE_LABEL: Record<string, string> = {
  agent_chat: "Чат с агентом",
  clip_chat: "Чат клипа (ИИ-монтажёр)",
  montage_agent: "ИИ-монтаж без чата",
  analysis: "Анализ видео",
  chat_summary: "Резюме переписки",
  explainer_script: "Объяснялки",
  stories_script: "Истории",
  separate_voices: "Разделение голосов",
  subtitles: "Субтитры",
  publish_meta: "Заголовки и описания",
  tts: "Озвучка",
  montage: "ИИ-монтаж (план)",
  pick: "ИИ-выбор лучших",
};

function Totals({ title, since }: { title: string; since: string }) {
  const q = useQuery({ queryKey: usageKeys.totals({ since }), queryFn: () => usageApi.totals({ since }) });
  const t = q.data?.total;
  return (
    <div className="panel" style={{ display: "grid", gap: 4, minWidth: 150 }}>
      <span className="muted" style={{ fontSize: 12.5 }}>{title}</span>
      <strong style={{ fontSize: 22, fontVariantNumeric: "tabular-nums" }}>{t ? rub(t.cost_rub) : "…"}</strong>
      <span className="muted" style={{ fontSize: 12 }}>
        {t ? `${t.calls} ${requestsWord(t.calls)} · ${tokens(t.total_tokens)} ток. · ${usd(t.cost_usd)}` : ""}
        {t?.errors ? ` · ошибок ${t.errors}` : ""}
      </span>
    </div>
  );
}

function GroupTable({ title, groupBy, since, label }: {
  title: string;
  groupBy: UsageGroupBy;
  since: string;
  label?: (key: string) => string;
}) {
  const q = useQuery({
    queryKey: usageKeys.totals({ since, groupBy }),
    queryFn: () => usageApi.totals({ since, group_by: groupBy }),
  });
  const rows = q.data?.groups ?? [];
  return (
    <div className="panel" style={{ display: "grid", gap: 8 }}>
      <strong>{title}</strong>
      {q.isLoading ? <Loading /> : !rows.length ? <span className="muted">Запросов не было.</span> : (
        <table className="table">
          <thead>
            <tr>
              <th />
              <th style={{ textAlign: "right" }}>Запросы</th>
              <th style={{ textAlign: "right" }}>Вход</th>
              <th style={{ textAlign: "right" }}>Кэш</th>
              <th style={{ textAlign: "right" }}>Выход + мысли</th>
              <th style={{ textAlign: "right" }}>₽</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r: UsageSums & { key: string | number | null }) => {
              const key = r.key == null || r.key === "" ? "—" : String(r.key);
              return (
                <tr key={key}>
                  <td title={key}>{label ? label(key) : key}</td>
                  <td style={{ textAlign: "right" }}>{r.calls}{r.errors ? <span className="muted"> ({r.errors} ош.)</span> : null}</td>
                  <td style={{ textAlign: "right" }}>{tokens(r.prompt_tokens)}</td>
                  <td style={{ textAlign: "right" }}>{tokens(r.cached_tokens)}</td>
                  <td style={{ textAlign: "right" }}>{tokens(r.output_tokens)} + {tokens(r.thinking_tokens)}</td>
                  <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }} title={usd(r.cost_usd)}>
                    {rub(r.cost_rub)}{r.unpriced ? <span className="muted" title="модели нет в таблице цен"> *</span> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RecentCalls() {
  const q = useQuery({ queryKey: usageKeys.calls({ limit: 25 }), queryFn: () => usageApi.calls({ limit: 25 }) });
  const rows = q.data?.calls ?? [];
  return (
    <details className="panel">
      <summary style={{ cursor: "pointer" }}><strong>Последние запросы</strong></summary>
      {q.isLoading ? <Loading /> : (
        <table className="table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Когда (UTC)</th>
              <th>Назначение</th>
              <th>Модель</th>
              <th>Сессия</th>
              <th style={{ textAlign: "right" }}>Токены</th>
              <th style={{ textAlign: "right" }}>с</th>
              <th style={{ textAlign: "right" }}>₽</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id}>
                <td>{c.created_at.slice(5, 16)}</td>
                <td title={c.caller}>{PURPOSE_LABEL[c.purpose] ?? c.purpose}</td>
                <td>{c.model}</td>
                <td>{c.session || "—"}</td>
                <td style={{ textAlign: "right" }}>{c.total_tokens == null ? "—" : tokens(c.total_tokens)}</td>
                <td style={{ textAlign: "right" }}>{c.duration_ms == null ? "" : (c.duration_ms / 1000).toFixed(1)}</td>
                <td style={{ textAlign: "right" }} title={c.error || undefined}>
                  {c.ok ? (c.cost_rub == null ? "—" : rub(c.cost_rub)) : <span style={{ color: "var(--danger)" }}>ошибка</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}

function Prices() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: usageKeys.prices, queryFn: usageApi.prices });
  const [rate, setRate] = useState("");
  const [overrides, setOverrides] = useState("");
  useEffect(() => {
    if (!q.data) return;
    setRate(String(q.data.usd_rub));
    setOverrides(JSON.stringify(q.data.overrides ?? {}, null, 2));
  }, [q.data]);
  const err = (e: unknown, fallback: string) => toast.error(e instanceof ApiError ? e.message : fallback);
  const save = useMutation({
    mutationFn: () => {
      let parsed: Record<string, unknown>;
      try {
        parsed = overrides.trim() ? JSON.parse(overrides) : {};
      } catch {
        throw new ApiError(400, "Правки цен — не JSON");
      }
      return usageApi.setPrices({ overrides: parsed, usd_rub: Number(rate.replace(",", ".")) });
    },
    onSuccess: () => {
      toast.success("Цены сохранены. Новые запросы считаются по ним");
      qc.invalidateQueries({ queryKey: ["gemini-usage"] });
    },
    onError: (e) => err(e, "Не сохранилось"),
  });
  const recompute = useMutation({
    mutationFn: usageApi.recompute,
    onSuccess: (r) => {
      toast.success(`Пересчитано запросов: ${r.updated}`);
      qc.invalidateQueries({ queryKey: ["gemini-usage"] });
    },
    onError: (e) => err(e, "Не пересчиталось"),
  });
  if (q.isLoading || !q.data) return <Loading />;
  const models = Object.entries(q.data.prices).sort(([a], [b]) => a.localeCompare(b));
  return (
    <details className="panel">
      <summary style={{ cursor: "pointer" }}><strong>Цены и курс</strong></summary>
      <div style={{ display: "grid", gap: 12, marginTop: 8 }}>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          {q.data.units}, сверено {q.data.checked_at} по{" "}
          <a href={q.data.source} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>странице цен Google</a>.
          Размышления модели оплачиваются как выход. «проверить» — модели на странице нет, цена взята у ближайшей.
        </p>
        <table className="table">
          <thead>
            <tr>
              <th>Модель</th>
              <th style={{ textAlign: "right" }}>Вход</th>
              <th style={{ textAlign: "right" }}>Кэш</th>
              <th style={{ textAlign: "right" }}>Выход</th>
              <th>Ещё</th>
            </tr>
          </thead>
          <tbody>
            {models.map(([name, p]) => {
              const extra = Object.entries(p)
                .filter(([k]) => !["input", "cached", "output", "verify"].includes(k))
                .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`)
                .join("; ");
              return (
                <tr key={name}>
                  <td>{name}{p.verify ? <span className="muted"> · проверить</span> : null}</td>
                  <td style={{ textAlign: "right" }}>{String(p.input ?? "—")}</td>
                  <td style={{ textAlign: "right" }}>{String(p.cached ?? "—")}</td>
                  <td style={{ textAlign: "right" }}>{String(p.output ?? "—")}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{extra}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <label className="field" style={{ margin: 0, maxWidth: 200 }}>
          <span>Курс, ₽ за $</span>
          <input className="input" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Свои цены поверх таблицы (JSON: модель → input, cached, output…; null — убрать модель)</span>
          <textarea
            className="input"
            rows={6}
            style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 12.5 }}
            value={overrides}
            placeholder={'{"gemini-3.5-flash": {"input": 0.5, "cached": 0.05, "output": 3}}'}
            onChange={(e) => setOverrides(e.target.value)}
          />
        </label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
          <button className="btn" disabled={recompute.isPending} onClick={() => recompute.mutate()}
            title="Старые запросы посчитать по нынешним ценам и курсу">
            {recompute.isPending ? "Пересчитываю…" : "Пересчитать прошлые траты"}
          </button>
        </div>
      </div>
    </details>
  );
}

export function UsageSettings() {
  // Границы дня — по часам браузера, один раз при открытии страницы.
  const [bounds] = useState(() => ({ today: localDayStart(0), week: localDayStart(6), month: localDayStart(29) }));
  const purposeLabel = (k: string) => PURPOSE_LABEL[k] ?? k;
  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 960 }}>
      <p className="muted" style={{ margin: 0, fontSize: 13 }}>
        Каждый запрос сервиса к Gemini: токены и цена по таблице ниже. Траты переписки с агентом видны и в самом чате.
      </p>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <Totals title="Сегодня" since={bounds.today} />
        <Totals title="7 дней" since={bounds.week} />
        <Totals title="30 дней" since={bounds.month} />
      </div>
      <GroupTable title="За 7 дней по назначению" groupBy="purpose" since={bounds.week} label={purposeLabel} />
      <GroupTable title="За 7 дней по модели" groupBy="model" since={bounds.week} />
      <GroupTable title="По дням (UTC), 30 дней" groupBy="day" since={bounds.month} />
      <RecentCalls />
      <Prices />
    </div>
  );
}
