import { Suspense, useCallback, useEffect, useState } from "react";
import { Loading } from "@/components/ui";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { ActivityCenter } from "@/components/ActivityCenter";
import { CommandPalette } from "@/components/CommandPalette";
import { Icon } from "@/components/Icon";
import { AddSource } from "@/pages/ProjectsPage";
import { useQuery } from "@tanstack/react-query";
import { tasksApi } from "@/api/tasks";
import { qk } from "@/api/keys";
import { isActive } from "@/hooks/useActiveTasks";
import { apKeys, autopilotApi } from "@/api/autopilot";
import { useModules } from "@/hooks/useModules";

// Order follows the daily loop: make → clip → publish (manual or auto) → watch.
// module — пункт виден, только пока модуль включён («Настройки → Модули», app/modules.py).
const NAV: { to: string; label: string; icon: string; badge?: "active" | "approvals"; module?: string }[] = [
  { to: "/projects", label: "Проекты", icon: "folder" },
  { to: "/chat", label: "Чат", icon: "chat", module: "agent_chat" },
  { to: "/clips", label: "Клипы", icon: "scissors" },
  { to: "/assets", label: "Файлы для монтажа", icon: "image" },
  { to: "/approvals", label: "Одобрение", icon: "check", badge: "approvals", module: "autopilot" },
  { to: "/publications", label: "Публикации", icon: "send", module: "publishing" },
  { to: "/automation", label: "Авто", icon: "zap", module: "automation" },
  { to: "/tasks", label: "Очередь", icon: "queue", badge: "active" },
  { to: "/accounts", label: "Аккаунты", icon: "users", module: "publishing" },
];

export const SHORTCUTS: [string, string][] = [
  ["Ctrl / ⌘ + K", "поиск и команды"],
  ["N", "создать проект"],
  ["1 … 5", "вкладки проекта: Исходник, Моменты, Монтаж, Клипы, Смонтированные"],
  ["F", "Моменты: ★ в избранное (момент под курсором / открытый)"],
  ["X", "Моменты: скрыть / вернуть момент под курсором"],
  ["E", "Моменты: открыть момент под курсором в монтаже"],
  ["S", "Монтаж: разрезать кусок по курсору"],
  ["Del", "Монтаж: удалить выбранный блок таймлайна"],
  ["Пробел", "Монтаж: плей / пауза с места курсора"],
  ["← / →", "Монтаж: кадр назад / вперёд (Shift — секунда)"],
  ["Home", "Монтаж: в начало клипа"],
  ["Ctrl / ⌘ + Z", "Монтаж: шаг назад по правке клипа"],
  ["Ctrl / ⌘ + D", "Монтаж: копия выделенного слоя"],
  ["Ctrl / ⌘ + C, V", "Монтаж: скопировать слой и вставить под курсор (можно в другой клип)"],
  ["Alt + стрелки", "Монтаж: подвинуть выделенный слой (Shift — крупнее)"],
  ["Esc", "закрыть окно · из монтажа — назад к моментам"],
  ["?", "эта подсказка"],
];

const typing = (el: EventTarget | null) => {
  const t = el as HTMLElement | null;
  return !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
};

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const [palette, setPalette] = useState(false);
  const [create, setCreate] = useState(false);
  const [keys, setKeys] = useState(false);
  // Read the shared cache only: ActivityCenter owns the polling side effects
  // (completion toasts) — a second useActiveTasks() would toast everything twice.
  const tasks = useQuery({ queryKey: qk.activeTasks, queryFn: tasksApi.active, refetchInterval: 5000 });
  const activeCount = (tasks.data ?? []).filter((t) => isActive(t.status)).length;
  // Автопилот: сколько карточек ждёт владельца (app/autopilot.py).
  const { on, ready } = useModules();
  const approvals = useQuery({
    queryKey: apKeys.counts,
    queryFn: autopilotApi.counts,
    refetchInterval: 30000,
    enabled: ready("autopilot"),
  });
  const pendingCount = approvals.data?.pending ?? 0;
  const openCreate = useCallback(() => setCreate(true), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((v) => !v);
        return;
      }
      if (e.key === "Escape") {
        setCreate(false);
        setKeys(false);
      }
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      if (document.querySelector(".modal-backdrop")) return; // a dialog owns the keyboard
      if (e.key === "?") setKeys(true);
      else if (e.key === "n" || e.key === "т") setCreate(true);
      else if (/^[1-5]$/.test(e.key)) {
        const m = location.pathname.match(/^\/projects\/(\d+)/);
        if (!m) return;
        const seg = ["source", "candidates", "edit", "clips", "montaged"][Number(e.key) - 1];
        if (seg === "edit") {
          document.querySelector<HTMLAnchorElement>('.pipeline a[href*="?clip="]')?.click();
        } else navigate(`/projects/${m[1]}/${seg}`);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [location.pathname, navigate]);

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
        <button className="nav-create" onClick={openCreate} title="Новый проект: файл или ссылка (N)">
          <Icon name="plus" />
          <span>Создать</span>
          <kbd>N</kbd>
        </button>
        <nav className="nav-section">
          {NAV.filter((item) => !item.module || on(item.module)).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive: on }) => `nav-link${on ? " active" : ""}`}
            >
              <Icon name={item.icon} className="ico" />
              <span>{item.label}</span>
              {item.badge === "active" && activeCount ? <span className="nav-badge">{activeCount}</span> : null}
              {item.badge === "approvals" && pendingCount ? <span className="nav-badge">{pendingCount}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="nav-spacer" />
        <button className="nav-link nav-keys" onClick={() => setKeys(true)}>
          <Icon name="keyboard" className="ico" />
          <span>Горячие клавиши</span>
          <kbd>?</kbd>
        </button>
        <NavLink to="/help" className={({ isActive: on }) => `nav-link${on ? " active" : ""}`}>
          <Icon name="help" className="ico" />
          <span>Помощь</span>
        </NavLink>
        <NavLink to="/settings" className={({ isActive: on }) => `nav-link${on ? " active" : ""}`}>
          <Icon name="settings" className="ico" />
          <span>Настройки</span>
        </NavLink>
        <form action="/logout" method="post">
          <button type="submit" className="nav-link" style={{ width: "100%", border: 0, background: "none" }}>
            <Icon name="logout" className="ico" />
            <span>Выйти</span>
          </button>
        </form>
      </aside>
      <main className="content">
        <div className="topbar">
          <button className="topbar-search" onClick={() => setPalette(true)}>
            <Icon name="search" size={16} />
            <span>Поиск или команда…</span>
            <kbd>Ctrl K</kbd>
          </button>
          <div className="topbar-right">
            <Link to="/tasks" className={`topbar-active${activeCount ? " on" : ""}`} title="Очередь задач">
              <Icon name="bolt" size={15} />
              {activeCount ? `${activeCount} активн.` : "нет задач"}
              {activeCount ? <span className="pulse-dot" /> : null}
            </Link>
            <ActivityCenter />
          </div>
        </div>
        <div className="content-inner">
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </div>
      </main>

      <CommandPalette open={palette} onClose={() => setPalette(false)} onCreate={openCreate} />

      {create ? (
        <div className="modal-backdrop" onClick={() => setCreate(false)}>
          <div className="modal create-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <div className="pub-head">
              <h3>Новый проект</h3>
              <button className="pub-x" title="Закрыть" onClick={() => setCreate(false)}>
                ×
              </button>
            </div>
            <p className="muted" style={{ margin: "0 0 12px", fontSize: 13 }}>
              Загрузите видео или вставьте ссылку — скачивание пойдёт в фоне, прогресс в «Очереди».
            </p>
            <AddSource onStarted={() => setCreate(false)} />
          </div>
        </div>
      ) : null}

      {keys ? (
        <div className="modal-backdrop" onClick={() => setKeys(false)}>
          <div className="modal keys-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <div className="pub-head">
              <h3>Горячие клавиши</h3>
              <button className="pub-x" title="Закрыть" onClick={() => setKeys(false)}>
                ×
              </button>
            </div>
            <table className="keys-table">
              <tbody>
                {SHORTCUTS.map(([k, v]) => (
                  <tr key={k}>
                    <td>
                      <kbd>{k}</kbd>
                    </td>
                    <td>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
