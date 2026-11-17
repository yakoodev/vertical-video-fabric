import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { platformName, platformShort } from "@/api/feed";
import {
  blockError,
  blockOk,
  homeApi,
  homeKeys,
  type Block,
  type HomeExample,
  type HomeProject,
  type HomeSummary,
  type HomeWaiting,
} from "@/api/home";
import { qk } from "@/api/keys";
import { tasksApi } from "@/api/tasks";
import type { ActiveTask } from "@/api/types";
import { useShell } from "@/components/AppShell";
import { ErrorNote } from "@/components/ErrorNote";
import { Icon } from "@/components/Icon";
import { ErrorState, Loading, plural } from "@/components/ui";
import { isActive } from "@/hooks/useActiveTasks";
import { useModules } from "@/hooks/useModules";
import { newVideoPath, projectPath } from "@/lib/paths";
import { ago, mskTime, utcDate } from "@/lib/time";
import { requestsWord, rub } from "@/lib/usage";
import "@/styles/feed.css";
import "@/styles/home.css";

/**
 * «Главная»: короткая сводка с телефона — что ждёт решения, свежие работы, что скоро выйдет и что зашло, что идёт
 * сейчас, траты месяца, проекты и примеры. Данные — одна ручка GET /api/home (app/home.py); упавший блок
 * показывает причину, остальные на месте. Каждый блок ведёт в свой раздел.
 */

function greeting(now: Date): string {
  const h = now.getHours();
  if (h >= 5 && h < 12) return "Доброе утро";
  if (h >= 12 && h < 18) return "Добрый день";
  if (h >= 18 && h < 23) return "Добрый вечер";
  return "Доброй ночи";
}

const compact = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });
const views = (n: number | null) => (n == null ? "—" : compact.format(n));

function Section({
  title,
  to,
  more,
  className,
  children,
}: {
  title: string;
  to?: string;
  more?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`home-sec${className ? ` ${className}` : ""}`}>
      <header className="home-sec-h">
        <h2>{title}</h2>
        {to ? (
          <Link to={to} className="home-more">
            {more ?? "Все"}
            <Icon name="arrow" size={14} />
          </Link>
        ) : null}
      </header>
      {children}
    </section>
  );
}

function Failed({ error }: { error: string }) {
  return <p className="home-err">Этот блок не загрузился: {error}</p>;
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="home-empty">{children}</p>;
}

/* ---------- быстрые действия ---------- */

function QuickActions() {
  const { on } = useModules();
  const { openNewProject } = useShell();
  const links: { label: string; to: string; icon: string; module?: string }[] = [
    { label: "Лента", to: "/feed", icon: "feed" },
    { label: "Мувик под песню", to: newVideoPath(null, "frag"), icon: "bolt", module: "frag_movie" },
    { label: "AMV", to: newVideoPath(null, "amv"), icon: "film", module: "amv" },
    { label: "Клип на песню", to: newVideoPath(null, "mv"), icon: "play", module: "music_video" },
    { label: "Объяснялка", to: newVideoPath(null, "expl"), icon: "chat", module: "explainer" },
  ];
  return (
    <div className="home-actions">
      <Link to={newVideoPath(null)} className="home-act home-act--primary">
        <Icon name="plus" size={17} />
        Новое видео
      </Link>
      {on("series") ? (
        <button type="button" className="home-act home-act--strong" onClick={openNewProject}>
          <Icon name="folder" size={17} />
          Новый проект
        </button>
      ) : null}
      {links
        .filter((l) => !l.module || on(l.module))
        .map((l) => (
          <Link key={l.label} to={l.to} className="home-act">
            <Icon name={l.icon} size={16} />
            {l.label}
          </Link>
        ))}
    </div>
  );
}

/* ---------- ждёт тебя ---------- */

type Tile = { key: string; label: string; count: number; to: string; tone: string; hint?: string };

function waitingTiles(w: HomeWaiting): Tile[] {
  const tiles: Tile[] = [];
  if (w.feed) {
    tiles.push({ key: "ready", label: "Готово, не выложено", count: w.feed.ready_unposted, to: "/feed?f=ready_unposted", tone: "ok" });
    tiles.push({ key: "review", label: "На проверке", count: w.feed.review, to: "/feed?f=review", tone: "warn" });
  }
  if (w.approvals) {
    const first = w.approvals.first[0];
    tiles.push({
      key: "approve",
      label: "Одобрение",
      count: w.approvals.count,
      to: "/inbox?tab=approve",
      tone: "accent",
      hint: first ? first.project_name || first.title : undefined,
    });
  }
  if (w.questions) {
    const n = w.questions.questions + w.questions.memes;
    tiles.push({ key: "questions", label: "Вопросы по стримам", count: n, to: "/inbox?tab=questions", tone: "info" });
  }
  if (w.chat) {
    const first = w.chat.first[0];
    tiles.push({
      key: "chat",
      label: "Агент ждёт ответа",
      count: w.chat.count,
      to: first ? `/chat/${first.thread_id}` : "/chat",
      tone: "accent",
      hint: first ? first.label || first.project_name || first.thread_title : undefined,
    });
  }
  if (w.failed) {
    tiles.push({ key: "failed", label: `Упало за ${w.failed.days} дн.`, count: w.failed.count, to: "#home-failed", tone: "danger" });
  }
  return tiles;
}

function Waiting({ block }: { block: Block<HomeWaiting> }) {
  const err = blockError(block);
  const w = blockOk(block);
  return (
    <Section title="Ждёт тебя" to="/inbox" more="Входящие" className="home-span">
      {err || !w ? (
        <Failed error={err ?? "нет данных"} />
      ) : (
        <>
          {w.total === 0 ? <Empty>Всё разобрано: нечего проверять, одобрять и чинить.</Empty> : null}
          <div className="home-tiles">
            {waitingTiles(w).map((t) => {
              const body = (
                <>
                  <b className="home-tile-n">{t.count}</b>
                  <span className="home-tile-l">{t.label}</span>
                  {t.hint && t.count ? <span className="home-tile-h">{t.hint}</span> : null}
                </>
              );
              const cls = `home-tile home-tile--${t.tone}${t.count ? "" : " is-zero"}`;
              return t.to.startsWith("#") ? (
                <a key={t.key} href={t.to} className={cls}>
                  {body}
                </a>
              ) : (
                <Link key={t.key} to={t.to} className={cls}>
                  {body}
                </Link>
              );
            })}
          </div>
          {w.failed?.items.length ? (
            <ul className="home-list home-failed" id="home-failed">
              {w.failed.items.map((f) => (
                <li key={`${f.kind}:${f.id}`}>
                  <Link to={f.to} className="home-row">
                    <span className="home-row-tag home-row-tag--danger">{f.kind === "job" ? (f.status === "needs_reauth" ? "Нужен вход" : "Публикация") : "Рендер"}</span>
                    <span className="home-row-main">
                      <span className="home-row-t">{f.label || `#${f.id}`}</span>
                      {f.error_view ? (
                        <ErrorNote raw={f.error} view={f.error_view} inLink className="home-row-s home-row-err" />
                      ) : f.error ? (
                        <span className="home-row-s">{f.error}</span>
                      ) : null}
                    </span>
                    <span className="home-row-side">{ago(f.at)}</span>
                  </Link>
                  {/* Строка — ссылка: сырой текст площадки раскрывается под ней, а не внутри. */}
                  {f.error_view && f.error ? (
                    <details className="home-row-raw">
                      <summary>подробнее</summary>
                      <pre>{f.error}</pre>
                    </details>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          {w.errors ? <Failed error={Object.entries(w.errors).map(([k, v]) => `${k}: ${v}`).join("; ")} /> : null}
        </>
      )}
    </Section>
  );
}

/* ---------- последние работы ---------- */

function RecentWorks({ block }: { block: HomeSummary["recent_works"] }) {
  const err = blockError(block);
  const data = blockOk(block);
  return (
    <Section title="Последние работы" to="/feed" more="Лента" className="home-span">
      {err || !data ? (
        <Failed error={err ?? "нет данных"} />
      ) : !data.items.length ? (
        <Empty>Лента пока пустая — готовые работы появятся здесь, когда агент сдаст первую версию.</Empty>
      ) : (
        <div className="home-works">
          {data.items.map((w) => (
            <Link key={w.id} to={`/feed?work=${w.id}`} className="home-work">
              <span className="home-work-cover">
                {w.cover_url ? <img src={w.cover_url} alt="" loading="lazy" /> : <Icon name="film" size={24} />}
                {w.ready_unposted ? <span className="home-work-flag">не выложено</span> : null}
              </span>
              <span className="home-work-t">{w.title}</span>
              <span className="home-work-m">
                {w.latest ? <span className={`feed-st feed-st--${w.latest.status}`}>{w.latest.status_label}</span> : null}
                {w.posted_platforms.map((p) => (
                  <span key={p} className={`feed-plat feed-plat--${p}`} title={`Выложено: ${platformName(p)}`}>
                    {platformShort(p)}
                  </span>
                ))}
              </span>
              <span className="home-work-s">{[w.kind_label, ago(w.activity_at)].filter(Boolean).join(" · ")}</span>
            </Link>
          ))}
        </div>
      )}
    </Section>
  );
}

/* ---------- скоро выйдет и что зашло ---------- */

function Upcoming({ block }: { block: HomeSummary["upcoming"] }) {
  const { on } = useModules();
  const err = blockError(block);
  const data = blockOk(block);
  return (
    <Section title="Скоро выйдет" to={on("publishing") ? "/pubs?tab=pubs" : undefined} more="Публикации">
      {err || !data ? (
        <Failed error={err ?? "нет данных"} />
      ) : !data.items.length ? (
        <Empty>По расписанию ничего не стоит.</Empty>
      ) : (
        <ul className="home-list">
          {data.items.map((u) => (
            <li key={u.job_id}>
              <Link to={u.to} className="home-row">
                <span className="home-row-time">{mskTime(u.scheduled_at)}</span>
                <span className="home-row-main">
                  <span className="home-row-t">{u.title}</span>
                  <span className="home-row-s">
                    {[
                      u.targets.map((t) => `${platformShort(t.platform)}${t.account ? ` · ${t.account}` : ""}`).join(", "),
                      u.project_name,
                    ]
                      .filter(Boolean)
                      .join(" — ")}
                  </span>
                </span>
              </Link>
            </li>
          ))}
          {data.total > data.items.length ? <li className="home-list-more">и ещё {data.total - data.items.length}</li> : null}
        </ul>
      )}
    </Section>
  );
}

function TopWeek({ block }: { block: HomeSummary["top_week"] }) {
  const err = blockError(block);
  const data = blockOk(block);
  return (
    <Section title="Что зашло за неделю">
      {err || !data ? (
        <Failed error={err ?? "нет данных"} />
      ) : !data.items.length ? (
        <Empty>
          {data.published
            ? `За неделю вышло ${plural(data.published, "видео", "видео", "видео")}, числа ещё не собраны.`
            : "За неделю ничего не выходило."}
        </Empty>
      ) : (
        <>
          <ol className="home-list home-top">
            {data.items.map((t, i) => {
              const inner = (
                <>
                  <span className="home-top-n">{i + 1}</span>
                  <span className="home-row-main">
                    <span className="home-row-t">{t.title || "без названия"}</span>
                    <span className="home-row-s">
                      {[platformName(t.platform), t.account, ago(t.published_at)].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  <span className="home-top-v">
                    <b>{views(t.views)}</b>
                    {t.likes != null ? <small>♥ {views(t.likes)}</small> : null}
                  </span>
                </>
              );
              return (
                <li key={`${t.kind}:${t.id}`}>
                  {t.to.startsWith("/") ? (
                    <Link to={t.to} className="home-row">
                      {inner}
                    </Link>
                  ) : (
                    <a href={t.to || t.url} target="_blank" rel="noreferrer" className="home-row">
                      {inner}
                    </a>
                  )}
                </li>
              );
            })}
          </ol>
          <p className="home-note">
            Всего {views(data.total_views)} просмотров у {plural(data.published, "публикации", "публикаций", "публикаций")} за {data.days} дней
          </p>
        </>
      )}
    </Section>
  );
}

/* ---------- работает сейчас и траты ---------- */

const TASK_KIND: Record<string, string> = {
  job: "Публикация",
  clip: "Рендер",
  analysis: "Анализ",
  download: "Скачивание",
  torrent: "Торрент",
};

const HEAVY_KIND: Record<string, string> = {
  render: "рендер",
  whisper: "распознавание речи",
  autofocus: "автофокус",
  action: "разбор действия",
  amv: "AMV",
  audio_map: "карта звука",
  color_match: "подбор цвета",
  demucs: "разделение вокала",
  feed_preview: "превью ленты",
  game_events: "события игры",
  music_video: "клип на песню",
  publish_master: "файл для площадки",
  tracking: "трекинг",
};

function taskLink(t: ActiveTask): string {
  if (t.kind === "job") return `/pubs/${t.id}`;
  if (t.source_id) return `/v/${t.source_id}`;
  return "/inbox?tab=tasks";
}

function Working({ block }: { block: HomeSummary["working"] }) {
  // Тот же запрос и кэш, что у «Задач» в шапке: второй опрос не появляется.
  const tasks = useQuery({ queryKey: qk.activeTasks, queryFn: tasksApi.active });
  // Публикация, назначенная на будущее, ещё не идёт: она в «Скоро выйдет», не здесь.
  const now = Date.now();
  const active = (tasks.data ?? []).filter(
    (t) => isActive(t.status) && !(t.kind === "job" && t.status === "queued" && (utcDate(t.scheduled_at)?.getTime() ?? 0) > now),
  );
  const heavy = blockOk(block)?.heavy;
  return (
    <Section title="Работает сейчас" to="/inbox?tab=tasks" more="Очередь">
      {heavy && (heavy.running.length || heavy.waiting) ? (
        <p className="home-heavy">
          <span className="pulse-dot" />
          Тяжёлая очередь:{" "}
          {heavy.running.map((r) => `${HEAVY_KIND[r.kind] ?? r.kind}${r.label ? ` «${r.label}»` : ""}`).join(", ") || "свободна"}
          {heavy.waiting ? `, ждут ${heavy.waiting}` : ""}
        </p>
      ) : null}
      {tasks.isLoading ? (
        <Loading />
      ) : !active.length ? (
        <Empty>Сейчас ничего не идёт.</Empty>
      ) : (
        <ul className="home-list">
          {active.slice(0, 6).map((t) => (
            <li key={`${t.kind}:${t.id}`}>
              <Link to={taskLink(t)} className="home-row home-row--task">
                <span className="home-row-tag">{TASK_KIND[t.kind] ?? t.kind}</span>
                <span className="home-row-main">
                  <span className="home-row-t">{t.label || `#${t.id}`}</span>
                  {t.progress != null ? (
                    <span className="dl-progress">
                      <span className="dl-bar">
                        <span style={{ width: `${Math.round(t.progress)}%` }} />
                      </span>
                      <span className="mono">{Math.round(t.progress)}%</span>
                    </span>
                  ) : (
                    <span className="home-row-s">{ago(t.updated_at)}</span>
                  )}
                </span>
              </Link>
            </li>
          ))}
          {active.length > 6 ? <li className="home-list-more">и ещё {active.length - 6}</li> : null}
        </ul>
      )}
    </Section>
  );
}

function Spend({ block }: { block: HomeSummary["spend"] }) {
  const err = blockError(block);
  const data = blockOk(block);
  const gem = data ? blockOk(data.gemini) : null;
  const polza = data ? blockOk(data.polza) : null;
  const share = polza && polza.cap_rub > 0 ? Math.min(1, polza.spent_rub / polza.cap_rub) : 0;
  const reservedShare = polza && polza.cap_rub > 0 ? Math.min(1 - share, polza.reserved_rub / polza.cap_rub) : 0;
  const month = new Date().toLocaleDateString("ru-RU", { month: "long" });
  return (
    <Section title={`Траты за ${month}`} to="/settings/usage" more="Подробно">
      {err || !data ? (
        <Failed error={err ?? "нет данных"} />
      ) : (
        <div className="home-spend">
          <div className="home-meter">
            <div className="home-meter-h">
              <span>Gemini</span>
              <b>{gem ? rub(gem.cost_rub) : "—"}</b>
            </div>
            {gem ? (
              <span className="home-meter-s">
                {gem.calls.toLocaleString("ru-RU")} {requestsWord(gem.calls)}
                {gem.errors ? `, ошибок ${gem.errors}` : ""} · без потолка
              </span>
            ) : (
              <Failed error={blockError(data.gemini) ?? "нет данных"} />
            )}
          </div>
          <div className="home-meter">
            <div className="home-meter-h">
              <span>Polza</span>
              <b>
                {polza ? rub(polza.spent_rub) : "—"}
                {polza ? <small> из {rub(polza.cap_rub)}</small> : null}
              </b>
            </div>
            {polza ? (
              <>
                <span className={`home-bar${share >= 0.9 ? " is-hot" : ""}`} role="meter" aria-valuenow={polza.spent_rub} aria-valuemin={0} aria-valuemax={polza.cap_rub}>
                  <span style={{ width: `${share * 100}%` }} />
                  {reservedShare ? <i style={{ width: `${reservedShare * 100}%` }} /> : null}
                </span>
                <span className="home-meter-s">
                  картинки, видео, голос{polza.reserved_rub ? ` · зарезервировано ${rub(polza.reserved_rub)}` : ""} · потолок
                  месяца на все проекты{polza.enabled ? "" : " · платная генерация выключена"}
                </span>
              </>
            ) : (
              <Failed error={blockError(data.polza) ?? "нет данных"} />
            )}
          </div>
        </div>
      )}
    </Section>
  );
}

/* ---------- проекты ---------- */

function projectLine(p: HomeProject): string {
  if (p.next) return `выйдет ${mskTime(p.next.scheduled_at)}`;
  if (p.last_activity_at) return `последнее: ${ago(p.last_activity_at)}`;
  return "пока пусто";
}

function Projects({ block }: { block: HomeSummary["projects"] }) {
  const { on, ready } = useModules();
  const { openNewProject } = useShell();
  const err = blockError(block);
  const data = blockOk(block);
  if (ready("series") && !on("series")) return null;
  return (
    <Section title="Проекты" to="/projects" more="Все проекты" className="home-span">
      {err || !data ? (
        <Failed error={err ?? "нет данных"} />
      ) : !data.items.length ? (
        <div className="home-empty-cta">
          <Empty>Проектов пока нет. Проект — канал, сериал или тема: у него свои видео, лор, правила и автопилот.</Empty>
          <button type="button" className="btn primary sm" onClick={openNewProject}>
            <Icon name="plus" size={15} /> Новый проект
          </button>
        </div>
      ) : (
        <div className="home-projects">
          {data.items.map((p) => (
            <Link key={p.id} to={projectPath(p.id)} className={`home-proj${p.next ? " is-soon" : ""}`}>
              <span className="home-proj-t">{p.name.replace(/\s*\(.*\)$/, "") || p.name}</span>
              <span className="home-proj-s">{projectLine(p)}</span>
              <span className="home-proj-f">
                <span>{plural(p.videos, "видео", "видео", "видео")}</span>
                {p.approvals ? <span className="home-proj-b">{p.approvals} на одобрение</span> : null}
                {p.open_questions ? <span className="home-proj-b home-proj-b--q">{p.open_questions} вопр.</span> : null}
              </span>
            </Link>
          ))}
        </div>
      )}
    </Section>
  );
}

/* ---------- примеры ---------- */

const EXAMPLE_ICON: Record<string, string> = { frag: "bolt", amv: "film", mv: "play", expl: "chat", "3d": "layers" };

function ExampleCard({ e }: { e: HomeExample }) {
  const ex = e.example;
  const cover = (
    <span className="home-ex-cover">
      {ex?.poster_url ? <img src={ex.poster_url} alt="" loading="lazy" /> : <Icon name={EXAMPLE_ICON[e.type] ?? "star"} size={30} />}
      {ex ? <span className="home-ex-src">{ex.source === "template" ? "шаблон" : ex.source === "release" ? "выпуск" : "из ленты"}</span> : null}
    </span>
  );
  return (
    <article className="home-ex">
      {ex?.to ? (
        <Link to={ex.to} className="home-ex-link" title={`Открыть пример: ${ex.title}`}>
          {cover}
        </Link>
      ) : (
        cover
      )}
      <div className="home-ex-body">
        <b className="home-ex-t">{e.title}</b>
        {ex ? (
          <span className="home-ex-w" title={ex.title}>
            {ex.to ? <Link to={ex.to}>{ex.title}</Link> : ex.title}
            {ex.version ? ` · ${ex.version}` : ""}
          </span>
        ) : (
          <span className="home-ex-w muted">примера пока нет</span>
        )}
        <span className="home-ex-d">{e.text}</span>
        <Link to={newVideoPath(null, e.type)} className="btn sm home-ex-go">
          Сделать так же
          <Icon name="arrow" size={14} />
        </Link>
      </div>
    </article>
  );
}

function Examples({ block }: { block: HomeSummary["examples"] }) {
  const err = blockError(block);
  const data = blockOk(block);
  if (data && !data.items.length) return null;
  return (
    <Section title="Примеры: что можно сделать" to={newVideoPath(null)} more="Все типы" className="home-span">
      {err || !data ? (
        <Failed error={err ?? "нет данных"} />
      ) : (
        <div className="home-examples">
          {data.items.map((e) => (
            <ExampleCard key={e.key} e={e} />
          ))}
        </div>
      )}
    </Section>
  );
}

/* ---------- страница ---------- */

export function HomePage() {
  const q = useQuery({ queryKey: homeKeys.all, queryFn: homeApi.get, refetchInterval: 60_000, staleTime: 15_000 });
  const now = new Date();
  const date = now.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  const waiting = q.data ? blockOk(q.data.waiting) : null;
  return (
    <div className="home">
      <header className="home-hero">
        <div>
          <h1>{greeting(now)}</h1>
          <p className="home-date">
            {date.charAt(0).toUpperCase() + date.slice(1)}
            {waiting ? (waiting.total ? ` · ждёт тебя: ${waiting.total}` : " · всё разобрано") : ""}
          </p>
        </div>
        <QuickActions />
      </header>
      {q.isLoading ? (
        <Loading />
      ) : q.isError || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <div className="home-grid">
          <Waiting block={q.data.waiting} />
          <RecentWorks block={q.data.recent_works} />
          <Upcoming block={q.data.upcoming} />
          <TopWeek block={q.data.top_week} />
          <Working block={q.data.working} />
          <Spend block={q.data.spend} />
          <Projects block={q.data.projects} />
          <Examples block={q.data.examples} />
        </div>
      )}
    </div>
  );
}
