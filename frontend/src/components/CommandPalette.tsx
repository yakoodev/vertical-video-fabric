import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import { Icon } from "@/components/Icon";

// Searchable words for the source type, so «твич» / «ютуб» find projects.
const SOURCE_KIND: Record<string, string> = {
  twitch_url: "twitch твич",
  youtube_url: "youtube ютуб",
  upload: "файл",
  direct_url: "ссылка",
  smotvibe_url: "плеер",
};

type Cmd = { id: string; group: string; label: string; hint?: string; keywords?: string; icon: string; run: () => void };

/**
 * Ctrl/⌘ K — one box to jump anywhere or run a command: pages, projects (and
 * their tabs), clips, «Создать проект». Arrow keys + Enter; Esc closes.
 */
export function CommandPalette({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const sources = useQuery({ queryKey: qk.sources, queryFn: sourcesApi.list, enabled: open });
  const clips = useQuery({ queryKey: qk.clips(), queryFn: () => clipsApi.list(), enabled: open, staleTime: 30_000 });
  // Inside a project its moments are searchable too (the most frequent target).
  const projectId = location.pathname.match(/^\/projects\/(\d+)/)?.[1] ?? "";
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
      { id: "create", group: "Действия", label: "Создать проект", hint: "файл или ссылка", icon: "plus", run: onCreate },
      { id: "queue", group: "Действия", label: "Открыть очередь задач", icon: "queue", run: go("/tasks") },
      { id: "p-projects", group: "Разделы", label: "Проекты", icon: "folder", run: go("/projects") },
      { id: "p-clips", group: "Разделы", label: "Клипы", icon: "scissors", run: go("/clips") },
      { id: "p-assets", group: "Разделы", label: "Файлы для монтажа (мемы, звуки)", icon: "image", run: go("/assets") },
      { id: "p-pub", group: "Разделы", label: "Публикации", icon: "send", run: go("/publications") },
      { id: "p-auto", group: "Разделы", label: "Авто", icon: "zap", run: go("/automation") },
      { id: "p-acc", group: "Разделы", label: "Аккаунты", icon: "users", run: go("/accounts") },
      { id: "p-set", group: "Разделы", label: "Настройки", icon: "settings", run: go("/settings") },
      { id: "p-help", group: "Разделы", label: "Помощь", icon: "help", run: go("/help") },
    ];
    // Inside a project: its tabs first — the most frequent jumps.
    const m = location.pathname.match(/^\/projects\/(\d+)/);
    if (m) {
      const id = m[1];
      const tabs: [string, string][] = [
        ["source", "Исходник"],
        ["candidates", "Моменты"],
        ["clips", "Клипы проекта"],
        ["montaged", "Смонтированные"],
      ];
      list.unshift(
        ...tabs.map(([seg, label]) => ({
          id: `tab-${seg}`,
          group: "Этот проект",
          label,
          icon: "arrow",
          run: go(`/projects/${id}/${seg}`),
        })),
      );
    }
    for (const p of project.data?.clip_plans ?? []) {
      if (p.hidden) continue;
      list.push({
        id: `plan-${p.id}`,
        group: "Моменты проекта",
        label: p.title || `План #${p.id}`,
        hint: `${p.favorite ? "★ " : ""}${p.ai_pick ? `✨${p.ai_pick.score} ` : ""}→ монтаж`,
        icon: "scissors",
        run: go(`/projects/${project.data!.id}/candidates?clip=${p.id}`),
      });
    }
    for (const s of sources.data ?? []) {
      const name = s.original_filename || s.original_url || `Проект #${s.id}`;
      list.push({
        id: `src-${s.id}`,
        group: "Проекты",
        label: name,
        hint: `${s.clip_plans_count ?? 0} моментов`,
        keywords: SOURCE_KIND[s.source_type] ?? "",
        icon: "film",
        run: go(`/projects/${s.id}/candidates`),
      });
    }
    for (const c of (clips.data ?? []).slice(0, 300)) {
      list.push({
        id: `clip-${c.id}`,
        group: "Клипы",
        label: c.title || `Клип #${c.id}`,
        hint: c.status === "succeeded" ? "готов" : c.status,
        icon: "scissors",
        run: go(c.source_id ? `/projects/${c.source_id}/clips` : "/clips"),
      });
    }
    return list;
  }, [sources.data, clips.data, project.data, location.pathname, navigate, onCreate]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const hits = needle
      ? commands.filter((c) => `${c.label} ${c.group} ${c.hint ?? ""} ${c.keywords ?? ""}`.toLowerCase().includes(needle))
      : commands.filter((c) => c.group !== "Клипы" && c.group !== "Моменты проекта");
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
            placeholder="Проект, клип, раздел или команда…"
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
