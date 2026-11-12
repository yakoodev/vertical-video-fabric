/**
 * Единственный список вкладок интерфейса. Из него строятся сами вкладки, лестница, Ctrl K, цифры
 * на клавиатуре и подсказка «?». Новую вкладку или новое название менять только здесь.
 */

export type TabDef = {
  key: string;
  label: string;
  /** Модуль вкладки (app/modules.py): выключен, значит вкладки нет. */
  module?: string;
  /** Как вкладка называется в Ctrl K, если там нужно уточнение. */
  cmd?: string;
};

type IsOn = (module: string) => boolean;

export const visibleTabs = <T extends { module?: string }>(tabs: readonly T[], on: IsOn): T[] =>
  tabs.filter((tab) => !tab.module || on(tab.module));

/* ---------- Видео: /v/:id/<key> ---------- */

export type VideoTab = TabDef & {
  /** Убрана за кнопку «Ещё». */
  more?: boolean;
};

export const VIDEO_TABS: readonly VideoTab[] = [
  // Сама страница видео: запись, транскрипт, чат и сетка клипов (один элемент на клип).
  { key: "clips", label: "Клипы", cmd: "Клипы этого видео" },
  { key: "source", label: "Запись и анализ" },
  { key: "candidates", label: "Черновики" },
  { key: "questions", label: "Вопросы", cmd: "Вопросы этого видео", module: "stream_questions" },
  { key: "segments", label: "Сегменты", more: true },
  // Каждый готовый файл отдельно: переименовать, удалить, опубликовать пачкой.
  { key: "renders", label: "Рендеры", cmd: "Рендеры этого видео", more: true },
  { key: "montaged", label: "Смонтированные", more: true },
];

/* ---------- Проект: /p/:id?tab=<key> ---------- */

export type ProjectTabKey = "videos" | "clips" | "questions" | "files" | "materials" | "chat" | "know" | "stats" | "autopilot";

export type ProjectTab = TabDef & { key: ProjectTabKey };

export const PROJECT_TABS: readonly ProjectTab[] = [
  { key: "videos", label: "Видео", cmd: "Видео проекта" },
  { key: "clips", label: "Клипы", cmd: "Клипы проекта" },
  { key: "questions", label: "Вопросы", cmd: "Вопросы проекта", module: "stream_questions" },
  { key: "files", label: "Файлы", cmd: "Файлы проекта" },
  // Маскоты и позы, миры и актёры, стиль, персонажи и бюджет ИИ-видео, темы канала (решение 16).
  { key: "materials", label: "Материалы", cmd: "Материалы проекта: маскоты, миры, персонажи" },
  { key: "chat", label: "Чат", cmd: "Чат проекта", module: "agent_chat" },
  { key: "know", label: "Лор и правила", cmd: "Что агент знает, лор и правила" },
  { key: "stats", label: "Статистика" },
  // Без модуля автопилота на вкладке остаётся раздел «Как работает агент»: пауза и список действуют и без него.
  { key: "autopilot", label: "Автопилот" },
];

/** Вкладки проекта. Набор один у всех проектов: «Материалы» нужны, только если включён хотя бы один генератор. */
export function projectTabs(on: IsOn): ProjectTab[] {
  const generators = on("explainer") || on("stories") || on("ai_video") || on("series3d");
  return visibleTabs(PROJECT_TABS, on).filter((tab) => tab.key !== "materials" || generators);
}

/** Разделы «Материалов»: ?tab=materials&mat=<key>. */
export const MATERIAL_TABS = [
  { key: "mascots", label: "Маскоты и позы", module: "explainer" },
  { key: "worlds", label: "Миры, актёры, сцены", module: "stories" },
  { key: "s3d", label: "3D-серия", module: "series3d" },
  { key: "aiv", label: "ИИ-видео: стиль, персонажи, бюджет", module: "ai_video" },
  { key: "channel", label: "Канал: темы и расписание", module: "explainer" },
] as const;

/**
 * Старые вкладки проекта (закладки, ответы агента, Telegram). Ролики и эпизоды теперь строки вкладки «Видео»,
 * настройки и библиотеки — «Материалы»; ?sub= у ИИ-видео ведёт в тот же раздел материалов.
 */
export function movedProjectTab(tab: string | null, sub: string | null): Record<string, string> | null {
  if (tab === "expl") return { tab: "videos", kind: "expl" };
  if (tab === "story") return { tab: "videos", kind: "story" };
  if (tab === "aiv") return sub ? { tab: "materials", mat: "aiv", sub } : { tab: "videos", kind: "aiv" };
  if (tab === "channel") return { tab: "materials", mat: "channel" };
  return null;
}

/** Подвкладки «Лор и правила»: те же адреса ?tab=brief|rules|hyp, что были у отдельных вкладок. */
export const LORE_TABS = [
  { key: "know", label: "Что агент знает" },
  { key: "brief", label: "Резюме и лор" },
  { key: "rules", label: "Правила" },
  { key: "hyp", label: "Гипотезы" },
] as const;

/* ---------- Разделы вне проектов ---------- */

export type SectionTab = TabDef & { to: string };
export type Section = { title: string; to: string; tabs: readonly SectionTab[] };

export const SECTIONS: Record<string, Section> = {
  inbox: {
    title: "Входящие",
    to: "/inbox",
    tabs: [
      { key: "approve", label: "Одобрение", to: "/inbox?tab=approve", module: "autopilot" },
      { key: "questions", label: "Вопросы", to: "/inbox?tab=questions", module: "stream_questions" },
      { key: "tasks", label: "Очередь задач", to: "/inbox?tab=tasks" },
    ],
  },
  pubs: {
    title: "Публикации и аккаунты",
    to: "/pubs",
    tabs: [
      { key: "pubs", label: "Публикации", to: "/pubs?tab=pubs", module: "publishing" },
      { key: "accounts", label: "Аккаунты", to: "/pubs?tab=accounts", module: "publishing" },
      { key: "ready", label: "Готовые ролики", to: "/pubs?tab=ready" },
    ],
  },
  files: {
    title: "Общие файлы",
    to: "/files",
    tabs: [
      { key: "shared", label: "Общие", to: "/files" },
      { key: "project", label: "По проектам", to: "/files?tab=project" },
      { key: "all", label: "Все файлы", to: "/files?tab=all" },
    ],
  },
  settings: {
    title: "Настройки",
    to: "/settings",
    tabs: [
      { key: "render", label: "Пресеты рендера", to: "/settings/render" },
      { key: "banners", label: "Баннеры", to: "/settings/banners" },
      { key: "audio", label: "Музыка", to: "/settings/audio" },
      { key: "subtitles", label: "Субтитры", to: "/settings/subtitles" },
      { key: "brands", label: "Бренды", to: "/settings/brands" },
      { key: "prompts", label: "Промпты", to: "/settings/prompts" },
      { key: "defaults", label: "По умолчанию", to: "/settings/defaults" },
      { key: "modules", label: "Модули", to: "/settings/modules" },
      { key: "usage", label: "Траты Gemini", to: "/settings/usage" },
      { key: "proxy", label: "Прокси", to: "/settings/proxy" },
      { key: "telegram", label: "Telegram", to: "/settings/telegram", module: "telegram" },
      { key: "backup", label: "Экспорт и импорт", to: "/settings/backup" },
    ],
  },
  help: { title: "Помощь", to: "/help", tabs: [] },
  automation: { title: "Авто", to: "/automation", tabs: [] },
  chat: { title: "Все переписки", to: "/chat", tabs: [] },
};

export const SECTION_ORDER = ["inbox", "pubs", "files", "settings", "help"];

/* ---------- Подсказка «?»: те же списки, что у цифр в AppShell ---------- */

const keysFor = (count: number) => (count > 1 ? `1 … ${count}` : "1");
const labels = (tabs: readonly TabDef[]) => tabs.map((tab) => tab.label).join(", ");

export function tabShortcuts(on: IsOn): [string, string][] {
  const project = projectTabs(on);
  const video = visibleTabs(VIDEO_TABS, on);
  const inbox = visibleTabs(SECTIONS.inbox.tabs, on);
  const pubs = visibleTabs(SECTIONS.pubs.tabs, on);
  const main = video.filter((tab) => !tab.more);
  const more = video.filter((tab) => tab.more);
  return [
    [keysFor(project.length), `вкладки проекта: ${labels(project)}`],
    [keysFor(video.length), `вкладки видео: ${labels(main)}, и из «Ещё»: ${labels(more)}`],
    [keysFor(inbox.length), `вкладки Входящих: ${labels(inbox)}`],
    [keysFor(pubs.length), `вкладки Публикаций: ${labels(pubs)}`],
  ];
}
