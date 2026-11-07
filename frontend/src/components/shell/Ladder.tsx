import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { navApi, navKeys, type NavState } from "@/api/nav";
import { sourcesApi } from "@/api/sources";
import { qk } from "@/api/keys";
import { Icon } from "@/components/Icon";
import { plural } from "@/components/ui";
import { clipPath, projectPath, videoPath } from "@/lib/paths";
import { releaseKeys, releasesApi } from "@/api/releases";
import { SECTIONS, SECTION_ORDER, visibleTabs } from "@/lib/tabs";
import { HIDDEN_SOURCE_TYPES, KIND_LABELS } from "@/lib/videoKinds";
import { clipGroupKeys, clipGroupsApi, countClips } from "@/api/clipGroups";
import { useModules } from "@/hooks/useModules";

type MenuItem = { to: string; label: string; hint?: string; cur?: boolean; action?: boolean; onSelect?: () => void };
type Rung = {
  cap: string;
  val: string;
  href?: string;
  /** Следующая, ещё не выбранная ступень. */
  next?: boolean;
  mute?: boolean;
  items?: MenuItem[];
  /** Список подгружается при открытии меню. */
  loading?: boolean;
  searchLabel?: string;
};

/** Где мы: номер проекта и видео из адреса (видео знает свой проект). */
export function useWhere(nav: NavState | undefined) {
  const location = useLocation();
  const parts = location.pathname.split("/").filter(Boolean);
  const head = parts[0] ?? "";
  // Экран клипа сам знает своё видео и проект (тот же запрос, что у страницы: второго не будет).
  const clipId = head === "clips" && /^\d+$/.test(parts[1] ?? "") ? parts[1] : "";
  const videoId = head === "v" ? parts[1] ?? "" : "";
  // Монтаж клипа: /v/:id/candidates?clip=N. Холсты лежат в скрытом источнике, их проект знает только клип.
  const asked = new URLSearchParams(location.search).get("clip") ?? "";
  const editClipId = videoId && parts[2] === "candidates" && /^\d+$/.test(asked) ? asked : "";
  const viewId = clipId || editClipId;
  // Страница выпуска: проект и клипы знает сам выпуск (тот же запрос, что у страницы).
  const releaseKey = head === "r" ? parts[1] ?? "" : "";
  const release = useQuery({
    queryKey: releaseKeys.one(releaseKey),
    queryFn: () => releasesApi.get(releaseKey),
    enabled: Boolean(releaseKey),
    retry: false,
    staleTime: 15_000,
  });
  const clip = useQuery({
    queryKey: clipGroupKeys.view(viewId),
    queryFn: () => clipGroupsApi.view(viewId),
    enabled: Boolean(viewId),
    retry: false,
    staleTime: 15_000,
  });
  const source = useQuery({
    queryKey: qk.source(videoId),
    queryFn: () => sourcesApi.get(videoId),
    enabled: Boolean(videoId),
    staleTime: 30_000,
  });
  const projectId =
    head === "p"
      ? Number(parts[1]) || null
      : release.data?.project?.id ?? clip.data?.project?.id ?? source.data?.project_id ?? null;
  const project = nav?.projects.find((p) => p.id === projectId) ?? null;
  // Холст и загруженный клип: служебный источник, а не видео, которое открыл владелец.
  const hiddenVideo = Boolean(source.data && HIDDEN_SOURCE_TYPES.includes(source.data.source_type));
  return { head, parts, videoId, source, projectId, project, clipId, editClipId, clip, hiddenVideo, releaseKey, release };
}

function Menu({
  rung,
  onClose,
}: {
  rung: Rung;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const navigate = useNavigate();
  const ref = useRef<HTMLInputElement>(null);
  const items = rung.items ?? [];
  const searchable = items.length > 6;
  useEffect(() => {
    if (searchable) ref.current?.focus();
  }, [searchable]);
  const needle = q.trim().toLowerCase();
  const shown = needle ? items.filter((i) => i.label.toLowerCase().includes(needle)) : items;
  return (
    <div className="lad-menu" role="menu" onClick={(e) => e.stopPropagation()}>
      {searchable ? (
        <div className="lad-menu-search">
          <Icon name="search" size={15} />
          <input
            ref={ref}
            value={q}
            placeholder={rung.searchLabel ?? "Найти…"}
            aria-label={rung.searchLabel ?? "Найти"}
            onChange={(e) => {
              setQ(e.target.value);
              setIdx(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setIdx((i) => Math.min(shown.length - 1, i + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setIdx((i) => Math.max(0, i - 1));
              } else if (e.key === "Enter" && shown[idx]) {
                e.preventDefault();
                onClose();
                if (shown[idx].onSelect) shown[idx].onSelect?.();
                else navigate(shown[idx].to);
              } else if (e.key === "Escape") {
                onClose();
              }
            }}
          />
        </div>
      ) : null}
      <div className="lad-menu-list">
        {rung.loading ? <div className="lad-menu-empty">Загрузка…</div> : null}
        {!rung.loading && !shown.length ? <div className="lad-menu-empty">Ничего не нашлось</div> : null}
        {shown.map((item, i) => (
          <Link
            key={`${item.to}-${i}`}
            to={item.to}
            role="menuitem"
            className={`lad-menu-item${item.cur ? " cur" : ""}${searchable && i === idx ? " hot" : ""}${item.action ? " act" : ""}`}
            onClick={(e) => {
              if (item.onSelect) {
                e.preventDefault();
                item.onSelect();
              }
              onClose();
            }}
            title={item.label}
          >
            <span className="lad-menu-label">{item.label}</span>
            {item.hint ? <span className="lad-menu-hint">{item.hint}</span> : null}
          </Link>
        ))}
      </div>
    </div>
  );
}

/** Лестница сверху: три ступени, у каждой список соседей с поиском. */
export function Ladder({ nav, onCreateVideo }: { nav: NavState | undefined; onCreateVideo: () => void }) {
  const [open, setOpen] = useState<number | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const { on } = useModules();
  const { head, parts, videoId, source, projectId, project, clipId, editClipId, clip, hiddenVideo, releaseKey, release } = useWhere(nav);
  const hasProject = head === "p" || head === "v" || Boolean(clipId) || Boolean(releaseKey);
  const planId = videoId && parts[2] === "candidates" ? Number(params.get("clip")) || null : null;
  // Соседи по видео на ступени «Клип»: один пункт на клип, а не на план.
  const cards = useQuery({
    queryKey: clipGroupKeys.source(videoId),
    queryFn: () => clipGroupsApi.forSource(videoId),
    enabled: open === 2 && Boolean(videoId),
    staleTime: 15_000,
  });

  const videos = useQuery({
    queryKey: navKeys.videos(projectId),
    queryFn: () => navApi.videos(projectId),
    enabled: open === 1 && hasProject && (head === "p" || Boolean(clipId) || Boolean(releaseKey) || source.isSuccess),
    staleTime: 15_000,
  });

  // Адрес сменился — меню закрывается; клик мимо и Esc тоже.
  useEffect(() => setOpen(null), [location.pathname, location.search]);
  useEffect(() => {
    if (open == null) return;
    const close = () => setOpen(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const projectMenu: MenuItem[] = useMemo(
    () => [
      ...(nav?.projects ?? []).map((p) => ({
        to: projectPath(p.id),
        label: p.name,
        hint: p.videos ? String(p.videos) : "",
        cur: p.id === projectId,
      })),
      { to: "/projects", label: "Все проекты и видео", hint: nav?.no_project_videos ? `вне проектов: ${nav.no_project_videos}` : "", cur: head === "projects", action: true },
    ],
    [nav, projectId, head],
  );

  const rungs = useMemo<{ list: Rung[]; active: number }>(() => {
    if (hasProject) {
      const view = clip.data;
      const rel = release.data;
      const projectName = (head === "v" && source.isLoading) || ((clipId || editClipId) && clip.isLoading) || (releaseKey && release.isLoading) ? "…" : project?.name ?? "Вне проектов";
      const videoCount = project?.videos ?? nav?.no_project_videos ?? 0;
      const plans = (source.data?.clip_plans ?? []).filter((p) => !p.hidden);
      const plan = planId ? source.data?.clip_plans.find((p) => p.id === planId) : null;
      // Холст и загруженный файл лежат на служебном источнике: ступени «видео» у них нет.
      const noVideo = Boolean(view?.canvas || view?.uploaded);
      const shownVideo = hiddenVideo ? "" : videoId || (view && !noVideo ? String(view.source.id) : "");
      const shownVideoName = hiddenVideo
        ? KIND_LABELS[source.data?.source_type ?? ""]
        : videoId
          ? source.data?.display_name || source.data?.original_filename || "…"
          : view
            ? view.uploaded
              ? "Загруженный клип"
              : view.canvas
                ? "Холст"
                : view.source.name
            : "…";
      const cardItems = cards.data?.clips.filter((c) => !c.hidden) ?? [];
      const newVideo = head === "p" && parts[2] === "new";
      // Клип из выпуска: средняя ступень — выпуск, а не служебный источник холстов.
      const viewRelease = view?.release ?? null;
      const clipRung: Rung = releaseKey
        ? {
            cap: "Клип",
            val: rel ? (rel.clips.length ? `Выберите: ${plural(rel.clips.length, "клип", "клипа", "клипов")}` : "Клипа ещё нет") : "…",
            next: true,
            items: (rel?.clips ?? []).map((c) => ({ to: clipPath(c.id), label: c.name, hint: c.format })),
            searchLabel: "Найти клип",
          }
        : newVideo
          ? { cap: "Клип", val: "Сначала видео", next: true }
          : clipId
        ? {
            cap: "Клип",
            val: view ? `${view.name}, ${view.format}` : "…",
            items: [
              ...(view?.neighbors ?? []).map((n) => ({
                to: clipPath(n.id),
                label: n.name,
                hint: n.format,
                cur: n.id === view?.id,
              })),
              ...(view && !noVideo ? [{ to: videoPath(view.source.id), label: "Все клипы этого видео", action: true }] : []),
            ],
            searchLabel: "Найти клип",
          }
        : {
            cap: plan ? "Клип, монтаж" : "Клип",
            val: plan
              ? plan.title || `Клип #${plan.id}`
              : videoId
                ? `Выберите: ${plural(countClips(plans), "клип", "клипа", "клипов")}`
                : "Сначала видео",
            href: plan ? clipPath(plan.id) : undefined,
            next: !plan,
            items: hiddenVideo
              ? (view?.neighbors ?? []).map((n) => ({ to: clipPath(n.id), label: n.name, hint: n.format, cur: n.id === view?.id }))
              : videoId
              ? [
                  ...cardItems.map((c) => ({
                    to: clipPath(c.id),
                    label: c.name,
                    hint: c.favorite ? `★ ${c.format}` : c.format,
                    cur: planId != null && c.plan_ids.includes(planId),
                  })),
                  { to: videoPath(videoId), label: "Все клипы этого видео", action: true },
                ]
              : [],
            loading: open === 2 && cards.isLoading,
            searchLabel: "Найти клип",
          };
      const videoItems: MenuItem[] = [
        ...(videos.data?.videos ?? []).map((v) => ({
          to: videoPath(v.id),
          label: v.name,
          hint: v.clips ? String(v.clips) : "",
          cur: String(v.id) === videoId,
        })),
        ...(videos.data?.generated ?? []).map((g) => ({
          to: g.to,
          label: g.name || g.kind,
          hint: g.kind,
          cur: g.key === (releaseKey || viewRelease?.key),
        })),
      ];
      const onRelease = Boolean(releaseKey || viewRelease);
      const chosen = videoId || clipId || releaseKey;
      const midCap = onRelease ? "Выпуск" : videoId || (view && !noVideo) ? "Видео" : "Видео или выпуск";
      const midVal = newVideo
        ? "Новое видео"
        : releaseKey
          ? rel?.name ?? "…"
          : viewRelease
            ? viewRelease.name
            : chosen
              ? shownVideoName
              : `Выберите: ${videoCount}`;
      const midHref = releaseKey
        ? undefined
        : viewRelease
          ? viewRelease.steps_to ?? undefined
          : shownVideo
            ? videoPath(shownVideo)
            : undefined;
      return {
        active: plan || clipId ? 2 : videoId || releaseKey || newVideo ? 1 : 0,
        list: [
          {
            cap: "Проект",
            val: projectName,
            href: projectId ? projectPath(projectId) : "/projects",
            mute: !project && (head === "v" ? !source.isLoading && !clip.isLoading : Boolean(clipId)),
            items: projectMenu,
            searchLabel: "Найти проект",
          },
          {
            cap: midCap,
            val: midVal,
            href: midHref,
            next: !chosen && !newVideo,
            items: [{ to: "/", label: "+ Новое видео", action: true, onSelect: onCreateVideo }, ...videoItems],
            loading: open === 1 && videos.isLoading,
            searchLabel: "Найти видео или выпуск",
          },
          clipRung,
        ],
      };
    }
    if (head === "new") {
      return {
        active: 1,
        list: [
          { cap: "Проект", val: "Выберите на странице", items: projectMenu, searchLabel: "Найти проект" },
          { cap: "Видео или выпуск", val: "Новое видео" },
          { cap: "Клип", val: "Сначала видео", next: true },
        ],
      };
    }
    if (head === "projects") {
      return {
        active: 0,
        list: [
          { cap: "Проект", val: "Все проекты", href: "/projects", items: projectMenu, searchLabel: "Найти проект" },
          { cap: "Видео", val: "Выберите в списке", next: true },
          { cap: "Клип", val: "Сначала видео", next: true },
        ],
      };
    }
    const sectionKey = head === "publications" ? "pubs" : head;
    const section = SECTIONS[sectionKey];
    const tabs = visibleTabs(section?.tabs ?? [], on);
    const asked = sectionKey === "settings" ? parts[1] : params.get("tab");
    const tab = tabs.find((t) => t.key === asked) ?? tabs[0];
    const jobDetail = sectionKey === "pubs" && parts[1];
    return {
      active: 1,
      list: [
        { cap: "Проект", val: "Вне проектов", mute: true, items: projectMenu, searchLabel: "Найти проект" },
        {
          cap: "Раздел",
          val: section?.title ?? "",
          href: section?.to,
          items: SECTION_ORDER.map((key) => ({
            to: SECTIONS[key].to,
            label: SECTIONS[key].title,
            cur: key === sectionKey,
          })),
        },
        {
          cap: jobDetail ? "Публикация" : "Вкладка",
          val: jobDetail ? `№ ${parts[1]}` : tab?.label ?? "Весь раздел",
          mute: !tab && !jobDetail,
          items: tabs.map((t) => ({ to: t.to, label: t.label, cur: t.key === tab?.key })),
        },
      ],
    };
  }, [hasProject, head, parts, params, on, source.isLoading, source.data, project, nav, planId, videos.data, videos.isLoading, open, videoId, projectId, projectMenu, onCreateVideo, clipId, editClipId, hiddenVideo, clip.data, clip.isLoading, cards.data, cards.isLoading, releaseKey, release.data, release.isLoading]);

  return (
    <nav className="lad" aria-label="Где вы находитесь">
      {rungs.list.map((g, i) => {
        const hasMenu = Boolean(g.items?.length) || (i === 1 && hasProject) || (i === 2 && Boolean(videoId));
        return (
          <div className="lad-rungw" key={g.cap + i}>
            <button
              type="button"
              className={`lad-rung${i === rungs.active ? " now" : ""}${g.next ? " next" : ""}${g.mute ? " mute" : ""}`}
              aria-current={i === rungs.active ? "location" : undefined}
              onClick={(e) => {
                e.stopPropagation();
                // Текущая ступень и ступень без адреса открывают соседей; остальные ведут на свой экран.
                if (g.href && i !== rungs.active) {
                  setOpen(null);
                  navigate(g.href);
                } else if (hasMenu) setOpen((v) => (v === i ? null : i));
              }}
            >
              <small>{g.cap}</small>
              <b title={g.val}>{g.val}</b>
            </button>
            {hasMenu ? (
              <button
                type="button"
                className="lad-chev"
                aria-label={`Соседние: ${g.cap}`}
                aria-expanded={open === i}
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen((v) => (v === i ? null : i));
                }}
              >
                <Icon name="down" size={16} />
              </button>
            ) : null}
            {open === i ? <Menu rung={g} onClose={() => setOpen(null)} /> : null}
          </div>
        );
      })}
    </nav>
  );
}
