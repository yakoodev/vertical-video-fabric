import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { audioTracksApi, montageAssetsApi } from "@/api/assets";
import { ApiError } from "@/api/client";
import { clipGroupsApi } from "@/api/clipGroups";
import { navApi, navKeys } from "@/api/nav";
import { releaseKeys, releasesApi } from "@/api/releases";
import { Icon } from "@/components/Icon";
import { NewCanvas } from "@/components/NewCanvas";
import { useToast } from "@/components/Toast";
import { EmptyState, Loading, formatDuration } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { clipPath, projectPath, releasePath } from "@/lib/paths";
import { AiVideoNew } from "@/pages/AiVideoTab";
import { ExplainerNew } from "@/pages/ExplainerTab";
import { AddSource } from "@/pages/VideoListPage";
import { StoryNew } from "@/pages/StoriesTab";
import "@/styles/aivideo.css";
import "@/styles/explainer.css";
import "@/styles/stories.css";
import "@/styles/releases.css";

/**
 * «Новое видео» (решение 16): один путь. Тип определяет шаги, результат всегда клип с одним и тем же экраном. Цены — прикидка
 * по прошлым роликам: настоящая сумма появится на карточке перед платным шагом. Без проекта можно только файл, ссылку и холст.
 */

type TypeKey = "link" | "story" | "aiv" | "3d" | "expl" | "promo" | "canvas" | "top";
type IconName = Parameters<typeof Icon>[0]["name"];
type TypeCard = { key: TypeKey; title: string; text: string; price: string; icon: IconName; module?: string; paid?: boolean; project?: boolean };

const TYPES: TypeCard[] = [
  { key: "link", title: "Ссылка или файл", text: "Стрим, серия, запись. Скачать, распознать, нарезать на клипы.", price: "Файл можно перетащить сюда", icon: "film" },
  { key: "story", title: "История", text: "Рассказчик и картинка на фразу. Сценарий, картинки, озвучка.", price: "около 87 ₽ за 50 секунд", icon: "book", module: "stories", project: true },
  { key: "aiv", title: "ИИ-видео", text: "Ролик с нуля через видео-модели. Сценарий, персонажи, раскадровка, видео.", price: "дорого: 468 ₽ за 5 планов", icon: "zap", module: "ai_video", paid: true, project: true },
  { key: "3d", title: "3D-серия", text: "Blender, рендер на этом компьютере. Сценарий, режиссура, озвучка, рендер.", price: "0 ₽, рендер минуты", icon: "layers", module: "stories", project: true },
  { key: "expl", title: "Объяснялка", text: "Маскот объясняет тему. Тема, сценарий, позы, озвучка.", price: "около 34 ₽", icon: "chat", module: "explainer", project: true },
  { key: "promo", title: "Промо приложения", text: "Телефон с записью экрана. Материалы, сборка.", price: "0 ₽", icon: "pad", module: "promo", project: true },
  { key: "canvas", title: "Пустой холст", text: "Собрать руками. Сразу клип.", price: "0 ₽", icon: "image" },
  { key: "top", title: "ТОП-N", text: "Лучшие клипы одного видео подряд с номерами. Новый клип.", price: "0 ₽", icon: "star", module: "compilation", project: true },
];

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function NewVideoPage() {
  const params = useParams();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const { on, ready } = useModules();
  const nav = useQuery({ queryKey: navKeys.all, queryFn: navApi.get, staleTime: 15_000 });
  const routeProject = Number(params.projectId) || null;
  const [chosen, setChosen] = useState<number | null>(null);
  const projectId = routeProject ?? chosen;
  const project = nav.data?.projects.find((p) => p.id === projectId);
  const asked = search.get("type") as TypeKey | null;
  const cards = TYPES.filter((t) => !t.module || on(t.module));
  const current = cards.find((t) => t.key === asked) ?? cards[0];
  const formRef = useRef<HTMLDivElement>(null);

  // На телефоне форма выше карточек: выбрали тип — показываем её.
  useEffect(() => {
    if (asked) formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [asked]);

  const pick = (key: TypeKey) =>
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("type", key);
        return next;
      },
      { replace: true },
    );
  const opened = (to: string) => navigate(to);
  const needsProject = Boolean(current?.project && !projectId);

  let form: ReactNode = null;
  if (needsProject) {
    form = <p className="muted">Этому типу нужен проект: его материалы и бюджет живут там. Выберите проект выше.</p>;
  } else if (current?.key === "link") {
    form = (
      <>
        <p className="muted nv-hint">Скачивание пойдёт в фоне, прогресс в «Очереди задач». Видео ляжет {project ? `в проект «${project.name}»` : "вне проектов"}.</p>
        <AddSource noCanvas projectId={projectId} />
      </>
    );
  } else if (current?.key === "canvas") {
    form = <NewCanvas inline projectId={projectId} />;
  } else if (current?.key === "expl" && projectId) {
    form = <ExplainerNew projectId={projectId} onCreated={(v) => opened(releasePath(`expl-${v.id}`, true))} />;
  } else if ((current?.key === "story" || current?.key === "3d") && projectId) {
    form = <StoryNew projectId={projectId} engine={current.key === "3d" ? "3d" : "images"} onCreated={(e) => opened(releasePath(`story-${e.id}`, true))} />;
  } else if (current?.key === "aiv" && projectId) {
    form = <div className="aiv"><AiVideoNew projectId={projectId} onCreated={(s) => opened(`${releasePath(`aiv-${s.id}`, true)}&sub=script`)} /></div>;
  } else if (current?.key === "promo" && projectId) {
    form = <PromoForm projectId={projectId} />;
  } else if (current?.key === "top" && projectId) {
    form = <TopForm projectId={projectId} />;
  }

  if (!ready("series") && nav.isLoading) return <Loading />;
  return (
    <div className="nv">
      <div className="page-title">
        <h1>Новое видео{project ? ` в проекте «${project.name}»` : ""}</h1>
        <div className="page-sub">Тип определяет шаги. Результат всегда клип с одним и тем же экраном, материалы ложатся в файлы проекта.</div>
      </div>
      {routeProject ? null : (
        <label className="field nv-project">
          <span>Проект</span>
          <select className="input" value={chosen ?? ""} onChange={(e) => setChosen(Number(e.target.value) || null)}>
            <option value="">Без проекта</option>
            {(nav.data?.projects ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="nv-form" ref={formRef}>
        <h2>{current?.title}</h2>
        {form}
      </div>
      <div className="nv-grid" role="radiogroup" aria-label="Тип нового видео">
        {cards.map((t) => (
          <button
            key={t.key}
            type="button"
            role="radio"
            aria-checked={t.key === current?.key}
            data-type={t.key}
            className={`nv-card${t.key === current?.key ? " on" : ""}`}
            onClick={() => pick(t.key)}
          >
            <span className="nv-card-h">
              <Icon name={t.icon} size={20} />
              <b>{t.title}</b>
              {t.paid ? <span className="tag warn">платно</span> : null}
            </span>
            <span className="nv-card-t">{t.text}</span>
            <small>{t.price}</small>
          </button>
        ))}
      </div>
      {projectId ? (
        <p className="muted nv-foot">
          Маскоты, миры и персонажи: <Link to={`${projectPath(projectId, "materials")}`}>материалы проекта</Link>.
        </p>
      ) : null}
    </div>
  );
}

/* ---------- ТОП-N ---------- */

function TopForm({ projectId }: { projectId: number }) {
  const toast = useToast();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const videos = useQuery({ queryKey: navKeys.videos(projectId), queryFn: () => navApi.videos(projectId) });
  const list = useMemo(() => (videos.data?.videos ?? []).filter((v) => v.clips >= 2), [videos.data]);
  const [sourceId, setSourceId] = useState<number | null>(null);
  const source = sourceId ?? list[0]?.id ?? null;
  const cards = useQuery({
    queryKey: ["top-form", source],
    queryFn: () => clipGroupsApi.forSource(source as number),
    enabled: source != null,
  });
  const clips = useMemo(
    () =>
      (cards.data?.clips ?? [])
        .filter((c) => !c.hidden)
        .sort((a, b) => Number(b.favorite) - Number(a.favorite) || b.score - a.score),
    [cards.data],
  );
  const [picked, setPicked] = useState<number[] | null>(null);
  const [count, setCount] = useState(5);
  const [title, setTitle] = useState("");
  const [countdown, setCountdown] = useState(true);
  const shown = picked ?? clips.slice(0, count).map((c) => c.id);
  const toggle = (id: number) => setPicked(shown.includes(id) ? shown.filter((x) => x !== id) : [...shown, id]);
  const build = useMutation({
    mutationFn: () => releasesApi.top(source as number, { plan_ids: shown, title: title.trim(), countdown }),
    onSuccess: (r) => {
      toast.success(`ТОП из ${r.clips} клипов, ${Math.round(r.duration)} с` + (r.skipped.length ? `, не влезло ${r.skipped.length} (предел 40 кусков)` : ""));
      qc.invalidateQueries({ queryKey: navKeys.all });
      navigate(clipPath(r.clip_plan_id));
    },
    onError: (e) => toast.error(errText(e, "Компиляция не собралась")),
  });
  if (videos.isLoading) return <Loading />;
  if (!list.length) {
    return <EmptyState icon="🏆" title="Пока не из чего" hint="ТОП-N собирается из клипов одного видео: нужно видео хотя бы с двумя клипами." />;
  }
  return (
    <div className="series-col">
      <div className="series-row">
        <label className="field">
          <span>Из какого видео</span>
          <select
            className="input"
            value={source ?? ""}
            onChange={(e) => {
              setSourceId(Number(e.target.value));
              setPicked(null);
            }}
          >
            {list.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}, клипов {v.clips}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Сколько клипов</span>
          <select
            className="input"
            value={count}
            onChange={(e) => {
              setCount(Number(e.target.value));
              setPicked(null);
            }}
          >
            {[3, 5, 7, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Название</span>
          <input className="input" value={title} maxLength={100} placeholder={`ТОП-${shown.length} моментов стрима`} onChange={(e) => setTitle(e.target.value)} />
        </label>
      </div>
      <ul className="nv-top">
        {clips.map((c) => (
          <li key={c.id}>
            <label className="aiv-check">
              <input type="checkbox" checked={shown.includes(c.id)} onChange={() => toggle(c.id)} />
              <span>
                {c.favorite ? "★ " : ""}
                {c.name}
              </span>
              <small className="muted">{formatDuration(c.duration_sec)}</small>
            </label>
          </li>
        ))}
      </ul>
      <div className="series-row">
        <label className="aiv-check" title="#N … #1: лучший последним">
          <input type="checkbox" checked={countdown} onChange={(e) => setCountdown(e.target.checked)} /> обратный отсчёт
        </label>
        <button className="btn primary sm" disabled={shown.length < 2 || build.isPending} onClick={() => build.mutate()}>
          {build.isPending ? "Собираю…" : `Собрать ТОП-${shown.length}`}
        </button>
      </div>
      <span className="muted">Клип ляжет в это видео и в список выпусков проекта. Сборка берёт куски из одной записи: ТОП из разных видео пока не умеет.</span>
    </div>
  );
}

/* ---------- Промо приложения ---------- */

type PromoScene = { asset_id: string; from: string; to: string; title: string };

function PromoForm({ projectId }: { projectId: number }) {
  const toast = useToast();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const assets = useQuery({ queryKey: ["promo-assets", projectId], queryFn: () => montageAssetsApi.listScoped({ projectId }) });
  const tracks = useQuery({ queryKey: ["audio-tracks"], queryFn: audioTracksApi.list, staleTime: 60_000 });
  const videos = useMemo(() => (assets.data ?? []).filter((a) => a.kind === "video"), [assets.data]);
  const [title, setTitle] = useState("");
  const [accent, setAccent] = useState("#ff6a1f");
  const [hook, setHook] = useState("");
  const [scenes, setScenes] = useState<PromoScene[]>([{ asset_id: "", from: "0", to: "6", title: "" }]);
  const [cta, setCta] = useState("");
  const [lines, setLines] = useState("");
  const [track, setTrack] = useState(0);
  const setScene = (index: number, patch: Partial<PromoScene>) => setScenes(scenes.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  const ready = title.trim() && hook.trim() && cta.trim() && scenes.every((s) => s.asset_id && s.title.trim() && Number(s.to) > Number(s.from));
  const build = useMutation({
    mutationFn: () =>
      releasesApi.promo(projectId, {
        title: title.trim(),
        spec: {
          brand: { accent },
          hook: hook.split("\n").map((l) => l.trim()).filter(Boolean),
          scenes: scenes.map((s) => ({ asset_id: Number(s.asset_id), from: Number(s.from), to: Number(s.to), title: s.title.trim() })),
          end: { cta: cta.trim(), lines: lines.split("\n").map((l) => l.trim()).filter(Boolean) },
          music: track ? { track_id: track } : null,
        },
      }),
    onSuccess: (r) => {
      toast.success("Промо собрано: дальше правки и рендер на экране клипа");
      qc.invalidateQueries({ queryKey: navKeys.all });
      qc.invalidateQueries({ queryKey: releaseKeys.all });
      navigate(r.to);
    },
    onError: (e) => toast.error(errText(e, "Промо не собралось")),
  });
  if (assets.isLoading) return <Loading />;
  return (
    <div className="series-col">
      {!videos.length ? (
        <p className="aiv-warn">
          Нужны записи экрана: загрузите их в <Link to={projectPath(projectId, "files")}>файлы проекта</Link>, они появятся здесь.
        </p>
      ) : null}
      <div className="series-row">
        <label className="field">
          <span>Название</span>
          <input className="input" value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} placeholder="Приложение, промо" />
        </label>
        <label className="expl-color">
          <input type="color" value={accent} onChange={(e) => setAccent(e.target.value)} />
          <span className="muted">акцент</span>
        </label>
        <label className="field">
          <span>Музыка</span>
          <select className="input" value={track} onChange={(e) => setTrack(Number(e.target.value))}>
            <option value={0}>без музыки</option>
            {(tracks.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>Первые слова, 1–3 строки по 1–3 слова</span>
        <textarea className="input" rows={3} value={hook} onChange={(e) => setHook(e.target.value)} placeholder={"Ищешь коллаб?\nХватит ждать.\nСвайпай."} />
      </label>
      <div className="li-cap">Сцены: запись экрана в телефоне и подпись над ним</div>
      {scenes.map((s, i) => (
        <div key={i} className="series-row nv-scene">
          <select className="input" value={s.asset_id} aria-label={`Запись сцены ${i + 1}`} onChange={(e) => setScene(i, { asset_id: e.target.value })}>
            <option value="">Запись…</option>
            {videos.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label || a.original_filename || `Файл ${a.id}`}
              </option>
            ))}
          </select>
          <input className="input series-num" title="С какой секунды записи" aria-label="От, с" value={s.from} onChange={(e) => setScene(i, { from: e.target.value })} />
          <input className="input series-num" title="До какой секунды записи" aria-label="До, с" value={s.to} onChange={(e) => setScene(i, { to: e.target.value })} />
          <input className="input wide" placeholder="Подпись, 2–6 слов" value={s.title} onChange={(e) => setScene(i, { title: e.target.value })} />
          {scenes.length > 1 ? (
            <button className="btn ghost sm" title="Убрать сцену" onClick={() => setScenes(scenes.filter((_, n) => n !== i))}>
              ✕
            </button>
          ) : null}
        </div>
      ))}
      {scenes.length < 6 ? (
        <div>
          <button className="btn ghost sm" onClick={() => setScenes([...scenes, { asset_id: "", from: "0", to: "6", title: "" }])}>
            ＋ Сцена
          </button>
        </div>
      ) : null}
      <div className="series-row">
        <label className="field">
          <span>Финал: ссылка или призыв</span>
          <input className="input" value={cta} onChange={(e) => setCta(e.target.value)} placeholder="example.com" />
        </label>
        <label className="field">
          <span>Строки над ним, по одной</span>
          <textarea className="input" rows={2} value={lines} onChange={(e) => setLines(e.target.value)} />
        </label>
      </div>
      <div className="series-row">
        <button className="btn primary sm" disabled={!ready || build.isPending} onClick={() => build.mutate()}>
          {build.isPending ? "Собираю…" : "Собрать промо, 0 ₽"}
        </button>
        <span className="muted">Тонкую настройку и новые сборки делает агент в чате клипа: заменяются только слои промо, ваши слои остаются.</span>
      </div>
    </div>
  );
}

