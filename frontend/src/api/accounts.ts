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
  remove: (id: number | string) => api.del<{ deleted: boolean }>(`/api/accounts/${id}`),
};
