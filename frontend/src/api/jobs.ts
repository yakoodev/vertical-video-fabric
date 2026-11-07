import { api } from "@/api/client";
import type { Job } from "@/api/types";

export const jobsApi = {
  list: () => api.get<Job[]>("/api/jobs"),
  get: (id: number | string) => api.get<Job>(`/api/jobs/${id}`),
  cancel: (id: number | string) => api.post<Job>(`/api/jobs/${id}/cancel`),
};
