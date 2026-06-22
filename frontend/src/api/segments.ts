import { api } from "@/api/client";
import type { AiSegment, FocusPoint } from "@/api/types";

export const segmentsApi = {
  patchTimecodes: (segmentId: number, start_sec: number, end_sec: number) =>
    api.patch<AiSegment>(`/api/segments/${segmentId}/timecodes`, { start_sec, end_sec }),
  setFocus: (segmentId: number, focus: FocusPoint[]) =>
    api.patch<AiSegment>(`/api/segments/${segmentId}/focus`, { focus }),
  // Deterministic detector (faces/motion) — fills focus for many segments.
  autofocus: (sourceId: number | string, segmentIds: number[]) =>
    api.post<{ updated: number }>(`/api/sources/${sourceId}/autofocus`, { segment_ids: segmentIds }),
  // Run the detector for a single segment; returns the updated segment.
  autofocusOne: (segmentId: number) => api.post<AiSegment>(`/api/segments/${segmentId}/autofocus`),
};
