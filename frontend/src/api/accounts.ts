import { api } from "@/api/client";
import type { Account } from "@/api/types";

export interface AccountCreate {
  platform: string;
  label: string;
  cookie: string;
  proxy_url?: string;
  /** Убрать прокси у аккаунта (пустой proxy_url прежний прокси оставляет). */
  clear_proxy?: boolean;
}

export const accountsApi = {
  list: () => api.get<Account[]>("/api/accounts"),
  create: (body: AccountCreate) => api.post<Account>("/api/accounts", body),
  /** Только прокси, без повторной вставки cookie; пустая строка убирает прокси. */
  setProxy: (id: number, proxy_url: string) => api.put<Account>(`/api/accounts/${id}/proxy`, { proxy_url }),
  /** Канал YouTube для загрузки (UC… или @ник); пустая строка — канал по умолчанию у входа. */
  setChannel: (id: number, channel: string) => api.put<Account>(`/api/accounts/${id}/channel`, { channel }),
  /** Браузер аккаунта держит вход: true/false; null — по умолчанию (включён, если у аккаунта свой прокси). */
  setKeepalive: (id: number, enabled: boolean | null) =>
    api.put<Account>(`/api/accounts/${id}/keepalive`, { enabled }),
  remove:(id: number | string) => api.del<{ deleted: boolean }>(`/api/accounts/${id}`),
};
