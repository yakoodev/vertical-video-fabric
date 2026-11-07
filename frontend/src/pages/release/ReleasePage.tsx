import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { aivApi } from "@/api/aivideo";
import { ApiError } from "@/api/client";
import { navKeys } from "@/api/nav";
import { releaseKeys, releasesApi, type ReleaseStep, type ReleaseView } from "@/api/releases";
import { Icon } from "@/components/Icon";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading, formatDuration, plural } from "@/components/ui";
import { clipEditorPath, clipPath, projectPath } from "@/lib/paths";
import { AiVideoTab } from "@/pages/AiVideoTab";
import { ExplainerVideoPanel } from "@/pages/ExplainerTab";
import { StoryDraftPanel } from "@/pages/release/StoryDraftPanel";
import { StoryEpisodePanel } from "@/pages/StoriesTab";
import "@/styles/aivideo.css";
import "@/styles/explainer.css";
import "@/styles/stories.css";
import "@/styles/releases.css";

/**
 * Страница выпуска /r/:key (решения 12 и 16): шаги типа, цена и карточка на месте, дальше клипы с обычным экраном.
 * Один клип и всё готово — адрес сразу ведёт в клип; ?steps=1 показывает шаги и тогда (ссылка с лестницы и экрана клипа).
 * Сам конвейер у каждого типа свой: страница только показывает его и зовёт его же ручки.
 */

const ACTIVE = ["working", "approval"];
const rub = (v: number) => `${v.toFixed(v > 0 && v < 10 ? 2 : 0).replace(".", ",")} ₽`;
/** Потраченные деньги: с копейками, если они есть, без округления до рубля. */
const rubExact = (v: number) => `${(Math.round(v * 100) % 100 === 0 ? v.toFixed(0) : v.toFixed(2)).replace(".", ",")} ₽`;

function dateRu(value: string): string {
  const d = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

const STEP_ICON: Record<string, string> = { done: "✓", now: "…", wait: "₽", failed: "!", todo: "" };
const STEP_WORD: Record<string, string> = { done: "сделано", now: "идёт", wait: "ждёт разрешения", failed: "ошибка", todo: "дальше" };

function StepRail({ steps, active, onPick }: { steps: ReleaseStep[]; active?: string; onPick?: (step: ReleaseStep) => void }) {
  const ref = useRef<HTMLOListElement>(null);
  // На телефоне шаги — лента: текущий шаг должен быть виден без прокрутки.
  const currentKey = steps.find((s) => s.sub === active || ["wait", "failed", "now"].includes(s.state))?.key;
  useEffect(() => {
    const list = ref.current;
    const item = list?.querySelector<HTMLElement>(`[data-step="${currentKey}"]`);
    if (!list || !item || list.scrollWidth <= list.clientWidth) return;
    list.scrollLeft = item.offsetLeft - list.offsetLeft - 8;
  }, [currentKey]);
  return (
    <ol className="rel-steps" aria-label="Шаги выпуска" ref={ref}>
      {steps.map((step) => {
        const body = (
          <>
            <span className={`rel-dot ${step.state}`} aria-hidden>
              {STEP_ICON[step.state]}
            </span>
            <span className="rel-step-t">
              <b>{step.label}</b>
              {step.note ? <small title={step.note}>{step.note}</small> : null}
              <span className="sr-only">{STEP_WORD[step.state]}</span>
            </span>
          </>
        );
        return (
          <li key={step.key} data-step={step.key} className={`rel-step ${step.state}${active && step.sub === active ? " on" : ""}`}>
            {onPick && step.sub ? (
              <button type="button" onClick={() => onPick(step)} aria-current={active === step.sub ? "step" : undefined}>
                {body}
              </button>
            ) : (
              <div>{body}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Та же карточка, что во «Входящих» и в Telegram: сумма, что покупаем, «Разрешить» и «Отказать». */
function CostCard({ release }: { release: ReleaseView }) {
  const qc = useQueryClient();
  const toast = useToast();
  const card = release.approval;
  const decide = useMutation({
    mutationFn: (action: "approve" | "reject") => aivApi.decide(card!.id, action),
    onSuccess: (_d, action) => {
      toast.success(action === "approve" ? "Разрешено: шаг пошёл, выпуск соберётся сам" : "Отказано");
      qc.invalidateQueries({ queryKey: releaseKeys.all });
      qc.invalidateQueries({ queryKey: navKeys.all });
      qc.invalidateQueries({ queryKey: ["explainer"] });
      qc.invalidateQueries({ queryKey: ["stories"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось"),
  });
  if (!card) return null;
  return (
    <section className="rel-card" aria-label="Платный шаг">
      <div className="rel-card-h">
        <Icon name="check" size={18} />
        <h3>Платный шаг: нужно подтверждение</h3>
        <b className="rel-card-sum">≈ {rub(card.amount_rub)}</b>
      </div>
      <pre className="rel-card-q">{card.question}</pre>
      <div className="series-row">
        <button className="btn primary" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
          Разрешить · {rub(card.amount_rub)}
        </button>
        <button className="btn" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
          Отказать
        </button>
      </div>
      <small className="muted">Та же карточка приходит во «Входящие» и в Telegram.</small>
    </section>
  );
}

function ClipsBlock({ release }: { release: ReleaseView }) {
  if (!release.clips.length && !release.older.length) return null;
  return (
    <section className="panel series-panel rel-clips">
      <h3>{release.clips.length === 1 ? "Клип" : `Клипы · ${release.clips.length}`}</h3>
      <div className="rel-clip-list">
        {release.clips.map((c) => (
          <div key={c.id} className="rel-clip">
            <Link to={clipPath(c.id)} className="rel-clip-th" aria-label={c.name}>
              {c.latest ? <img src={`/media/clips/${c.latest.id}/thumb`} alt="" loading="lazy" /> : <span>нет файла</span>}
            </Link>
            <div className="rel-clip-m">
              <Link to={clipPath(c.id)}>{c.name}</Link>
              <small className="muted">
                {c.format}
                {c.duration_sec ? `, ${formatDuration(c.duration_sec)}` : ""}
                {c.versions ? `, ${plural(c.versions, "версия", "версии", "версий")}` : ", ещё не рендерили"}
              </small>
            </div>
            <div className="rel-clip-a">
              <Link className="btn primary sm" to={clipPath(c.id)}>
                Открыть клип
              </Link>
              {c.uploaded ? null : (
                <Link className="btn ghost sm" to={clipEditorPath(c.source_id, c.id)}>
                  Монтаж
                </Link>
              )}
            </div>
          </div>
        ))}
      </div>
      {release.older.length ? (
        <details className="rel-older">
          <summary>Старые сборки отдельными клипами · {release.older.length}</summary>
          <p className="muted">Собраны до того, как пересборка стала версией клипа. Открываются как есть; объединить их в один клип можно на экране клипа.</p>
          <ul>
            {release.older.map((o) => (
              <li key={o.id}>
                <Link to={clipPath(o.id)}>{o.title}</Link> <span className="muted">{dateRu(o.date)}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

export function ReleasePage() {
  const { releaseKey = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = useQuery({
    queryKey: releaseKeys.one(releaseKey),
    queryFn: () => releasesApi.get(releaseKey),
    refetchInterval: (q) => (ACTIVE.includes((q.state.data as ReleaseView | undefined)?.status ?? "") ? 4000 : false),
  });
  if (query.isLoading) return <Loading />;
  if (query.isError || !query.data) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const r = query.data;
  // Один клип и всё готово: выпуск открывается сразу клипом (решение 12). Адрес шагов — ?steps=1.
  if (!params.has("steps") && r.to.startsWith("/clips/")) return <Navigate to={r.to} replace />;
  const projectId = r.project?.id ?? 0;
  const gone = () => navigate(projectId ? projectPath(projectId) : "/projects");
  const aivSub = params.get("sub") ?? "script";
  const pickStep = (step: ReleaseStep) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (step.sub) next.set("sub", step.sub);
        return next;
      },
      { replace: true },
    );
  const meta = [
    `${r.kind_label}, ${dateRu(r.date)}`,
    r.spent_rub ? `потрачено ${rubExact(r.spent_rub)}` : "",
    r.spent_estimated_rub ? `ещё ≈ ${rubExact(r.spent_estimated_rub)} по оценке` : "",
  ].filter(Boolean);
  return (
    <div className="rel">
      <div className="rel-head">
        <h1 title={r.topic || r.name}>{r.name}</h1>
        <div className="rel-meta">
          {meta.map((m) => (
            <span key={m} className="chip">
              {m}
            </span>
          ))}
          <span className={`chip rel-status ${r.status}`}>{r.status_label}</span>
          {r.voice && !r.script_draft ? <span className="muted">{r.voice}</span> : null}
        </div>
        {/* Биты или сценарий уже правят: что будет с правками субтитров, видно до новой сборки. */}
        {r.status !== "ready" && r.reassemble.subtitle_fixes.note ? <p className="muted rel-summary">{r.reassemble.subtitle_fixes.note}</p> : null}
      </div>
      <div className="rel-grid">
        <div className="rel-rail">
          <StepRail steps={r.steps} active={r.kind === "aiv" ? aivSub : undefined} onPick={r.kind === "aiv" ? pickStep : undefined} />
        </div>
        <div className="rel-body">
          {r.error && r.status !== "failed" ? <p className="muted">{r.error}</p> : null}
          <CostCard release={r} />
          <ClipsBlock release={r} />
          {r.kind === "expl" && projectId ? (
            <ExplainerVideoPanel projectId={projectId} videoId={r.table_id} reassembleNote={r.reassemble.note} onDeleted={gone} />
          ) : null}
          {r.script_draft ? <StoryDraftPanel draft={r.script_draft} projectId={projectId} onDeleted={gone} /> : null}
          {(r.kind === "story" || r.kind === "3d") && projectId && !r.script_draft ? (
            <StoryEpisodePanel projectId={projectId} episodeId={r.table_id} reassembleNote={r.reassemble.note} onDeleted={gone} />
          ) : null}
          {r.kind === "aiv" && projectId ? <AiVideoTab projectId={projectId} scriptId={r.table_id} /> : null}
          {!r.has_steps ? <p className="muted">У этого типа нет шагов: всё делается на экране клипа.</p> : null}
        </div>
      </div>
    </div>
  );
}
