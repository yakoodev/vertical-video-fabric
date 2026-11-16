import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { navKeys } from "@/api/nav";
import { TORRENT_ACTIVE, torrentsApi, type TorrentTask } from "@/api/torrents";
import { useToast } from "@/components/Toast";
import { Loading, formatBytes } from "@/components/ui";
import { videoPath } from "@/lib/paths";

/**
 * «🧲 Торрент»: magnet-ссылка или файл .torrent → список файлов с галочками и размерами → загрузка с прогрессом.
 * Закрыть окно можно в любой момент: задача идёт дальше и видна в «Активности»; каждый видеофайл станет своим видео.
 */

type Mode = "magnet" | "file";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function eta(sec: number | null): string {
  if (sec == null) return "—";
  if (sec >= 3600) return `${Math.floor(sec / 3600)} ч ${Math.floor((sec % 3600) / 60)} мин`;
  if (sec >= 60) return `${Math.floor(sec / 60)} мин`;
  return `${sec} с`;
}

export function TorrentDialog({ projectId, onClose }: { projectId?: number | null; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("magnet");
  const [magnet, setMagnet] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [picked, setPicked] = useState<number[] | null>(null);

  const add = useMutation({
    mutationFn: () => (mode === "file" && file ? torrentsApi.addFile(file, projectId) : torrentsApi.addMagnet(magnet.trim(), projectId)),
    onSuccess: (t) => {
      setTaskId(t.id);
      setPicked(null);
      qc.invalidateQueries({ queryKey: qk.activeTasks });
    },
    onError: (e) => toast.error(errText(e, "Не удалось добавить торрент")),
  });

  const task = useQuery({
    queryKey: ["torrent", taskId],
    queryFn: () => torrentsApi.get(taskId as number),
    enabled: taskId != null,
    refetchInterval: (q) => {
      const t = q.state.data as TorrentTask | undefined;
      if (!t || !TORRENT_ACTIVE.has(t.status)) return false;
      return t.status === "selecting" ? 5000 : 1000;
    },
  });
  const t = task.data;

  // Готово — списки видео перечитать: новые источники появятся без перезагрузки.
  useEffect(() => {
    if (t?.status === "succeeded") {
      qc.invalidateQueries({ queryKey: qk.sources });
      qc.invalidateQueries({ queryKey: navKeys.all });
    }
  }, [t?.status, qc]);

  const start = useMutation({
    mutationFn: (files: number[]) => torrentsApi.start(taskId as number, files),
    onSuccess: (next) => {
      qc.setQueryData(["torrent", taskId], next);
      qc.invalidateQueries({ queryKey: qk.activeTasks });
    },
    onError: (e) => toast.error(errText(e, "Загрузка не началась")),
  });
  const cancel = useMutation({
    mutationFn: () => torrentsApi.cancel(taskId as number),
    onSuccess: (next) => {
      qc.setQueryData(["torrent", taskId], next);
      qc.invalidateQueries({ queryKey: qk.activeTasks });
    },
    onError: (e) => toast.error(errText(e, "Не удалось отменить")),
  });

  const restart = () => {
    setTaskId(null);
    setPicked(null);
  };
  const canAdd = mode === "magnet" ? magnet.trim().startsWith("magnet:") : Boolean(file);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal torrent-dlg" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Видео из торрента">
        <h3 style={{ margin: "0 0 4px" }}>🧲 Видео из торрента</h3>
        <div className="muted torrent-sub">
          Сначала придёт список файлов, потом отметьте нужные. Каждый видеофайл станет отдельным видео. Раздача после
          загрузки выключена.
        </div>

        {taskId == null ? (
          <div className="torrent-col">
            <div className="seg" role="radiogroup" aria-label="Откуда торрент">
              {(
                [
                  ["magnet", "Magnet-ссылка"],
                  ["file", "Файл .torrent"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="radio"
                  aria-checked={mode === key}
                  className={`seg-item${mode === key ? " active" : ""}`}
                  onClick={() => setMode(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            {mode === "magnet" ? (
              <textarea
                className="input mono torrent-magnet"
                rows={3}
                autoFocus
                placeholder="magnet:?xt=urn:btih:…"
                value={magnet}
                disabled={add.isPending}
                onChange={(e) => setMagnet(e.target.value)}
              />
            ) : (
              <label className="btn torrent-file">
                {file ? `📄 ${file.name}` : "Выбрать файл .torrent"}
                <input
                  type="file"
                  accept=".torrent,application/x-bittorrent"
                  hidden
                  disabled={add.isPending}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    e.target.value = "";
                  }}
                />
              </label>
            )}
            <div className="torrent-actions">
              <button className="btn ghost" onClick={onClose}>
                Отмена
              </button>
              <button className="btn primary" disabled={!canAdd || add.isPending} onClick={() => add.mutate()}>
                {add.isPending ? "Добавляю…" : "Получить список файлов"}
              </button>
            </div>
          </div>
        ) : !t ? (
          <Loading />
        ) : t.status === "metadata" ? (
          <div className="torrent-col">
            <Loading label={t.message || "Получаю список файлов…"} />
            <span className="muted torrent-note">По magnet-ссылке список приходит от других участников раздачи: обычно за секунды, у редких раздач дольше.</span>
            <TaskActions onClose={onClose} onCancel={() => cancel.mutate()} cancelling={cancel.isPending} />
          </div>
        ) : t.status === "selecting" ? (
          <FilePicker
            task={t}
            picked={picked ?? t.selected}
            onPick={setPicked}
            busy={start.isPending}
            onStart={(files) => start.mutate(files)}
            onCancel={() => cancel.mutate()}
            onClose={onClose}
          />
        ) : t.status === "downloading" || t.status === "registering" || t.status === "cancelling" ? (
          <ProgressCard task={t} onClose={onClose} onCancel={() => cancel.mutate()} cancelling={cancel.isPending} />
        ) : t.status === "succeeded" ? (
          <div className="torrent-col">
            <div className="torrent-done">
              <b>Готово: {t.source_ids.length === 1 ? "видео добавлено" : `видео добавлено: ${t.source_ids.length}`}</b>
              {t.error ? <span className="torrent-warn">Не всё получилось: {t.error}</span> : null}
              <ul className="torrent-links">
                {t.source_ids.map((id, i) => (
                  <li key={id}>
                    <Link to={videoPath(id)} onClick={onClose}>
                      Открыть видео {t.source_ids.length > 1 ? i + 1 : ""} →
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
            <div className="torrent-actions">
              <button className="btn ghost" onClick={restart}>
                Ещё торрент
              </button>
              <button className="btn primary" onClick={onClose}>
                Закрыть
              </button>
            </div>
          </div>
        ) : (
          <div className="torrent-col">
            <b>{t.status === "cancelled" ? "Отменено, недокачанное удалено" : "Не скачалось"}</b>
            {t.error ? <span className="torrent-warn">{t.error}</span> : null}
            <div className="torrent-actions">
              <button className="btn ghost" onClick={onClose}>
                Закрыть
              </button>
              <button className="btn primary" onClick={restart}>
                Попробовать снова
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TaskActions({ onClose, onCancel, cancelling }: { onClose: () => void; onCancel: () => void; cancelling: boolean }) {
  return (
    <div className="torrent-actions">
      <button className="btn ghost" disabled={cancelling} onClick={onCancel}>
        {cancelling ? "Отменяю…" : "Отменить торрент"}
      </button>
      <button className="btn" onClick={onClose} title="Загрузка продолжится, прогресс в «Активности»">
        Свернуть
      </button>
    </div>
  );
}

function FilePicker({
  task,
  picked,
  onPick,
  busy,
  onStart,
  onCancel,
  onClose,
}: {
  task: TorrentTask;
  picked: number[];
  onPick: (files: number[]) => void;
  busy: boolean;
  onStart: (files: number[]) => void;
  onCancel: () => void;
  onClose: () => void;
}) {
  const videos = useMemo(() => task.files.filter((f) => f.is_video), [task.files]);
  const total = task.files.filter((f) => picked.includes(f.index)).reduce((sum, f) => sum + f.size, 0);
  const over = task.max_bytes > 0 && total > task.max_bytes;
  const toggle = (index: number) => onPick(picked.includes(index) ? picked.filter((i) => i !== index) : [...picked, index].sort((a, b) => a - b));
  const allVideos = videos.length > 1 && videos.every((f) => picked.includes(f.index));
  return (
    <div className="torrent-col">
      <div className="torrent-name">
        <b>{task.name || task.label}</b>
        <span className="muted mono">{task.files.length} файл., видео {videos.length}</span>
      </div>
      {videos.length > 1 ? (
        <label className="aiv-check">
          <input type="checkbox" checked={allVideos} onChange={() => onPick(allVideos ? [] : videos.map((f) => f.index))} /> все видео
        </label>
      ) : null}
      <ul className="torrent-files" aria-label="Файлы торрента">
        {task.files.map((f) => (
          <li key={f.index} className={f.is_video ? "" : "off"}>
            <label className="aiv-check" title={f.is_video ? f.path : "Не видео: источником не станет"}>
              <input type="checkbox" checked={picked.includes(f.index)} disabled={!f.is_video || busy} onChange={() => toggle(f.index)} />
              <span className="torrent-path">{f.path}</span>
            </label>
            <span className="muted mono">{formatBytes(f.size)}</span>
          </li>
        ))}
      </ul>
      <div className={`torrent-total${over ? " over" : ""}`}>
        Выбрано: {picked.length} · {formatBytes(total)}
        {task.max_bytes ? <span className="muted"> из {formatBytes(task.max_bytes)} за раз</span> : null}
        {over ? <span> — больше предела, отметьте меньше</span> : null}
      </div>
      <div className="torrent-actions">
        <button className="btn ghost" disabled={busy} onClick={onCancel}>
          Отменить торрент
        </button>
        <button className="btn" onClick={onClose} title="Выбор подождёт, задача — в «Активности»">
          Свернуть
        </button>
        <button className="btn primary" disabled={!picked.length || over || busy} onClick={() => onStart(picked)}>
          {busy ? "Начинаю…" : `Скачать ${formatBytes(total)}`}
        </button>
      </div>
    </div>
  );
}

function ProgressCard({
  task,
  onClose,
  onCancel,
  cancelling,
}: {
  task: TorrentTask;
  onClose: () => void;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const percent = Math.round(task.progress ?? 0);
  const registering = task.status === "registering";
  return (
    <div className="torrent-col">
      <div className="torrent-name">
        <b>{task.name || task.label}</b>
        <span className="muted mono">
          {formatBytes(task.done_bytes)} из {formatBytes(task.total_bytes)}
        </span>
      </div>
      <div className="dl-progress torrent-progress" title={task.message}>
        <div className="dl-bar">
          <span style={{ width: `${percent}%` }} />
        </div>
        <span className="mono">{percent}%</span>
      </div>
      {registering || task.status === "cancelling" ? (
        <span className="muted">{task.message}</span>
      ) : (
        <dl className="torrent-stats">
          <div>
            <dt>Скорость</dt>
            <dd className="mono">{task.speed ? `${formatBytes(task.speed)}/с` : "—"}</dd>
          </div>
          <div>
            <dt>Осталось</dt>
            <dd className="mono">{eta(task.eta_sec)}</dd>
          </div>
          <div>
            <dt>Пиры</dt>
            <dd className="mono">
              {task.peers}
              {task.seeders ? ` (раздают ${task.seeders})` : ""}
            </dd>
          </div>
        </dl>
      )}
      {registering ? null : (
        <TaskActions onClose={onClose} onCancel={onCancel} cancelling={cancelling || task.status === "cancelling"} />
      )}
    </div>
  );
}
