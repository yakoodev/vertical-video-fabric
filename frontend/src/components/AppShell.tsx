import { NavLink, Outlet } from "react-router-dom";
import { ActivityCenter } from "@/components/ActivityCenter";

const NAV = [
  { to: "/projects", label: "Проекты", ico: "🎬" },
  { to: "/clips", label: "Клипы", ico: "✂️" },
  { to: "/automation", label: "Авто", ico: "⚡" },
  { to: "/publications", label: "Публикации", ico: "📡" },
  { to: "/tasks", label: "Задачи", ico: "🗂️" },
  { to: "/accounts", label: "Аккаунты", ico: "👤" },
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
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
          >
            <span className="ico">{item.ico}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
        <div className="nav-spacer" />
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
