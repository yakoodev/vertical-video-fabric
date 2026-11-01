import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { modulesApi, modulesKey } from "@/api/modules";

/**
 * Какие модули сервиса включены (app/modules.py). Пока ответа нет — «включено»: поведение как
 * до модулей, ничего не мигает. Выключенный модуль прячет свои пункты меню, панели и кнопки.
 */
export function useModules() {
  const q = useQuery({ queryKey: modulesKey, queryFn: modulesApi.get, staleTime: 30_000 });
  const on = useCallback(
    (id: string) => {
      const mod = q.data?.modules.find((m) => m.id === id);
      return mod ? mod.enabled : true;
    },
    [q.data],
  );
  // Для фоновых опросов и целых страниц: «включён» только по ответу сервера (иначе первый
  // запрос выключенного модуля успел бы уйти и получить 423). Сервер не ответил — как раньше.
  const ready = useCallback((id: string) => (q.data || q.isError ? on(id) : false), [q.data, q.isError, on]);
  return { on, ready, data: q.data, isLoading: q.isLoading };
}

/** Один модуль: true, пока не пришёл ответ или модуль включён. */
export function useModuleOn(id: string): boolean {
  return useModules().on(id);
}
