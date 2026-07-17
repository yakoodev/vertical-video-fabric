import { api } from "@/api/client";
import type { AiSegment, FocusPoint } from "@/api/types";

export const segmentsApi = {
  patchTimecodes: (segmentId: number, start_sec: number, end_sec: number) =>
    api.patch<AiSegment>(`/api/segments/${segmentId}/timecodes`, { start_sec, end_sec }),
  setFocus: (segmentId: number, focus: FocusPoint[]) =>
    api.patch<AiSegment>(`/api/segments/${segmentId}/focus`, { focus }),
  // Detector (faces/motion/content) — fills focus for many segments.
  // use_vlm additionally asks Gemini for the framing of each detected shot.
  autofocus: (sourceId: number | string, segmentIds: number[], useVlm = false) =>
    api.post<{ updated: number }>(`/api/sources/${sourceId}/autofocus`, {
      segment_ids: segmentIds,
      use_vlm: useVlm,
    }),
  // Run the detector for a single segment; returns the updated segment.
  autofocusOne: (segmentId: number, useVlm = false) =>
    api.post<AiSegment>(`/api/segments/${segmentId}/autofocus`, { use_vlm: useVlm }),
};
