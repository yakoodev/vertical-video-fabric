import { Suspense, useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Outlet, useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { navApi, navKeys } from "@/api/nav";
import { ActivityCenter } from "@/components/ActivityCenter";
import { CommandPalette } from "@/components/CommandPalette";
import { Icon } from "@/components/Icon";
import { Ladder, useWhere } from "@/components/shell/Ladder";
import { NewProjectDialog } from "@/components/shell/NewProjectDialog";
import { Sidebar } from "@/components/shell/Sidebar";
import { Loading } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { rememberProject } from "@/lib/paths";
import { AddSource } from "@/pages/ProjectsPage";

// Панель чата и её зависимости не нужны, пока её не открыли.
const GeneralChatDrawer = lazyPage<{ onClose: () => void }>(() => import("@/components/shell/GeneralChatDrawer"), "GeneralChatDrawer");

export const SHORTCUTS: [string, string][] = [
  ["Ctrl / ⌘ + K", "поиск и команды"],
  ["N", "новое видео"],
  ["1 … 8", "вкладки проекта: Видео, Клипы, Вопросы, Файлы, Чат, Лор и правила, Статистика, Автопилот"],
  ["1 … 5", "вкладки видео: Исходник, Моменты, Монтаж, Клипы, Смонтированные"],
  ["1 … 3", "вкладки Входящих и Публикаций"],
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

const PROJECT_TABS = ["videos", "clips", "questions", "files", "chat", "brief", "stats", "autopilot"];
const VIDEO_TABS = ["clips", "source", "candidates", "edit", "segments", "renders", "montaged"];
const INBOX_TABS = ["approve", "questions", "tasks"];
const PUBS_TABS = ["pubs", "accounts", "ready"];

const typing = (el: EventTarget | null) => {
  const t = el as HTMLElement | null;
  return !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
};

export type ShellContext = { openNewVideo: () => void };

/** Кнопка «Новое видео» на странице проекта открывает диалог каркаса. */
export const useShell = () => useOutletContext<ShellContext>();

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const [palette, setPalette] = useState(false);
  const [create, setCreate] = useState(false);
  const [newProject, setNewProject] = useState(false);
  const [keys, setKeys] = useState(false);
  const [chat, setChat] = useState(false);
  const { on } = useModules();
  const nav = useQuery({ queryKey: navKeys.all, queryFn: navApi.get, refetchInterval: 30000, staleTime: 15_000 });
  const { projectId, project } = useWhere(nav.data);
  const openCreate = useCallback(() => setCreate(true), []);
  const openNewProject = useCallback(() => setNewProject(true), []);
  const openKeys = useCallback(() => setKeys(true), []);
  const openChat = useCallback(() => setChat(true), []);
  const closeChat = useCallback(() => setChat(false), []);

  useEffect(() => {
    if (projectId) rememberProject(projectId);
  }, [projectId]);

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
        setNewProject(false);
      }
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      if (document.querySelector(".modal-backdrop")) return; // a dialog owns the keyboard
      if (e.key === "?") setKeys(true);
      else if (e.key === "n" || e.key === "т") setCreate(true);
      else if (/^[1-8]$/.test(e.key)) {
        const n = Number(e.key) - 1;
        const [, head, id] = location.pathname.split("/");
        if (head === "p" && id && PROJECT_TABS[n]) {
          // Вкладки, которых у проекта нет (модуль выключен), остаются без реакции: нажимаем настоящую ссылку.
          document.querySelector<HTMLElement>(`.ptabs [data-tab="${PROJECT_TABS[n]}"]`)?.click();
        } else if (head === "v" && id && VIDEO_TABS[n]) {
          const seg = VIDEO_TABS[n];
          if (seg === "edit") document.querySelector<HTMLAnchorElement>('.pipeline a[href*="?clip="]')?.click();
          else navigate(`/v/${id}/${seg}`);
        } else if (head === "inbox" && INBOX_TABS[n]) {
          navigate(`/inbox?tab=${INBOX_TABS[n]}`);
        } else if (head === "pubs" && !id && PUBS_TABS[n]) {
          navigate(`/pubs?tab=${PUBS_TABS[n]}`);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [location.pathname, navigate]);

  return (
    <div className="shell">
      <Sidebar nav={nav.data} projectId={projectId} onCreateProject={openNewProject} onKeys={openKeys} />
      <main className="content">
        <header className="top">
          <Ladder nav={nav.data} onCreateVideo={openCreate} />
          <div className="tools">
            <button type="button" className="tool" onClick={() => setPalette(true)} title="Поиск и команды (Ctrl K)">
              <Icon name="search" size={16} />
              <span>
                Поиск<em> Ctrl K</em>
              </span>
            </button>
            {on("agent_chat") ? (
              <button type="button" className="tool" onClick={openChat} title="Вопросы агенту не про один проект">
                <Icon name="chat" size={16} />
                <span>Общий чат</span>
              </button>
            ) : null}
            <ActivityCenter />
          </div>
        </header>
        <div className="content-inner">
          <Suspense fallback={<Loading />}>
            <Outlet context={{ openNewVideo: openCreate } satisfies ShellContext} />
          </Suspense>
        </div>
      </main>

      <CommandPalette
        open={palette}
        onClose={() => setPalette(false)}
        onCreate={openCreate}
        onCreateProject={openNewProject}
        onOpenChat={openChat}
        nav={nav.data}
      />

      {chat ? (
        <Suspense fallback={null}>
          <GeneralChatDrawer onClose={closeChat} />
        </Suspense>
      ) : null}

      {newProject ? <NewProjectDialog onClose={() => setNewProject(false)} /> : null}

      {create ? (
        <div className="modal-backdrop" onClick={() => setCreate(false)}>
          <div className="modal create-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Новое видео">
            <div className="pub-head">
              <h3>{project ? `Новое видео в проекте «${project.name}»` : "Новое видео"}</h3>
              <button className="pub-x" title="Закрыть" onClick={() => setCreate(false)}>
                ×
              </button>
            </div>
            <p className="muted" style={{ margin: "0 0 12px", fontSize: 13 }}>
              Загрузите видео или вставьте ссылку: скачивание пойдёт в фоне, прогресс в «Очереди задач».
            </p>
            <AddSource onStarted={() => setCreate(false)} projectId={project?.id ?? null} />
          </div>
        </div>
      ) : null}

      {keys ? (
        <div className="modal-backdrop" onClick={() => setKeys(false)}>
          <div className="modal keys-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Горячие клавиши">
            <div className="pub-head">
              <h3>Горячие клавиши</h3>
              <button className="pub-x" title="Закрыть" onClick={() => setKeys(false)}>
                ×
              </button>
            </div>
            <table className="keys-table">
              <tbody>
                {SHORTCUTS.map(([k, v]) => (
                  <tr key={`${k}${v}`}>
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
