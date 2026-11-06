import { Suspense, useCallback, useEffect, useState, type ComponentProps } from "react";
import { useQuery } from "@tanstack/react-query";
import { Outlet, useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { navApi, navKeys } from "@/api/nav";
import { ActivityCenter } from "@/components/ActivityCenter";
import { CommandPalette } from "@/components/CommandPalette";
import { Icon } from "@/components/Icon";
import { chatPlace, levelTitle } from "@/components/shell/chatPlace";
import { Ladder, useWhere } from "@/components/shell/Ladder";
import { NewProjectDialog } from "@/components/shell/NewProjectDialog";
import { Sidebar } from "@/components/shell/Sidebar";
import { Loading } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { newVideoPath, rememberProject, videoPath } from "@/lib/paths";
import { SECTIONS, VIDEO_TABS, tabShortcuts, visibleTabs } from "@/lib/tabs";

// Панель чата и её зависимости не нужны, пока её не открыли.
const ChatDrawer = lazyPage<ComponentProps<typeof import("@/components/shell/ChatDrawer").ChatDrawer>>(() => import("@/components/shell/ChatDrawer"), "ChatDrawer");

const SHORTCUTS_BEFORE: [string, string][] = [
  ["Ctrl / ⌘ + K", "поиск и команды"],
  ["N", "новое видео"],
];

const SHORTCUTS_AFTER: [string, string][] = [
  ["F", "Черновики и монтаж: ★ в избранное (черновик под курсором или открытый клип)"],
  ["X", "Черновики: скрыть или вернуть черновик под курсором"],
  ["E", "Черновики: открыть черновик под курсором в монтаже"],
  ["S", "Монтаж: разрезать кусок по курсору"],
  ["Del", "Монтаж: удалить выбранный блок таймлайна"],
  ["Пробел", "Монтаж: плей / пауза с места курсора"],
  ["← / →", "Монтаж: кадр назад / вперёд (Shift — секунда)"],
  ["Home", "Монтаж: в начало клипа"],
  ["Ctrl / ⌘ + Z", "Монтаж: шаг назад по правке клипа"],
  ["Ctrl / ⌘ + Shift + Z", "Монтаж: шаг вперёд, вернуть отменённое"],
  ["Ctrl / ⌘ + D", "Монтаж: копия выделенного слоя"],
  ["Ctrl / ⌘ + C, V", "Монтаж: скопировать слой и вставить под курсор (можно в другой клип)"],
  ["Alt + стрелки", "Монтаж: подвинуть выделенный слой (Shift — крупнее)"],
  ["Esc", "закрыть окно · из монтажа — назад к черновикам"],
  ["?", "эта подсказка"],
];

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
  const [newProject, setNewProject] = useState(false);
  const [keys, setKeys] = useState(false);
  const [chat, setChat] = useState<"here" | "general" | null>(null);
  const { on } = useModules();
  const nav = useQuery({ queryKey: navKeys.all, queryFn: navApi.get, refetchInterval: 30000, staleTime: 15_000 });
  const where = useWhere(nav.data);
  const { projectId } = where;
  const place = chatPlace(where, nav.data, on("editor_agent"));
  const chatHere = place.levels[0];
  // Один путь: «Новое видео» → тип → страница шагов (решение 16). В проекте — сразу в него.
  const openCreate = useCallback(() => navigate(newVideoPath(projectId)), [navigate, projectId]);
  const openNewProject = useCallback(() => setNewProject(true), []);
  const openKeys = useCallback(() => setKeys(true), []);
  const openGeneralChat = useCallback(() => setChat("general"), []);
  const closeChat = useCallback(() => setChat(null), []);

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
        setKeys(false);
        setNewProject(false);
      }
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      if (document.querySelector(".modal-backdrop, .drawer-backdrop")) return; // a dialog owns the keyboard
      if (e.key === "?") setKeys(true);
      else if (e.key === "n" || e.key === "т") openCreate();
      else if (/^[1-9]$/.test(e.key)) {
        // Цифра — вкладка по порядку на экране: выключенных модулей в списке нет, подсказка «?» считает так же.
        const n = Number(e.key) - 1;
        const [, head, id, seg] = location.pathname.split("/");
        if (head === "p" && id) {
          // У проекта набор зависит ещё и от вида проекта: нажимаем настоящую ссылку.
          document.querySelectorAll<HTMLElement>(".ptabs [data-tab]")[n]?.click();
        } else if (head === "v" && id) {
          // В монтаже вкладок на экране нет: случайная цифра не должна выбрасывать из редактора.
          const editing = seg === "candidates" && new URLSearchParams(location.search).has("clip");
          const tab = editing ? undefined : visibleTabs(VIDEO_TABS, on)[n];
          if (tab) navigate(videoPath(id, tab.key));
        } else if (head === "inbox" || (head === "pubs" && !id)) {
          const tab = visibleTabs(SECTIONS[head].tabs, on)[n];
          if (tab) navigate(tab.to);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [location.pathname, location.search, navigate, on, openCreate]);

  return (
    <div className="shell">
      <Sidebar nav={nav.data} projectId={projectId} onCreateProject={openNewProject} onKeys={openKeys} />
      <main className="content">
        <header className="top">
          <Ladder nav={nav.data} onCreateVideo={openCreate} />
          <div className="tools">
            <button type="button" className="tool tool-search" onClick={() => setPalette(true)} title="Поиск и команды (Ctrl K)">
              <Icon name="search" size={16} />
              <span>
                Поиск<em> Ctrl K</em>
              </span>
            </button>
            {on("agent_chat") ? (
              <button
                type="button"
                className="tool tool-chat"
                disabled={place.pending}
                onClick={() => setChat("here")}
                title={place.pending ? "Чат" : levelTitle(chatHere)}
              >
                <Icon name="chat" size={16} />
                <span>{place.pending ? "Чат" : levelTitle(chatHere)}</span>
              </button>
            ) : null}
            {on("agent_chat") && (place.pending || chatHere.kind !== "general") ? (
              <button type="button" className="tool tool-general" onClick={openGeneralChat} title="Общий чат: вопросы агенту не про один проект">
                <span>
                  Общий<em> чат</em>
                </span>
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
        onOpenChat={openGeneralChat}
        nav={nav.data}
      />

      {chat ? (
        <Suspense fallback={null}>
          <ChatDrawer place={place} start={chat} onClose={closeChat} />
        </Suspense>
      ) : null}

      {newProject ? <NewProjectDialog onClose={() => setNewProject(false)} /> : null}

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
                {[...SHORTCUTS_BEFORE, ...tabShortcuts(on), ...SHORTCUTS_AFTER].map(([k, v]) => (
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
