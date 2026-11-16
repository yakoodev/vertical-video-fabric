import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { audioTracksApi } from "@/api/assets";
import { ApiError } from "@/api/client";
import { musicVideoApi, musicVideoKeys, type MusicVideoJob, type MusicVideoStyle } from "@/api/musicVideo";
import { useToast } from "@/components/Toast";

/**
 * «Клип на песню» (app/music_video.py): своя песня (из музыки, файлом или ссылкой), участок, стиль и потолок денег →
 * задание идёт само: разбор песни, план, одна карточка на картинки, сцены, кадры, клип проекта. Ниже — ход заданий.
 */

const STYLES: { value: MusicVideoStyle; label: string; hint: string }[] = [
  { value: "comic", label: "Комикс", hint: "плоский комикс, буквы с контуром, вспышки и панели" },
  { value: "paper", label: "Рваная бумага", hint: "буквы-вырезки, полоски печатной машинки, рваные листы" },
];

const LANGS: { value: string; label: string }[] = [
  { value: "", label: "язык сам" },
  { value: "ru", label: "русский" },
  { value: "en", label: "английский" },
];

const PRICE_RUB = 3;
const DONE = new Set(["done", "failed", "cancelled"]);

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** «1:05», «65» или «65.5» → секунды; пусто — null. */
export function parseSec(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  const parts = t.split(":").map(Number);
  if (parts.some((n) => Number.isNaN(n) || n < 0)) return NaN;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

export function MusicVideoForm({ projectId }: { projectId: number }) {
  const toast = useToast();
  const qc = useQueryClient();
  const tracks = useQuery({ queryKey: ["audio-tracks"], queryFn: audioTracksApi.list, staleTime: 60_000 });
  const [track, setTrack] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [link, setLink] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [style, setStyle] = useState<MusicVideoStyle>("comic");
  const [budget, setBudget] = useState(60);
  const [images, setImages] = useState(6);
  const [lyrics, setLyrics] = useState("");
  const [lang, setLang] = useState("");
  const [title, setTitle] = useState("");
  const [openJob, setOpenJob] = useState<number | null>(null);
  const startSec = parseSec(start);
  const endSec = parseSec(end);
  const badTime = Number.isNaN(startSec) || Number.isNaN(endSec) || (startSec != null && endSec != null && endSec - startSec < 4);
  const hasSong = Boolean(track || file || link.trim());
  const estimate = images * PRICE_RUB;
  const ready = hasSong && !badTime && budget >= 0;
  const create = useMutation({
    mutationFn: async () => {
      let trackId: number | null = track || null;
      if (!trackId && file) trackId = (await audioTracksApi.upload(file, file.name.replace(/\.[^.]+$/, ""))).id;
      return musicVideoApi.create(projectId, {
        track_id: trackId,
        music_url: trackId ? "" : link.trim(),
        style,
        start: startSec ?? 0,
        end: endSec,
        max_rub: budget,
        images,
        lyrics: lyrics.trim(),
        lang,
        title: title.trim(),
      });
    },
    onSuccess: (job) => {
      toast.success("Клип на песню пошёл: сначала разбор песни, потом карточка на картинки");
      qc.invalidateQueries({ queryKey: ["audio-tracks"] });
      qc.invalidateQueries({ queryKey: musicVideoKeys.project(projectId) });
      setOpenJob(job.id);
    },
    onError: (e) => toast.error(errText(e, "Клип на песню не запустился")),
  });
  return (
    <div className="series-col">
      <div className="series-row">
        <label className="field">
          <span>Песня из музыки</span>
          <select className="input" value={track} onChange={(e) => setTrack(Number(e.target.value))}>
            <option value={0}>не из музыки</option>
            {(tracks.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Название клипа</span>
          <input className="input" value={title} maxLength={100} placeholder="по названию песни" onChange={(e) => setTitle(e.target.value)} />
        </label>
      </div>
      {!track ? (
        <div className="series-row">
          <label className="field">
            <span>или файлом</span>
            <input className="input" type="file" accept="audio/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label className="field">
            <span>или ссылкой</span>
            <input className="input" value={link} disabled={Boolean(file)} placeholder="https://…" onChange={(e) => setLink(e.target.value)} />
          </label>
        </div>
      ) : null}
      <div className="series-row">
        <label className="field">
          <span>Начало участка</span>
          <input className="input" value={start} placeholder="0:15" inputMode="decimal" onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          <span>Конец участка (пусто — до конца песни)</span>
          <input className="input" value={end} placeholder="0:45" inputMode="decimal" onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {badTime ? <p className="muted">Участок: время как 0:15 или 15, конец позже начала хотя бы на 4 секунды.</p> : null}
      <div className="series-row">
        <label className="field">
          <span>Стиль</span>
          <select className="input" value={style} onChange={(e) => setStyle(e.target.value as MusicVideoStyle)}>
            {STYLES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}: {s.hint}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Картинок-фонов</span>
          <input className="input" type="number" min={1} max={40} value={images} onChange={(e) => setImages(Math.max(1, Math.min(40, Number(e.target.value) || 1)))} />
        </label>
        <label className="field">
          <span>Потолок денег, ₽</span>
          <input className="input" type="number" min={0} value={budget} onChange={(e) => setBudget(Math.max(0, Number(e.target.value) || 0))} />
        </label>
      </div>
      <label className="field">
        <span>Текст песни, по строке; [Припев] и пустые строки — не строки. Нет — распознаю сам</span>
        <textarea className="input" rows={5} value={lyrics} onChange={(e) => setLyrics(e.target.value)} />
      </label>
      <div className="series-row">
        <label className="field">
          <span>Язык песни</span>
          <select className="input" value={lang} onChange={(e) => setLang(e.target.value)}>
            {LANGS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="series-row">
        <button className="btn primary sm" disabled={!ready || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Запускаю…" : "Сделать клип"}
        </button>
        <span className="muted">
          Картинки ≈ {estimate} ₽ ({images} × {PRICE_RUB} ₽){estimate > budget ? " — больше потолка: карточка не уйдёт, уменьшите число картинок" : ""}. Сумма придёт
          карточкой во «Входящие» до трат; в пределах настройки проекта «Тратить без подтверждения» картинки рисуются сразу.
        </span>
      </div>
      <MusicVideoJobs projectId={projectId} openJob={openJob} onOpen={setOpenJob} />
    </div>
  );
}

function MusicVideoJobs({ projectId, openJob, onOpen }: { projectId: number; openJob: number | null; onOpen: (id: number) => void }) {
  const list = useQuery({ queryKey: musicVideoKeys.project(projectId), queryFn: () => musicVideoApi.list(projectId), refetchInterval: 15_000 });
  const jobs = list.data?.jobs ?? [];
  useEffect(() => {
    if (openJob == null && jobs.length && !DONE.has(jobs[0].status)) onOpen(jobs[0].id);
  }, [jobs, openJob, onOpen]);
  if (!jobs.length) return null;
  return (
    <div className="series-col">
      <div className="li-cap">Клипы на песню в этом проекте</div>
      <ul className="nv-top">
        {jobs.slice(0, 6).map((j) => (
          <li key={j.id}>
            <button type="button" className="btn ghost sm" onClick={() => onOpen(j.id)}>
              «{j.title}» · {j.style_label} · {j.status_label}
            </button>
          </li>
        ))}
      </ul>
      {openJob != null ? <MusicVideoStatus jobId={openJob} /> : null}
    </div>
  );
}

function MusicVideoStatus({ jobId }: { jobId: number }) {
  const toast = useToast();
  const qc = useQueryClient();
  const job = useQuery({
    queryKey: musicVideoKeys.one(jobId),
    queryFn: () => musicVideoApi.get(jobId),
    refetchInterval: (q) => (q.state.data && DONE.has((q.state.data as MusicVideoJob).status) ? false : 4000),
  });
  const refresh = (j: MusicVideoJob) => {
    qc.setQueryData(musicVideoKeys.one(jobId), j);
    qc.invalidateQueries({ queryKey: musicVideoKeys.project(j.project_id) });
  };
  const resume = useMutation({ mutationFn: () => musicVideoApi.resume(jobId), onSuccess: refresh, onError: (e) => toast.error(errText(e, "Не продолжилось")) });
  const cancel = useMutation({ mutationFn: () => musicVideoApi.cancel(jobId), onSuccess: refresh, onError: (e) => toast.error(errText(e, "Не отменилось")) });
  const j = job.data;
  if (!j) return null;
  const pct = Math.round((j.progress || 0) * 100);
  const ready = (j.images ?? []).filter((i) => i.ready).length;
  return (
    <div className="series-col">
      <div className="series-row">
        <strong>
          «{j.title}» — {j.status_label}
        </strong>
        <span className="muted">{pct}%</span>
      </div>
      <progress max={100} value={pct} style={{ width: "100%" }} />
      {j.message ? <p className="muted">{j.message}</p> : null}
      {j.error ? <p className="muted">Ошибка: {j.error}</p> : null}
      {j.images?.length ? (
        <p className="muted">
          Картинок готово {ready} из {j.images.length}
          {j.estimate_rub ? `, сумма ${j.estimate_rub.toFixed(2)} ₽` : ""}
          {j.dry_run ? " (сухой прогон, заглушки)" : ""}
        </p>
      ) : null}
      {j.render?.nf ? (
        <p className="muted">
          Кадры: {j.render.done} из {j.render.nf}
        </p>
      ) : null}
      <div className="series-row">
        {j.status === "awaiting_approval" ? (
          <Link className="btn sm" to="/inbox?tab=approve">
            Карточка на картинки
          </Link>
        ) : null}
        {j.clip_url ? (
          <Link className="btn primary sm" to={j.clip_url}>
            Открыть клип
          </Link>
        ) : null}
        {j.files["sheet.jpg"] ? (
          <a className="btn ghost sm" href={j.files["sheet.jpg"]} target="_blank" rel="noreferrer">
            Лист кадров
          </a>
        ) : null}
        {j.files["sync_check.mp4"] ? (
          <a className="btn ghost sm" href={j.files["sync_check.mp4"]} target="_blank" rel="noreferrer">
            Сверка синхрона
          </a>
        ) : null}
        {["failed", "cancelled", "needs_blender", "plan_review"].includes(j.status) ? (
          <button className="btn sm" disabled={resume.isPending} onClick={() => resume.mutate()}>
            Продолжить
          </button>
        ) : null}
        {!DONE.has(j.status) ? (
          <button className="btn ghost sm" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
            Отменить
          </button>
        ) : null}
      </div>
    </div>
  );
}
