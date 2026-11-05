import { lazy, type ComponentType } from "react";

const RELOADED = "vvf-chunk-reload";

function reloadedOnce(): boolean {
  try {
    if (sessionStorage.getItem(RELOADED)) return false;
    sessionStorage.setItem(RELOADED, "1");
    return true;
  } catch {
    return false;
  }
}

/**
 * Страница отдельным файлом. Вкладка, открытая до выкладки, просит файл старой сборки, которого уже нет:
 * один раз перезагружаемся на новую сборку; не помогло — ошибка видна как обычно.
 */
export function lazyPage<P extends object>(load: () => Promise<Record<string, unknown>>, name: string) {
  return lazy(async () => {
    try {
      const module = await load();
      try {
        sessionStorage.removeItem(RELOADED);
      } catch {
        // без sessionStorage просто не помним про перезагрузку
      }
      return { default: module[name] as ComponentType<P> };
    } catch (error) {
      if (reloadedOnce()) {
        window.location.reload();
        return new Promise<never>(() => {});
      }
      throw error;
    }
  });
}
