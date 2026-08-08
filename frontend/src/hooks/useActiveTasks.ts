import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { tasksApi } from "@/api/tasks";
import { qk } from "@/api/keys";
import type { ActiveTask } from "@/api/types";
import { useToast } from "@/components/Toast";

const ACTIVE = new Set(["queued", "running", "rendering", "analyzing", "downloading", "cancelling", "scheduling"]);
const TERMINAL = new Set(["succeeded", "failed", "needs_reauth", "done", "error", "cancelled"]);

export function isActive(status: string): boolean {
  return ACTIVE.has(status);
}
export function isTerminal(status: string): boolean {
  return TERMINAL.has(status);
}

const KIND_RU: Record<string, string> = {
  job: "Публикация",
  clip: "Клип",
  analysis: "Анализ",
  download: "Скачивание",
};

function taskKey(t: ActiveTask): string {
  return `${t.kind}:${t.id}`;
}

// Single polling subscription that also fires exactly-once toasts and refreshes
// affected lists when a task reaches a terminal state. Server is the source of
// truth for "active"; toasts are deduped by kind:id:updated_at.
export function useActiveTasks() {
  const qc = useQueryClient();
  const toast = useToast();
  const prevStatus = useRef<Map<string, string>>(new Map());

  const query = useQuery({
    queryKey: qk.activeTasks,
    queryFn: tasksApi.active,
    refetchInterval: (q) => {
      const data = q.state.data as ActiveTask[] | undefined;
      return data?.some((t) => isActive(t.status)) ? 2000 : 8000;
    },
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    const tasks = query.data;
    if (!tasks) return;
    let sawTerminal = false;
    for (const t of tasks) {
      const key = taskKey(t);
      const before = prevStatus.current.get(key);
      prevStatus.current.set(key, t.status);
      if (before === undefined) continue; // first observation, don't toast history
      if (before === t.status) continue;
      if (isTerminal(t.status) && !isTerminal(before)) {
        sawTerminal = true;
        // The user pressed cancel themselves — no need to announce it back.
        if (t.status === "cancelled") continue;
        const name = `${KIND_RU[t.kind] ?? t.kind} #${t.id}`;
        const what = t.label ? `${name} · ${t.label}` : name;
        const dedupe = `${key}:${t.updated_at}`;
        if (t.status === "succeeded" || t.status === "done") {
          toast.success(`${what} — готово`, dedupe);
        } else if (t.status === "needs_reauth") {
          toast.error(`${what} — нужна переавторизация аккаунта`, dedupe);
        } else {
          toast.error(`${what} — ошибка: ${t.error || "неизвестно"}`, dedupe);
        }
      }
    }
    if (sawTerminal) {
      qc.invalidateQueries({ queryKey: qk.clips() });
      qc.invalidateQueries({ queryKey: qk.jobs });
      qc.invalidateQueries({ queryKey: qk.sources });
      qc.invalidateQueries({ queryKey: qk.recentTasks });
    }
  }, [query.data, qc, toast]);

  return query;
}
