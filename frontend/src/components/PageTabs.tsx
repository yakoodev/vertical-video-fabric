import { Link } from "react-router-dom";

export type PageTab = { key: string; label: string; to: string; count?: number | null };

/** Вкладки страницы по образцу референсов: подчёркивание, счётчик в кружке, лента на телефоне. */
export function PageTabs({ tabs, active, label }: { tabs: PageTab[]; active: string; label: string }) {
  return (
    <nav className="ptabs" aria-label={label}>
      {tabs.map((t) => (
        <Link key={t.key} to={t.to} replace data-tab={t.key} className={t.key === active ? "on" : ""} aria-current={t.key === active ? "page" : undefined}>
          {t.label}
          {t.count != null ? <span className="ptabs-n">{t.count}</span> : null}
        </Link>
      ))}
    </nav>
  );
}
