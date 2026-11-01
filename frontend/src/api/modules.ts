import { api } from "@/api/client";

/** Модули сервиса (app/modules.py): что можно выключить в «Настройки → Модули». */
export type ModuleCost = "cpu" | "ram" | "gpu" | "network" | "paid";

export interface ServiceModule {
  id: string;
  name: string;
  group: string;
  description: string;
  cost: ModuleCost[];
  cost_note: string;
  depends: string[];
  dependents: string[];
  default: boolean;
  /** Работает: свой флаг включён и все зависимости работают. */
  enabled: boolean;
  /** Свой флаг (без учёта зависимостей). */
  flag: boolean;
  /** Выключенные зависимости, из-за которых модуль не работает. */
  blocked_by: string[];
  /** "env" — задан MODULES_DISABLED/MODULES_ENABLED, в UI не меняется. */
  locked: "env" | null;
  tools: string[];
  routes: string[];
  ui: string[];
  workers: string[];
}

export interface ModuleResources {
  rss_mb: number | null;
  threads: number;
  workers: { name: string; module: string; module_enabled: boolean; running: boolean }[];
  heavy: { limit?: number; running?: { kind: string; label: string }[]; waiting?: number; [k: string]: unknown } | null;
  whisper_loaded: string[];
}

export interface ModulesView {
  groups: { id: string; name: string }[];
  modules: ServiceModule[];
  env: { disabled: string; enabled: string };
  resources: ModuleResources;
}

export interface ModulesChange {
  changed: Record<string, boolean>;
  also_off: string[];
  also_on: string[];
  locked: string[];
  needs_confirm?: boolean;
  detail?: string;
}

export const modulesKey = ["modules"] as const;

export const modulesApi = {
  get: () => api.get<ModulesView>("/api/modules"),
  set: (modules: Record<string, boolean>, confirm = false) =>
    api.put<ModulesView & ModulesChange>("/api/modules", { modules, confirm }),
  dryRun: (modules: Record<string, boolean>) =>
    api.put<ModulesChange & { dry_run: true }>("/api/modules", { modules, dry_run: true }),
};
