import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { VOD_STATUS_LABEL, vodWatchApi, vodWatchKey, type VodWatchConfig } from "@/api/vodWatch";
import { projectsApi } from "@/api/projects";
import { subtitleProfilesApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";

/**
 * «Автозапуск на новый VOD» во вкладке «Автопилот» серии-стрима (app/vod_watch.py).
 * Только завершённые эфиры; клипы — карточками в «Одобрение», публикует только одобрение.
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const LAYOUTS: [string, string][] = [["", "как в клипе"], ["split", "вебка + игра"], ["pip_cam", "вебка в углу"], ["single", "один кадр"], ["meme", "мем"]];
const fmtLen = (sec: number) => (sec ? `${Math.floor(sec / 3600)}:${String(Math.floor((sec % 3600) / 60)).padStart(2, "0")}` : "");

export function VodWatchPanel({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const project = useQuery({ queryKey: qk.channelProject(projectId), queryFn: () => projectsApi.get(projectId) });
  const isStream = project.data?.kind === "stream_channel";
  const watch = useQuery({
    queryKey: vodWatchKey(projectId),
    queryFn: () => vodWatchApi.get(projectId),
    enabled: isStream,
    refetchInterval: 30000,
  });
  const profiles = useQuery({ queryKey: qk.subtitleProfiles, queryFn: subtitleProfilesApi.list, enabled: isStream });
  const [draft, setDraft] = useState<VodWatchConfig | null>(null);
  useEffect(() => {
    if (watch.data) setDraft(watch.data.config);
  }, [watch.data]);
  const onData = (data: unknown) => {
    qc.setQueryData(vodWatchKey(projectId), data);
    qc.invalidateQueries({ queryKey: ["approvals"] });
  };
  const save = useMutation({
    mutationFn: () => vodWatchApi.save(projectId, draft!),
    onSuccess: (d) => {
      onData(d);
      toast.success(d.config.enabled ? "Автозапуск включён" : "Сохранено");
    },
    onError: (e) => toast.error(errText(e, "Не сохранилось")),
  });
  const check = useMutation({
    mutationFn: () => vodWatchApi.check(projectId),
    onSuccess: (d) => {
      onData(d);
      if (d.error) toast.error(d.error);
      else toast.success(d.found?.length ? `Новых VOD: ${d.found.length}` : "Новых VOD нет");
    },
    onError: (e) => toast.error(errText(e, "Не проверилось")),
  });
  const queue = useMutation({
    mutationFn: ({ id, cutter }: { id: number; cutter: "pipeline" | "agent" }) => vodWatchApi.queue(projectId, id, cutter),
    onSuccess: onData,
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });

  if (!isStream) return null;
  if (!draft || !watch.data) return <Loading />;
  const patch = (p: Partial<VodWatchConfig>) => setDraft({ ...draft, ...p });
  const look = (p: Partial<VodWatchConfig["look"]>) => patch({ look: { ...draft.look, ...p } });
  const w = watch.data;

  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Автозапуск на новый VOD</h3>
        <button className="btn sm" disabled={!draft.channel || check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? "Проверяю…" : "Проверить сейчас"}
        </button>
      </div>
      <p className="muted series-hint">
        Новый завершённый эфир на Twitch → фоновая загрузка (~{draft.vod_size_gb} ГБ, по одной) → нарезка → карточки в
        «Одобрение». Эфир в прямом времени не пишется. Что уже было на канале при включении — не качается.
      </p>
      <label className="check">
        <input type="checkbox" checked={draft.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
        Следить за каналом
      </label>
      <label className="field">
        <span>Канал Twitch</span>
        <input className="input" placeholder="sareamura или twitch.tv/sareamura" value={draft.channel} onChange={(e) => patch({ channel: e.target.value })} />
      </label>
      <div className="series-row">
        <label className="field" style={{ margin: 0 }}>
          <span>Проверять раз в, ч</span>
          <input className="input series-num" type="number" min={0.5} step={0.5} value={draft.interval_hours} onChange={(e) => patch({ interval_hours: Number(e.target.value) })} />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Клипов с VOD</span>
          <input className="input series-num" type="number" min={1} max={30} value={draft.max_clips} onChange={(e) => patch({ max_clips: Number(e.target.value) })} />
        </label>
      </div>
      <label className="field">
        <span>Кто режет</span>
        <select className="input" value={draft.mode} onChange={(e) => patch({ mode: e.target.value as VodWatchConfig["mode"] })}>
          <option value="pipeline">сразу встроенный конвейер (анализ → рендер → на одобрение)</option>
          <option value="ask">сначала спросить: нарезать автоматом или отдать агенту (Claude/Gemini)</option>
        </select>
      </label>
      <div className="series-section">
        <strong>Оформление клипов</strong>
        <div className="series-row">
          <label className="check">
            <input type="checkbox" checked={draft.look.subtitles} onChange={(e) => look({ subtitles: e.target.checked })} />
            Субтитры
          </label>
          {draft.look.subtitles ? (
            <select className="input" style={{ width: "auto" }} value={draft.look.subtitle_profile_id} onChange={(e) => look({ subtitle_profile_id: Number(e.target.value) })}>
              <option value={0}>стиль по умолчанию</option>
              {profiles.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          ) : null}
          <select className="input" style={{ width: "auto" }} value={draft.look.layout} onChange={(e) => look({ layout: e.target.value })} title="Макет кадра">
            {LAYOUTS.map(([v, label]) => (
              <option key={v} value={v}>
                макет: {label}
              </option>
            ))}
          </select>
        </div>
        <div className="series-row">
          <label className="check">
            <input type="checkbox" checked={draft.look.censor_profanity} onChange={(e) => look({ censor_profanity: e.target.checked })} />
            Глушить мат
          </label>
          <label className="check" title="Плашка канала и концовка — из бренда проекта">
            <input type="checkbox" checked={draft.look.nameplate} onChange={(e) => look({ nameplate: e.target.checked })} />
            Плашка канала
          </label>
          <label className="check">
            <input type="checkbox" checked={draft.look.end_screen} onChange={(e) => look({ end_screen: e.target.checked })} />
            Концовка
          </label>
        </div>
        {!project.data?.brand && (draft.look.nameplate || draft.look.end_screen) ? (
          <p className="muted series-hint">У проекта нет бренда — плашка и концовка не поставятся (бренд — в «Обзоре» серии).</p>
        ) : null}
      </div>
      <button className="btn primary" style={{ justifySelf: "start" }} disabled={save.isPending} onClick={() => save.mutate()}>
        {save.isPending ? "Сохраняю…" : "Сохранить"}
      </button>
      {w.last_error ? <div style={{ color: "var(--danger)", fontSize: 12.5 }}>Проверка: {w.last_error}</div> : null}
      {w.last_check_at ? (
        <p className="muted series-hint">
          Проверено {w.last_check_at} UTC{w.config.enabled && w.next_check_at ? ` · следующая ${w.next_check_at} UTC` : ""}
        </p>
      ) : null}
      {w.items.length ? (
        <ul className="series-list">
          {w.items.slice(0, 10).map((it) => (
            <li key={it.id}>
              <div className="series-li-main">
                <a href={it.url} target="_blank" rel="noreferrer">
                  {it.title || it.vod_id}
                </a>
                <span className="muted">{fmtLen(it.duration_sec)}</span>
                <span className="chip">{VOD_STATUS_LABEL[it.status] ?? it.status}</span>
                {it.source_id ? <Link to={`/projects/${it.source_id}`}>источник #{it.source_id}</Link> : null}
              </div>
              {it.detail ? <div className="muted" style={{ fontSize: 12.5 }}>{it.detail}</div> : null}
              {["baseline", "skipped", "failed", "done", "for_agent"].includes(it.status) ? (
                <div className="series-row" style={{ marginTop: 4 }}>
                  <button className="btn sm" disabled={queue.isPending} onClick={() => queue.mutate({ id: it.id, cutter: "pipeline" })}>
                    Нарезать
                  </button>
                  <button className="btn ghost sm" disabled={queue.isPending} onClick={() => queue.mutate({ id: it.id, cutter: "agent" })}>
                    Скачать для агента
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
