import { useCallback, useEffect, useRef, useState } from "react";

const STEP = 48;

/** Длинный список частями: сотни карточек с полями ввода не строятся разом. resetKey — смена фильтра. */
export function useShowMore<T>(items: T[], resetKey: unknown = null) {
  const [count, setCount] = useState(STEP);
  useEffect(() => setCount(STEP), [resetKey]);
  const more = useCallback(() => setCount((n) => n + STEP), []);
  return { visible: items.slice(0, count), rest: Math.max(0, items.length - count), more };
}

/** Кнопка в конце списка: сама нажимается, когда до неё докрутили. */
export function ShowMore({ rest, onMore }: { rest: number; onMore: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!rest || !ref.current || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) onMore();
    });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [rest, onMore]);
  if (!rest) return null;
  return (
    <div className="show-more">
      <button ref={ref} type="button" className="btn sm" onClick={onMore}>
        Показать ещё ({rest})
      </button>
    </div>
  );
}
