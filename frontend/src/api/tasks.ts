import { api } from "@/api/client";
import type { ActiveTask } from "@/api/types";

export const tasksApi = {
  active: () => api.get<ActiveTask[]>("/api/tasks/active"),
  recent: (limit = 80) => api.get<ActiveTask[]>(`/api/tasks?limit=${limit}`),
};
