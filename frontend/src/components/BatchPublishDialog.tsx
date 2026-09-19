import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { accountsApi } from "@/api/accounts";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Loading, formatDuration } from "@/components/ui";

/**
 * Publish several rendered clips at once — the last step of
 * «анализ → избранное → настройка → рендер → публикация».
 *
 * Each clip goes out under its OWN title and description (rename a clip on its
 * card), to the same accounts. An interval spreads the posts out: ten uploads in
 * one minute are what platforms throttle and followers scroll past.
 * Clips with quality warnings are flagged here — the last cheap moment to catch
 * a silent intro or a clipping peak before it goes public.
 */
export function BatchPublishDialog({ clips, onClose }: { clips: Clip[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list });
  const [targets, setTargets] = useState<number[]>([]);
  const [privacy, setPrivacy] = useState<"public" | "unlisted" | "private">("public");
  const [startAt, setStartAt] = useState("");
  const [intervalMin, setIntervalMin] = useState(0);
  const [aiMeta, setAiMeta] = useState(true);

  const publish = useMutation({
    mutationFn: () =>
      clipsApi.publishBatch({
        clip_ids: clips.map((c) => c.id),
        targets,
        privacy,
        start_at: startAt ? startAt.replace("T", " ") : "",
        interval_minutes: intervalMin,
        ai_metadata: aiMeta,
      }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: qk.jobs });
      qc.invalidateQueries({ queryKey: qk.activeTasks });
      const metaFailed = Object.keys(res.metadata_errors ?? {}).length;
      if (metaFailed) toast.error(`ИИ-заголовки не вышли для ${metaFailed} клип(ов) — ушли со старыми`);
      if (res.skipped.length) {
        toast.error(`В очередь: ${res.jobs.length}. Пропущено ${res.skipped.length}: ${res.skipped[0].reason}`);
      } else {
        toast.success(`В очереди публикации: ${res.jobs.length}`);
      }
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось поставить в очередь"),
  });

  const toggle = (id: number) =>
    setTargets((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  const warned = clips.filter((c) => c.qc && !c.qc.ok);

  return (
    <div className="modal-backdrop" onClick={publish.isPending ? undefined : onClose}>
      <div className="modal pub-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="pub-head">
          <h3>Опубликовать выбранные · {clips.length}</h3>
          <button className="pub-x" title="Закрыть" onClick={onClose} disabled={publish.isPending}>
            ×
          </button>
        </div>

        <div className="bpub-list">
          {clips.map((c) => (
            <div key={c.id} className="bpub-row">
              {c.cover_url ? <img src={c.cover_url} alt="" /> : <span className="bpub-noimg" />}
              <span className="bpub-title" title={c.title}>
                {c.title || `Клип #${c.id}`}
              </span>
              <span className="muted mono">{formatDuration(c.duration_sec)}</span>
              {c.qc && !c.qc.ok ? (
                <span className="bpub-warn" title={c.qc.issues.map((i) => i.text).join("\n")}>
                  ⚠ {c.qc.issues.filter((i) => i.level === "warn").length}
                </span>
              ) : c.qc ? (
                <span className="bpub-ok">✓</span>
              ) : null}
            </div>
          ))}
        </div>
        {warned.length ? (
          <p className="bpub-note">
            У {warned.length} клип(ов) есть замечания по качеству — наведи на ⚠, чтобы посмотреть. Опубликовать всё
            равно можно.
          </p>
        ) : null}

        <div className="field">
          <span>Аккаунты</span>
          {accounts.isLoading ? (
            <Loading />
          ) : !accounts.data?.length ? (
            <span className="muted">Нет аккаунтов — добавьте на странице «Аккаунты»</span>
          ) : (
            <div style={{ display: "grid", gap: 6 }}>
              {accounts.data.map((a) => (
                <label key={a.id} className="check">
                  <input type="checkbox" checked={targets.includes(a.id)} onChange={() => toggle(a.id)} />
                  <span>
                    {a.platform} · {a.label}
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="field">
          <span>Приватность</span>
          <div className="seg" role="radiogroup" aria-label="Приватность">
            {(["public", "unlisted", "private"] as const).map((p) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={privacy === p}
                className={`seg-item${privacy === p ? " active" : ""}`}
                onClick={() => setPrivacy(p)}
              >
                {p}
              </button>
            ))}
          </div>
        </div>

        <label className="switch" style={{ margin: "4px 0 10px" }} title="Перед постановкой в очередь ИИ пишет каждому клипу заголовок, описание и хэштеги по его речи (≈5 сек на клип, параллельно)">
          <input type="checkbox" checked={aiMeta} onChange={(e) => setAiMeta(e.target.checked)} />
          <span className="switch-track" />
          <span>✨ ИИ-заголовки, описания и хэштеги для каждого клипа</span>
        </label>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: 1, minWidth: 200 }}>
            <span>Первый выходит</span>
            <input className="input" type="datetime-local" value={startAt} onChange={(e) => setStartAt(e.target.value)} />
            <span className="muted" style={{ fontSize: 12 }}>
              Пусто — сразу
            </span>
          </label>
          <label className="field" style={{ flex: 1, minWidth: 200 }}>
            <span>Интервал между клипами</span>
            <select className="input" value={intervalMin} onChange={(e) => setIntervalMin(Number(e.target.value))}>
              <option value={0}>все сразу</option>
              <option value={30}>30 минут</option>
              <option value={60}>1 час</option>
              <option value={120}>2 часа</option>
              <option value={240}>4 часа</option>
              <option value={480}>8 часов</option>
              <option value={1440}>раз в сутки</option>
            </select>
          </label>
        </div>

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 18 }}>
          <button className="btn ghost" onClick={onClose} disabled={publish.isPending}>
            Отмена
          </button>
          <button
            className="btn primary"
            disabled={publish.isPending || !targets.length}
            onClick={() => publish.mutate()}
          >
            {publish.isPending ? (aiMeta ? "Пишу заголовки…" : "…") : `В очередь · ${clips.length}`}
          </button>
        </div>
      </div>
    </div>
  );
}
