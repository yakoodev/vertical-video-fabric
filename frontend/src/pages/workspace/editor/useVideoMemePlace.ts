import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { type ClipPlan } from "@/api/types";
import { videoMemesApi } from "@/api/videoMemes";
import { useToast } from "@/components/Toast";
import type { Selection } from "@/pages/workspace/ClipTimeline";

/** «Врезка» — во весь кадр со звуком мема (звук клипа выключен), «Окно» — поверх клипа, клип тише. */
export type MemeMode = "full" | "pip";
/** Ширина окошка по умолчанию: заметно, но игра видна (сервер ещё поднимет его над субтитрами). */
export const MEME_PIP_SCALE = 0.55;
/** Перетаскивание карточки на дорожку «Вставки»: {id, mode}. */
export const MEME_DRAG_TYPE = "application/x-video-meme";

/**
 * Видеомем руками владельца: тот же place_video_meme, что у агента (POST …/video-memes/place), поэтому звук
 * (level speech, duck_volume) и проверки одинаковые. Как другие серверные правки: сначала дописать панель,
 * после — перечитать клип; шаг отмены — один («↩ Шаг назад» / Ctrl+Z).
 */
export function useVideoMemePlace({
  sourceId, activePlan, flushSettings, setSettingsNonce, setTlSelected,
}: {
  sourceId: string;
  activePlan: ClipPlan | undefined;
  flushSettings: () => Promise<void>;
  setSettingsNonce: React.Dispatch<React.SetStateAction<number>>;
  setTlSelected: (sel: Selection) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const place = useMutation({
    mutationFn: async ({ memeId, mode, at }: { memeId: number; mode: MemeMode; at: number }) => {
      if (!activePlan) throw new ApiError(400, "клип не открыт");
      await flushSettings();
      return videoMemesApi.place(activePlan.id, {
        meme_id: memeId,
        at: Math.max(0, Number(at.toFixed(2))),
        mode,
        ...(mode === "pip" ? { scale: MEME_PIP_SCALE } : {}),
      });
    },
    onSuccess: async (r, { at }) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      // Мем мог прийти в библиотеку после открытия редактора — дорожке нужен его файл (подпись, кадр).
      void qc.invalidateQueries({ queryKey: qk.montageAssets });
      setSettingsNonce((n) => n + 1);
      const index = r.inserts.findIndex((ins) => Math.abs(ins.at - at) < 0.02 && ins.mode === r.meme.mode);
      if (index >= 0) setTlSelected({ kind: "insert", index });
      const how = r.meme.mode === "full" ? "врезка во весь кадр" : "окошко";
      toast.success(`«${r.meme.title}» на ${at.toFixed(1)} с — ${how}, ${r.meme.duration.toFixed(1)} с · Ctrl+Z откатит`);
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Видеомем не встал"),
  });
  const placeVideoMeme = (memeId: number, mode: MemeMode, at: number) => {
    if (!place.isPending) place.mutate({ memeId, mode, at });
  };
  return { placeVideoMeme, placingMeme: place.isPending };
}
