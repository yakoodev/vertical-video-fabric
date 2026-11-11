import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, request } from "@/api/client";
import { useToast } from "@/components/Toast";

interface ProxyStatus {
  configured: boolean;
  source: "service" | "env" | "";
  masked: string;
  expires_on: string;
  days_left: number | null;
  for_gemini: boolean;
}
type ProxyTest = Record<string, { ok: boolean; status?: number; ms?: number; error?: string }>;

const proxyApi = {
  get: () => api.get<ProxyStatus>("/api/settings/proxy"),
  save: (body: { url: string; expires_on: string; for_gemini: boolean }) =>
    request<ProxyStatus>("/api/settings/proxy", { method: "PUT", body }),
  clear: () => api.del<ProxyStatus>("/api/settings/proxy"),
  test: (url: string) => api.post<ProxyTest>("/api/settings/proxy/test", { url }),
};

interface DownloadAccountStatus {
  account_id: number | null;
  label: string;
  own_proxy: boolean;
  accounts: { id: number; label: string; has_required_cookies: boolean }[];
}

const downloadAccountApi = {
  get: () => api.get<DownloadAccountStatus>("/api/settings/download-account"),
  save: (account_id: number | null) =>
    request<DownloadAccountStatus>("/api/settings/download-account", { method: "PUT", body: { account_id } }),
};

const TEST_LABELS: Record<string, string> = { twitch_vod: "Видео Twitch (VOD)", google: "Google / Gemini" };

/**
 * Прокси сервиса: когда провайдер режет видео-CDN Twitch (или Google). Задаётся
 * здесь, а не в .env — купленный прокси «протухает», его меняют руками. Загрузки
 * идут через него, только если напрямую не вышло; Gemini — только при геоблоке.
 */
export function ProxySettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["proxy-settings"], queryFn: proxyApi.get });
  const [url, setUrl] = useState("");
  const [expires, setExpires] = useState<string | null>(null);
  const [forGemini, setForGemini] = useState<boolean | null>(null);
  const [tested, setTested] = useState<ProxyTest | null>(null);
  const data = status.data;
  const expiresValue = expires ?? data?.expires_on ?? "";
  const geminiValue = forGemini ?? data?.for_gemini ?? true;

  const onErr = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Не получилось");
  const save = useMutation({
    mutationFn: () => proxyApi.save({ url: url.trim(), expires_on: expiresValue, for_gemini: geminiValue }),
    onSuccess: (s) => {
      qc.setQueryData(["proxy-settings"], s);
      setUrl("");
      toast.success("Прокси сохранён");
    },
    onError: onErr,
  });
  const clear = useMutation({
    mutationFn: proxyApi.clear,
    onSuccess: (s) => {
      qc.setQueryData(["proxy-settings"], s);
      setTested(null);
      toast.success("Прокси убран");
    },
    onError: onErr,
  });
  const test = useMutation({
    mutationFn: () => proxyApi.test(url.trim()),
    onSuccess: setTested,
    onError: onErr,
  });

  const expired = data?.days_left != null && data.days_left < 0;
  const soon = data?.days_left != null && data.days_left >= 0 && data.days_left <= 3;

  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 640 }}>
      <div className="panel" style={{ display: "grid", gap: 12 }}>
        <strong>Прокси для скачивания</strong>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Когда провайдер режет видео записей Twitch (или Google), сервис повторяет загрузку через этот прокси.
          Сначала всегда пробует напрямую — прокси тратится только когда без него никак.
        </p>
        <div style={{ fontSize: 13 }}>
          {data?.configured ? (
            <>
              Сейчас: <span className="mono">{data.masked}</span>
              {data.source === "env" ? <span className="muted"> · из .env</span> : null}
              {data.expires_on ? (
                <span style={{ color: expired ? "var(--danger)" : soon ? "var(--warn)" : undefined }}>
                  {" "}· {expired ? "протух" : "до"} {data.expires_on}
                  {data.days_left != null && !expired ? ` (${data.days_left} дн.)` : ""}
                </span>
              ) : null}
            </>
          ) : (
            <span className="muted">Не задан — всё качается напрямую.</span>
          )}
        </div>
        <input
          className="input mono"
          placeholder={data?.configured ? "новый адрес (пусто — оставить текущий)" : "логин:пароль:хост:порт или http://логин:пароль@хост:порт"}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          autoComplete="off"
        />
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <label className="field" style={{ margin: 0 }}>
            <span>Действует до</span>
            <input className="input" type="date" value={expiresValue} onChange={(e) => setExpires(e.target.value)} />
          </label>
          <label className="check">
            <input type="checkbox" checked={geminiValue} onChange={(e) => setForGemini(e.target.checked)} />
            <span>И для Gemini (если Google режет)</span>
          </label>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className="btn primary"
            disabled={save.isPending || (!url.trim() && !data?.configured)}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
          <button
            className="btn"
            disabled={test.isPending || (!url.trim() && !data?.configured)}
            onClick={() => test.mutate()}
            title="Проверить, открываются ли через прокси видео Twitch и Google"
          >
            {test.isPending ? "Проверяю…" : "Проверить"}
          </button>
          {data?.source === "service" ? (
            <button className="btn ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
              Убрать
            </button>
          ) : null}
        </div>
        {tested ? (
          <div style={{ display: "grid", gap: 4, fontSize: 13 }}>
            {Object.entries(tested).map(([name, r]) => (
              <div key={name} style={{ color: r.ok ? "var(--ok, #22c55e)" : "var(--danger)" }}>
                {r.ok ? "✓" : "✗"} {TEST_LABELS[name] ?? name}
                {r.ok ? ` — ${r.ms} мс` : ` — ${r.error || `ответ ${r.status}`}`}
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <DownloadAccountPanel />
    </div>
  );
}

/**
 * Аккаунт YouTube «для скачивания»: когда YouTube просит вход («подтвердите, что вы не бот» —
 * адрес прокси помечен), загрузка повторяется с его cookie. По умолчанию — никакой: аккаунты
 * публикаций сервис для скачивания сам не берёт, только если владелец отметит здесь.
 */
function DownloadAccountPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["download-account"], queryFn: downloadAccountApi.get });
  const save = useMutation({
    mutationFn: (id: number | null) => downloadAccountApi.save(id),
    onSuccess: (s) => {
      qc.setQueryData(["download-account"], s);
      toast.success(s.account_id ? `Для скачивания: ${s.label}` : "Аккаунт для скачивания не используется");
    },
    onError: (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Не получилось"),
  });
  const data = status.data;
  return (
    <div className="panel" style={{ display: "grid", gap: 12 }}>
      <strong>Аккаунт YouTube для скачивания</strong>
      <p className="muted" style={{ margin: 0, fontSize: 13 }}>
        Если YouTube отвечает «подтвердите, что вы не бот», сервис повторит загрузку с cookie этого аккаунта
        (через его прокси, если задан, иначе через прокси выше). Лучше завести отдельный аккаунт: Google может
        выкинуть из сессии, и с аккаунта публикаций пропадут заливки. Добавить аккаунт — в «Аккаунты».
      </p>
      <label className="field" style={{ margin: 0 }}>
        <span>Аккаунт</span>
        <select
          className="input"
          value={data?.account_id ?? ""}
          disabled={!data || save.isPending}
          onChange={(e) => save.mutate(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">Не использовать (по умолчанию)</option>
          {(data?.accounts ?? []).map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
              {a.has_required_cookies ? "" : " — cookie неполные"}
            </option>
          ))}
        </select>
      </label>
      {data && data.accounts.length === 0 ? (
        <span className="muted" style={{ fontSize: 13 }}>Аккаунтов YouTube пока нет.</span>
      ) : null}
    </div>
  );
}
