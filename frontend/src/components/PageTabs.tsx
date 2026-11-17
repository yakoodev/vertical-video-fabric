import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

export type PageTab = { key: string; label: string; to: string; count?: number | null };

/** Лента вкладок шире экрана (телефон): прокрутить так, чтобы выбранная была видна, а не уехала вправо. */
export function useActiveInView<T extends HTMLElement>(selector: string, key: unknown) {
  const ref = useRef<T>(null);
  useEffect(() => {
    let alive = true;
    const run = () => {
      const box = ref.current;
      if (!alive || !box || box.scrollWidth <= box.clientWidth) return;
      const item = box.querySelector<HTMLElement>(selector);
      if (!item) return;
      const left = item.getBoundingClientRect().left - box.getBoundingClientRect().left + box.scrollLeft;
      if (left < box.scrollLeft || left + item.offsetWidth > box.scrollLeft + box.clientWidth) {
        box.scrollLeft = left - (box.clientWidth - item.offsetWidth) / 2;
      }
    };
    run();
    // Шрифт и счётчики догружаются позже и раздвигают вкладки: ещё раз, когда всё встало.
    void document.fonts?.ready.then(run);
    const late = window.setTimeout(run, 400);
    return () => {
      alive = false;
      window.clearTimeout(late);
    };
  }, [selector, key]);
  return ref;
}

/** Вкладки страницы по образцу референсов: подчёркивание, счётчик в кружке, лента на телефоне. */
export function PageTabs({ tabs, active, label }: { tabs: PageTab[]; active: string; label: string }) {
  const ref = useActiveInView<HTMLElement>("a.on", `${active}:${tabs.map((t) => `${t.key}${t.count ?? ""}`).join(",")}`);
  return (
    <nav className="ptabs" aria-label={label} ref={ref}>
      {tabs.map((t) => (
        <Link key={t.key} to={t.to} replace data-tab={t.key} className={t.key === active ? "on" : ""} aria-current={t.key === active ? "page" : undefined}>
          {t.label}
          {t.count != null ? <span className="ptabs-n">{t.count}</span> : null}
        </Link>
      ))}
    </nav>
  );
}
