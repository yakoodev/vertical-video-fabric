/**
 * Режим «Смотреть монтаж»: плеер на весь экран поверх чего угодно. Сам просмотр — MontagePlayer,
 * его же можно встроить на любой экран (`<MontagePlayer clipPlanId={id} />`).
 */
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { MontagePlayer } from "./MontagePlayer";

export function WatchMontage({ clipPlanId, title, onClose }: { clipPlanId: number; title?: string; onClose: () => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  // Открыли кнопкой — можно сразу на настоящий весь экран; браузер не дал — остаётся полное окно.
  useEffect(() => {
    const el = rootRef.current;
    el?.requestFullscreen?.().catch(() => undefined);
    return () => {
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    };
  }, []);
  return createPortal(
    <div ref={rootRef} className="mwatch modal-backdrop" role="dialog" aria-modal="true" aria-label="Смотреть монтаж">
      <MontagePlayer clipPlanId={clipPlanId} autoPlay onClose={onClose} rootRef={rootRef} title={title} className="mplayer--watch" />
    </div>,
    document.body,
  );
}
