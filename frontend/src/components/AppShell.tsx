import { NavLink, Outlet } from "react-router-dom";
import { ActivityCenter } from "@/components/ActivityCenter";

const NAV_SECTIONS: { title: string; items: { to: string; label: string; ico: string }[] }[] = [
  {
    title: "Монтаж",
    items: [
      { to: "/projects", label: "Проекты", ico: "🎬" },
      { to: "/clips", label: "Клипы", ico: "✂️" },
    ],
  },
  {
    title: "Постинг",
    items: [
      { to: "/automation", label: "Авто", ico: "⚡" },
      { to: "/publications", label: "Публикации", ico: "📡" },
      { to: "/accounts", label: "Аккаунты", ico: "👤" },
    ],
  },
  {
    title: "Мониторинг",
    items: [{ to: "/tasks", label: "Задачи", ico: "🗂️" }],
  },
];

export function AppShell() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark" />
          <span className="word">
            FABRIC
            <small>VERTICAL VIDEO</small>
          </span>
        </div>
        {NAV_SECTIONS.map((section) => (
          <div className="nav-section" key={section.title}>
            <div className="nav-section-title">{section.title}</div>
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              >
                <span className="ico">{item.ico}</span>
                <span>{item.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
        <div className="nav-spacer" />
        <NavLink to="/help" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <span className="ico">📖</span>
          <span>Помощь</span>
        </NavLink>
        <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <span className="ico">⚙️</span>
          <span>Настройки</span>
        </NavLink>
        <form action="/logout" method="post">
          <button type="submit" className="nav-link" style={{ width: "100%", border: 0, background: "none" }}>
            <span className="ico">⎋</span>
            <span>Выйти</span>
          </button>
        </form>
      </aside>
      <main className="content">
        <div className="topbar">
          <ActivityCenter />
        </div>
        <div className="content-inner">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
