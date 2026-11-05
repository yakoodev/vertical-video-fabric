import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import type { NavState } from "@/api/nav";
import { Icon } from "@/components/Icon";
import { useModules } from "@/hooks/useModules";
import { clipEditorPath, projectPath, videoPath } from "@/lib/paths";

// Searchable words for the source type, so «твич» / «ютуб» find videos.
const SOURCE_KIND: Record<string, string> = {
  twitch_url: "twitch твич",
  youtube_url: "youtube ютуб",
  upload: "файл",
  direct_url: "ссылка",
  smotvibe_url: "плеер",
};

type Cmd = { id: string; group: string; label: string; hint?: string; keywords?: string; icon: string; run: () => void };

/**
 * Ctrl/⌘ K — одно поле на всё: проекты и их вкладки, видео, клипы, разделы, «Новое видео».
 * Стрелки и Enter; Esc закрывает.
 */
export function CommandPalette({
  open,
  onClose,
  onCreate,
  onCreateProject,
  onOpenChat,
  nav,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: () => void;
  onCreateProject: () => void;
  onOpenChat: () => void;
  nav: NavState | undefined;
}) {
  const { on } = useModules();
  const navigate = useNavigate();
  const location = useLocation();
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const sources = useQuery({ queryKey: qk.sources, queryFn: sourcesApi.list, enabled: open });
  const clips = useQuery({ queryKey: qk.clips(), queryFn: () => clipsApi.list(), enabled: open, staleTime: 30_000 });
  // Inside a video its moments are searchable too (the most frequent target).
  const projectId = location.pathname.match(/^\/v\/(\d+)/)?.[1] ?? "";
  const project = useQuery({
    queryKey: qk.source(projectId),
    queryFn: () => sourcesApi.get(projectId),
    enabled: open && Boolean(projectId),
    staleTime: 30_000,
  });

  useEffect(() => {
    if (open) {
      setQ("");
      setIdx(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const commands = useMemo<Cmd[]>(() => {
    const go = (to: string) => () => navigate(to);
    const list: Cmd[] = [
      { id: "create", group: "Действия", label: "Новое видео", hint: "файл или ссылка", icon: "plus", run: onCreate },
      ...(on("series") ? [{ id: "create-p", group: "Действия", label: "Новый проект", icon: "folder", run: onCreateProject }] : []),
      ...(on("agent_chat") ? [{ id: "gchat", group: "Действия", label: "Общий чат", hint: "вопрос агенту", icon: "chat", run: onOpenChat }] : []),
      { id: "queue", group: "Действия", label: "Очередь задач", keywords: "задачи", icon: "queue", run: go("/inbox?tab=tasks") },
      { id: "p-inbox", group: "Разделы", label: "Входящие", keywords: "одобрение вопросы", icon: "inbox", run: go("/inbox") },
      ...(on("autopilot") ? [{ id: "p-appr", group: "Разделы", label: "Одобрение", hint: "Входящие", icon: "check", run: go("/inbox?tab=approve") }] : []),
      ...(on("stream_questions") ? [{ id: "p-q", group: "Разделы", label: "Вопросы всех проектов", hint: "Входящие", icon: "chat", run: go("/inbox?tab=questions") }] : []),
      ...(on("publishing")
        ? [
            { id: "p-pub", group: "Разделы", label: "Публикации", icon: "send", run: go("/pubs?tab=pubs") },
            { id: "p-acc", group: "Разделы", label: "Аккаунты", icon: "users", run: go("/pubs?tab=accounts") },
          ]
        : []),
      { id: "p-ready", group: "Разделы", label: "Готовые ролики", keywords: "клипы", icon: "scissors", run: go("/pubs?tab=ready") },
      { id: "p-assets", group: "Разделы", label: "Общие файлы", keywords: "файлы для монтажа мемы звуки", icon: "file", run: go("/files") },
      { id: "p-all", group: "Разделы", label: "Все проекты и видео", icon: "folder", run: go("/projects") },
      ...(on("automation") ? [{ id: "p-auto", group: "Разделы", label: "Авто", hint: "разовый запуск по ссылке", icon: "zap", run: go("/automation") }] : []),
      { id: "p-set", group: "Разделы", label: "Настройки", icon: "settings", run: go("/settings") },
      { id: "p-help", group: "Разделы", label: "Помощь", icon: "help", run: go("/help") },
    ];
    // Inside a video: its tabs first; inside a project: its tabs. The most frequent jumps.
    const m = location.pathname.match(/^\/v\/(\d+)/);
    if (m) {
      const id = m[1];
      const tabs: [string, string][] = [
        ["source", "Исходник"],
        ["candidates", "Моменты"],
        ["clips", "Клипы этого видео"],
        ["montaged", "Смонтированные"],
        ...(on("stream_questions") ? ([["questions", "Вопросы этого видео"]] as [string, string][]) : []),
      ];
      list.unshift(
        ...tabs.map(([seg, label]) => ({
          id: `tab-${seg}`,
          group: "Это видео",
          label,
          icon: "arrow",
          run: go(videoPath(id, seg)),
        })),
      );
    }
    const inProject = location.pathname.match(/^\/p\/(\d+)/)?.[1];
    if (inProject) {
      const tabs: [string, string][] = [
        ["videos", "Видео проекта"],
        ["clips", "Клипы проекта"],
        ["questions", "Вопросы проекта"],
        ["files", "Файлы проекта"],
        ["chat", "Чат проекта"],
        ["brief", "Лор и правила"],
        ["stats", "Статистика"],
        ["autopilot", "Автопилот"],
      ];
      list.unshift(
        ...tabs.map(([tab, label]) => ({
          id: `ptab-${tab}`,
          group: "Этот проект",
          label,
          icon: "arrow",
          run: go(projectPath(inProject, tab)),
        })),
      );
    }
    for (const p of nav?.projects ?? []) {
      list.push({
        id: `project-${p.id}`,
        group: "Проекты",
        label: p.name,
        hint: p.videos ? `${p.videos} видео` : "",
        icon: "folder",
        run: go(projectPath(p.id)),
      });
    }
    for (const p of project.data?.clip_plans ?? []) {
      if (p.hidden) continue;
      list.push({
        id: `plan-${p.id}`,
        group: "Клипы этого видео",
        label: p.title || `План #${p.id}`,
        hint: `${p.favorite ? "★ " : ""}${p.ai_pick ? `✨${p.ai_pick.score} ` : ""}→ монтаж`,
        icon: "scissors",
        run: go(clipEditorPath(project.data!.id, p.id)),
      });
    }
    for (const s of sources.data ?? []) {
      const name = s.display_name || s.original_filename || s.original_url || `Видео #${s.id}`;
      list.push({
        id: `src-${s.id}`,
        group: "Видео",
        label: name,
        hint: `${s.clip_plans_count ?? 0} клипов`,
        keywords: `${SOURCE_KIND[s.source_type] ?? ""} ${s.original_filename}`,
        icon: "film",
        run: go(videoPath(s.id, "candidates")),
      });
    }
    for (const c of (clips.data ?? []).slice(0, 300)) {
      list.push({
        id: `clip-${c.id}`,
        group: "Клипы",
        label: c.title || `Клип #${c.id}`,
        hint: c.status === "succeeded" ? "готов" : c.status,
        icon: "scissors",
        run: go(
          c.source_id && c.clip_plan_id
            ? clipEditorPath(c.source_id, c.clip_plan_id)
            : c.source_id
              ? videoPath(c.source_id, "clips")
              : "/pubs?tab=ready",
        ),
      });
    }
    return list;
  }, [sources.data, clips.data, project.data, location.pathname, navigate, onCreate, onCreateProject, onOpenChat, nav, on]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const hits = needle
      ? commands.filter((c) => `${c.label} ${c.group} ${c.hint ?? ""} ${c.keywords ?? ""}`.toLowerCase().includes(needle))
      : commands.filter((c) => c.group !== "Клипы" && c.group !== "Клипы этого видео");
    return hits.slice(0, 40);
  }, [commands, q]);

  useEffect(() => setIdx(0), [q]);
  if (!open) return null;

  const run = (c: Cmd | undefined) => {
    if (!c) return;
    onClose();
    c.run();
  };

  let lastGroup = "";
  return (
    <div className="modal-backdrop cmdk-backdrop" onClick={onClose}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Поиск и команды">
        <div className="cmdk-input">
          <Icon name="search" />
          <input
            ref={inputRef}
            value={q}
            placeholder="Проект, видео, клип, раздел или команда…"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setIdx((i) => Math.min(shown.length - 1, i + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setIdx((i) => Math.max(0, i - 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                run(shown[idx]);
              } else if (e.key === "Escape") {
                onClose();
              }
            }}
          />
          <kbd>Esc</kbd>
        </div>
        <ul className="cmdk-list">
          {shown.length ? null : <li className="cmdk-empty muted">Ничего не нашлось</li>}
          {shown.map((c, i) => {
            const header = c.group !== lastGroup ? c.group : null;
            lastGroup = c.group;
            return (
              <li key={c.id}>
                {header ? <div className="cmdk-group">{header}</div> : null}
                <button
                  className={`cmdk-item${i === idx ? " active" : ""}`}
                  onMouseEnter={() => setIdx(i)}
                  onClick={() => run(c)}
                >
                  <Icon name={c.icon} size={16} />
                  <span className="cmdk-label">{c.label}</span>
                  {c.hint ? <span className="cmdk-hint">{c.hint}</span> : null}
                </button>
              </li>
            );
          })}
        </ul>
        <div className="cmdk-foot muted">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> выбрать
          </span>
          <span>
            <kbd>Enter</kbd> открыть
          </span>
          <span>
            <kbd>?</kbd> все горячие клавиши
          </span>
        </div>
      </div>
    </div>
  );
}
