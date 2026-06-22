import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { accountsApi, type AccountCreate } from "@/api/accounts";
import { qk } from "@/api/keys";
import type { Account } from "@/api/types";
import { ApiError } from "@/api/client";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { useToast } from "@/components/Toast";
import { Badge, ErrorState, Loading } from "@/components/ui";

export function AccountsSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list });
  const [platform, setPlatform] = useState("youtube");
  const [label, setLabel] = useState("");
  const [proxy, setProxy] = useState("");
  const [cookie, setCookie] = useState("");

  const create = useMutation({
    mutationFn: (body: AccountCreate) => accountsApi.create(body),
    onSuccess: () => {
      toast.success("Аккаунт сохранён");
      setLabel("");
      setProxy("");
      setCookie("");
      qc.invalidateQueries({ queryKey: qk.accounts });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить аккаунт"),
  });

  const del = useDeleteMutation<Account>({
    listKey: qk.accounts,
    mutationFn: accountsApi.remove,
    successMessage: () => "Аккаунт удалён",
  });

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <div className="panel" style={{ display: "grid", gap: 12, maxWidth: 640 }}>
        <strong>Добавить аккаунт</strong>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: 1, minWidth: 140 }}>
            <span>Платформа</span>
            <select className="input" value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="youtube">youtube</option>
              <option value="tiktok">tiktok</option>
              <option value="instagram">instagram</option>
            </select>
          </label>
          <label className="field" style={{ flex: 2, minWidth: 180 }}>
            <span>Метка</span>
            <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="main" />
          </label>
        </div>
        <label className="field">
          <span>Прокси (необязательно)</span>
          <input
            className="input"
            value={proxy}
            onChange={(e) => setProxy(e.target.value)}
            placeholder="http://user:pass@host:port"
          />
        </label>
        <label className="field">
          <span>Cookies</span>
          <textarea
            className="input"
            rows={4}
            value={cookie}
            onChange={(e) => setCookie(e.target.value)}
            placeholder="Вставьте экспортированные cookies (Netscape или JSON)"
          />
        </label>
        <div>
          <button
            className="btn primary"
            disabled={create.isPending || !label.trim() || !cookie.trim()}
            onClick={() => create.mutate({ platform, label: label.trim(), cookie: cookie.trim(), proxy_url: proxy.trim() || undefined })}
          >
            {create.isPending ? "Сохранение…" : "Сохранить аккаунт"}
          </button>
        </div>
      </div>

      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !query.data?.length ? (
        <span className="muted">Аккаунтов пока нет</span>
      ) : (
        <div className="card-grid">
          {query.data.map((acc) => (
            <div key={acc.id} className="panel" style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>
                  {acc.platform} · {acc.label}
                </strong>
                <Badge status={acc.has_required_cookies ? "ok" : "needs_reauth"}>
                  {acc.has_required_cookies ? "готов" : "re-auth"}
                </Badge>
              </div>
              <div className="muted" style={{ fontSize: 13, display: "grid", gap: 3 }}>
                <span>cookies: {acc.cookie_count}</span>
                <span>прокси: {acc.proxy_display}</span>
                {acc.missing_cookies ? <span style={{ color: "var(--warn)" }}>не хватает: {acc.missing_cookies}</span> : null}
              </div>
              <div>
                <button className="btn ghost sm" onClick={() => del.mutate(acc.id)}>
                  Удалить
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
