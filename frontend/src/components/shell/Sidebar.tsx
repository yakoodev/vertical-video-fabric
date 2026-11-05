import { useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";
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
  // Телефон: меню лентой, текущий пункт должен быть виден.
  useEffect(() => {
    if (window.innerWidth > 760) return;
    const link = ref.current?.querySelector<HTMLElement>(".side-link.on");
    if (link && ref.current) ref.current.scrollLeft = link.offsetLeft - (ref.current.clientWidth - link.offsetWidth) / 2;
  }, [pathname, projectId]);
  return (
    <aside className="side" ref={ref}>
      <div className="side-brand">
        <svg viewBox="0 0 24 24" aria-hidden width="24" height="24">
          <path d="M4 7c0-2 1.6-3.5 3.5-3.5H21l-3 5H9L4 13z" fill="#a78bfa" />
          <path d="M4 16c0-2 1.6-3.5 3.5-3.5H17l-3 4.5H9l-5 4z" fill="#8b5cf6" />
        </svg>
        <span>FABRIC</span>
      </div>
      <NavLink to="/projects" className={`side-head${isOn("projects") ? " on" : ""}`} title="Все проекты и видео">
        Проекты
      </NavLink>
      {seriesOn
        ? (nav?.projects ?? []).map((p) => (
            <NavLink key={p.id} to={projectPath(p.id)} className={() => link(p.id === projectId)} title={p.name}>
              <Icon name={projectIcon(p)} />
              <span className="side-t">{shortName(p.name)}</span>
              {p.videos ? <span className="side-n">{p.videos}</span> : null}
            </NavLink>
          ))
        : null}
      {seriesOn ? (
        <button type="button" className="side-link" onClick={onCreateProject} title="Новый проект: канал, сериал или тема">
          <Icon name="plus" />
          <span className="side-t">Новый проект</span>
        </button>
      ) : null}
      <div className="side-sep" />
      <NavLink to="/inbox" className={link(isOn("inbox"))} title="Одобрение, вопросы всех проектов, очередь задач">
        <Icon name="inbox" />
        <span className="side-t">Входящие</span>
        {inbox ? <span className="side-badge">{inbox}</span> : null}
      </NavLink>
      <NavLink to="/pubs" className={link(isOn("pubs"))} title={on("publishing") ? "Публикации и аккаунты" : "Готовые ролики"}>
        <Icon name="users" />
        <span className="side-t">{on("publishing") ? "Публикации и аккаунты" : "Готовые ролики"}</span>
      </NavLink>
      <NavLink to="/files" className={link(isOn("files"))} title="Общие файлы">
        <Icon name="file" />
        <span className="side-t">Общие файлы</span>
      </NavLink>
      <div className="side-sp" />
      <NavLink to="/settings" className={link(isOn("settings"))} title="Настройки">
        <Icon name="settings" />
        <span className="side-t">Настройки</span>
      </NavLink>
      <NavLink to="/help" className={link(isOn("help"))} title="Помощь">
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
    </aside>
  );
}
