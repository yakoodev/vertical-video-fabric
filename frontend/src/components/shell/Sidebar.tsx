import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import type { NavProject, NavState } from "@/api/nav";
import { Icon } from "@/components/Icon";
import { useModules } from "@/hooks/useModules";
import { projectPath } from "@/lib/paths";

function projectIcon(p: NavProject): string {
  const name = p.name.toLowerCase();
  if (p.kind === "ai_video") return /истор/.test(name) ? "layers" : "playbox";
  if (p.kind === "series" || /сериал/.test(name)) return "film";
  if (p.kind === "promo" || /промо/.test(name)) return "mega";
  if (p.kind === "explainer" || /сов|истор/.test(name)) return "book";
  if (/apex|игр/.test(name)) return "pad";
  return "folder";
}

/** Знак FABRIC: в боковой панели и в шапке монтажа, где панели нет. Клик по нему — «Главная». */
export function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden width={size} height={size}>
      <path d="M4 7c0-2 1.6-3.5 3.5-3.5H21l-3 5H9L4 13z" fill="#a78bfa" />
      <path d="M4 16c0-2 1.6-3.5 3.5-3.5H17l-3 4.5H9l-5 4z" fill="#8b5cf6" />
    </svg>
  );
}

const shortName = (name: string) => name.replace(/\s*\(.*\)$/, "") || name;

/** Боковая панель: проекты со счётчиками, вне проектов, настройки и помощь внизу. */
export function Sidebar({
  nav,
  projectId,
  onCreateProject,
  onKeys,
}: {
  nav: NavState | undefined;
  projectId: number | null;
  onCreateProject: () => void;
  onKeys: () => void;
}) {
  const { on } = useModules();
  const { pathname } = useLocation();
  const inbox = nav?.inbox.total ?? 0;
  const isOn = (...heads: string[]) => heads.includes(pathname.split("/")[1] ?? "");
  const link = (active: boolean) => `side-link${active ? " on" : ""}`;
  const seriesOn = on("series");
  const ref = useRef<HTMLElement>(null);
  // Телефон: меню лентой, текущий пункт должен быть виден. Список проектов приходит позже первого кадра
  // и сдвигает пункты вправо — поэтому пересчёт и после его загрузки.
  const projectCount = nav?.projects.length ?? 0;
  // Телефон: все проекты — один пункт «Проекты ▾», список открывается листом под лентой меню.
  const [sheetTop, setSheetTop] = useState<number | null>(null);
  const projectsOn = projectId != null || isOn("projects");
  useEffect(() => {
    if (window.innerWidth > 760) return;
    const side = ref.current;
    const link = side?.querySelector<HTMLElement>(".side-link.on");
    if (!link || !side) return;
    // Уже виден целиком (первые пункты ленты) — не двигаем: иначе уедут Главная и Проекты.
    const at = link.getBoundingClientRect();
    const box = side.getBoundingClientRect();
    if (at.left >= box.left && at.right <= box.right) return;
    side.scrollLeft += at.left - box.left - (box.width - at.width) / 2;
  }, [pathname, projectId, projectCount]);
  useEffect(() => setSheetTop(null), [pathname]);
  useEffect(() => {
    if (sheetTop == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSheetTop(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheetTop]);
  const toggleSheet = () =>
    setSheetTop((open) => (open != null ? null : Math.max(8, (ref.current?.getBoundingClientRect().bottom ?? 0) + 4)));
  const pubsLabel = on("publishing") ? "Публикации и аккаунты" : "Готовые ролики";
  return (
    <aside className="side" ref={ref}>
      <Link to="/" className="side-brand" title="На главную" aria-label="FABRIC — на главную">
        <BrandMark />
        <span>FABRIC</span>
      </Link>
      <NavLink to="/" end className={`${link(pathname === "/")} side-k-home`} title="Главная: что ждёт, что выйдет, что зашло">
        <Icon name="home" />
        <span className="side-t">Главная</span>
      </NavLink>
      {/* Только телефон: проекты свёрнуты в один пункт со списком (на компьютере — список ниже, как был). */}
      {seriesOn ? (
        <button
          type="button"
          className={`${link(projectsOn)} side-k-projects`}
          onClick={toggleSheet}
          aria-expanded={sheetTop != null}
          aria-haspopup="dialog"
          title="Проекты: список, все проекты и новый проект"
        >
          <Icon name="folder" />
          <span className="side-t">
            Проекты<span className="side-caret" aria-hidden> ▾</span>
          </span>
        </button>
      ) : (
        <NavLink to="/projects" className={`${link(projectsOn)} side-k-projects`} title="Все проекты и видео">
          <Icon name="folder" />
          <span className="side-t">Проекты</span>
        </NavLink>
      )}
      <NavLink to="/projects" className={`side-head${isOn("projects") ? " on" : ""}`} title="Все проекты и видео">
        {/* Значок виден, когда панель узкая (одни значки) — иначе «Все проекты» с панели не открыть. */}
        <Icon name="folder" />
        <span className="side-head-t">Проекты</span>
      </NavLink>
      {seriesOn
        ? (nav?.projects ?? []).map((p) => (
            <NavLink key={p.id} to={projectPath(p.id)} className={() => `${link(p.id === projectId)} side-proj`} title={p.name}>
              <Icon name={projectIcon(p)} />
              <span className="side-t">{shortName(p.name)}</span>
              {p.videos ? <span className="side-n">{p.videos}</span> : null}
            </NavLink>
          ))
        : null}
      {seriesOn ? (
        <button type="button" className="side-link side-proj" onClick={onCreateProject} title="Новый проект: канал, сериал или тема">
          <Icon name="plus" />
          <span className="side-t">Новый проект</span>
        </button>
      ) : null}
      <div className="side-sep" />
      <NavLink to="/inbox" className={`${link(isOn("inbox"))} side-k-inbox`} title="Одобрение, вопросы всех проектов, очередь задач">
        <Icon name="inbox" />
        <span className="side-t">Входящие</span>
        {inbox ? <span className="side-badge">{inbox}</span> : null}
      </NavLink>
      <NavLink to="/feed" className={`${link(isOn("feed"))} side-k-feed`} title="Лента: что сделали, но ещё не публиковали">
        <Icon name="feed" />
        <span className="side-t">Лента</span>
      </NavLink>
      <NavLink
        to="/pubs"
        className={`${link(isOn("pubs"))} side-k-pubs`}
        title={on("publishing") ? "Публикации и аккаунты: что уже запощено, аккаунты площадок, готовые ролики" : "Готовые ролики"}
      >
        <Icon name="users" />
        <span className="side-t side-t-long">{pubsLabel}</span>
        <span className="side-t side-t-short">{on("publishing") ? "Публикации" : "Готовое"}</span>
      </NavLink>
      <NavLink to="/files" className={`${link(isOn("files"))} side-k-files`} title="Общие файлы">
        <Icon name="file" />
        <span className="side-t side-t-long">Общие файлы</span>
        <span className="side-t side-t-short">Файлы</span>
      </NavLink>
      <div className="side-sp" />
      <NavLink to="/settings" className={`${link(isOn("settings"))} side-k-settings`} title="Настройки">
        <Icon name="settings" />
        <span className="side-t">Настройки</span>
      </NavLink>
      <NavLink to="/help" className={`${link(isOn("help"))} side-k-help`} title="Помощь">
        <Icon name="help" />
        <span className="side-t">Помощь</span>
      </NavLink>
      <div className="side-mini">
        <button type="button" onClick={onKeys} title="Горячие клавиши (?)">
          <Icon name="keyboard" size={15} />
          <span>Клавиши</span>
        </button>
        <form action="/logout" method="post">
          <button type="submit" title="Выйти">
            <Icon name="logout" size={15} />
            <span>Выйти</span>
          </button>
        </form>
      </div>
      {sheetTop != null ? (
        <>
          <div className="side-sheet-back" onClick={() => setSheetTop(null)} />
          {/* Любой пункт закрывает лист — и тот, что ведёт на уже открытую страницу. */}
          <div className="side-sheet" role="dialog" aria-label="Проекты" style={{ top: sheetTop }} onClick={() => setSheetTop(null)}>
            <NavLink to="/projects" end className={`side-sheet-item side-sheet-all${isOn("projects") ? " on" : ""}`}>
              <Icon name="folder" />
              <span className="side-t">Все проекты и видео</span>
            </NavLink>
            {(nav?.projects ?? []).map((p) => (
              <NavLink key={p.id} to={projectPath(p.id)} className={() => `side-sheet-item${p.id === projectId ? " on" : ""}`}>
                <Icon name={projectIcon(p)} />
                <span className="side-t">{shortName(p.name)}</span>
                {p.videos ? <span className="side-n">{p.videos}</span> : null}
              </NavLink>
            ))}
            <button
              type="button"
              className="side-sheet-item side-sheet-new"
              onClick={() => {
                setSheetTop(null);
                onCreateProject();
              }}
            >
              <Icon name="plus" />
              <span className="side-t">Новый проект</span>
            </button>
          </div>
        </>
      ) : null}
    </aside>
  );
}
