import { useDeferredValue, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { videoMemesApi, type VideoMeme, type VideoMemePatch } from "@/api/videoMemes";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import "@/styles/video-memes.css";

type Show = "all" | "waiting" | "approved";
const SHOW: [Show, string][] = [
  ["all", "Все"],
  ["waiting", "Ждут одобрения"],
  ["approved", "Одобренные"],
];

const TRANSCRIPT_LABEL: Record<VideoMeme["transcript_status"], string> = {
  pending: "расшифровка в очереди…",
  running: "Whisper слушает…",
  done: "",
  empty: "слов нет",
  failed: "расшифровка не удалась",
  off: "без расшифровки",
};

const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const num = (v: string) => (v.trim() === "" || Number.isNaN(Number(v)) ? null : Number(v));

/** Превью: кадр, под курсором мем играет без звука; по нажатию — плеер со звуком. */
function MemeMedia({ meme }: { meme: VideoMeme }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [open, setOpen] = useState(false);
  if (open) return <video src={meme.url} poster={meme.thumb_url} controls autoPlay playsInline />;
  return (
    <button
      type="button"
      className="vmeme-hover"
      aria-label={`Смотреть со звуком: ${meme.title}`}
      onClick={() => setOpen(true)}
      onMouseEnter={() => void ref.current?.play().catch(() => undefined)}
      onMouseLeave={() => {
        const v = ref.current;
        if (v) {
          v.pause();
          v.currentTime = 0;
        }
      }}
    >
      <video ref={ref} src={meme.url} poster={meme.thumb_url} muted loop playsInline preload="none" />
      <span className="vthumb-play" aria-hidden="true">
        ▶
      </span>
    </button>
  );
}

function MemeCard({ meme, onDelete }: { meme: VideoMeme; onDelete: (m: VideoMeme) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState(meme.title);
  const [description, setDescription] = useState(meme.description);
  const [whenToUse, setWhenToUse] = useState(meme.when_to_use);
  const [tags, setTags] = useState(meme.tags.join(", "));
  const [transcript, setTranscript] = useState(meme.transcript);
  const [license, setLicense] = useState(meme.license_note);
  // Расшифровка пришла из фона — показать её, пока поле не трогали.
  const [seenTranscript, setSeenTranscript] = useState(meme.transcript);
  if (meme.transcript !== seenTranscript) {
    setSeenTranscript(meme.transcript);
    setTranscript(meme.transcript);
  }

  const save = useMutation({
    mutationFn: (patch: VideoMemePatch) => videoMemesApi.update(meme.id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.videoMemes }),
    onError: (e) => toast.error(err(e, "Не удалось сохранить")),
  });
  const retranscribe = useMutation({
    mutationFn: () => videoMemesApi.transcribe(meme.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.videoMemes }),
    onError: (e) => toast.error(err(e, "Не удалось запустить расшифровку")),
  });
  const commit = (field: "title" | "description" | "when_to_use" | "transcript" | "license_note", value: string) => {
    const was = field === "title" ? meme.title : meme[field];
    if (value.trim() !== (was ?? "").trim()) save.mutate({ [field]: value });
  };
  const commitTags = () => {
    const next = tags.split(/[,#\n]+/).map((t) => t.trim().toLowerCase()).filter(Boolean);
    if (next.join(",") !== meme.tags.join(",")) save.mutate({ tags: next });
  };
  const busyTranscript = meme.transcript_status === "pending" || meme.transcript_status === "running";

  return (
    <div className={`asset-card vmeme-card${meme.approved ? " is-approved" : ""}`}>
      <div className="asset-media vmeme-media">
        <MemeMedia meme={meme} />
        <span className={`asset-kind vmeme-state${meme.approved ? " ok" : ""}`}>
          {meme.approved ? "✓ одобрен" : "ждёт одобрения"}
        </span>
        <span className="asset-dur mono">{meme.duration_sec.toFixed(1)} с</span>
        <button className="card-del" title="Удалить мем вместе с файлом" onClick={() => onDelete(meme)}>
          🗑
        </button>
      </div>
      <input
        className="input asset-label"
        value={title}
        maxLength={120}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={() => commit("title", title)}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      />
      <button
        className={`btn sm ${meme.approved ? "ghost" : "primary"}`}
        disabled={save.isPending}
        onClick={() => save.mutate({ approved: !meme.approved })}
        title="ИИ ставит в клипы только одобренные мемы"
      >
        {meme.approved ? "Снять одобрение" : "✓ Одобрить для ИИ"}
      </button>
      <div className="vmeme-transcript">
        <div className="vmeme-row">
          <span className="vmeme-cap">Что сказано{meme.transcript_lang ? ` · ${meme.transcript_lang}` : ""}</span>
          {meme.transcript_status !== "done" ? (
            <span className={`vmeme-status${meme.transcript_status === "failed" ? " bad" : ""}`} title={meme.transcript_error}>
              {TRANSCRIPT_LABEL[meme.transcript_status]}
            </span>
          ) : null}
          {meme.has_audio ? (
            <button
              className="btn ghost sm vmeme-mini"
              disabled={busyTranscript || retranscribe.isPending}
              onClick={() => retranscribe.mutate()}
              title="Распознать речь заново (Whisper)"
            >
              ↻
            </button>
          ) : null}
        </div>
        <textarea
          className="input asset-desc"
          rows={2}
          placeholder={meme.has_audio ? "расшифровка появится сама" : "в файле нет звука"}
          value={transcript}
          maxLength={4000}
          onChange={(e) => setTranscript(e.target.value)}
          onBlur={() => commit("transcript", transcript)}
        />
      </div>
      <textarea
        className="input asset-desc"
        rows={2}
        placeholder="Что значит: «чел в шоке хватается за голову»"
        value={description}
        maxLength={1000}
        onChange={(e) => setDescription(e.target.value)}
        onBlur={() => commit("description", description)}
      />
      <textarea
        className="input asset-desc"
        rows={2}
        placeholder="Когда вставлять: «после нелепой смерти», «тиммейт подвёл»"
        value={whenToUse}
        maxLength={1000}
        onChange={(e) => setWhenToUse(e.target.value)}
        onBlur={() => commit("when_to_use", whenToUse)}
      />
      <input
        className="input asset-tags"
        placeholder="теги: шок, фейл, победа…"
        value={tags}
        maxLength={300}
        onChange={(e) => setTags(e.target.value)}
        onBlur={commitTags}
      />
      <label className="vmeme-check">
        <input
          type="checkbox"
          checked={meme.has_real_people}
          onChange={(e) => save.mutate({ has_real_people: e.target.checked })}
        />
        реальные люди в кадре
      </label>
      <input
        className="input vmeme-license"
        placeholder="лицензия"
        value={license}
        maxLength={300}
        onChange={(e) => setLicense(e.target.value)}
        onBlur={() => commit("license_note", license)}
      />
      {meme.source_url ? (
        <a className="vmeme-source" href={meme.source_url} target="_blank" rel="noreferrer noopener">
          источник
        </a>
      ) : null}
    </div>
  );
}

function UrlForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const add = useMutation({
    mutationFn: () => videoMemesApi.fromUrl(url.trim(), num(start), num(end)),
    onSuccess: (m) => {
      toast.success(`Мем «${m.title}» добавлен — расшифровка идёт в фоне`);
      setUrl("");
      setStart("");
      setEnd("");
      onDone();
    },
    onError: (e) => toast.error(err(e, "Не удалось скачать")),
  });
  return (
    <form
      className="vmeme-url"
      onSubmit={(e) => {
        e.preventDefault();
        if (url.trim()) add.mutate();
      }}
    >
      <input className="input" placeholder="Ссылка на ролик (YouTube, TikTok…)" value={url} onChange={(e) => setUrl(e.target.value)} />
      <input className="input mono" inputMode="decimal" placeholder="с, от" value={start} onChange={(e) => setStart(e.target.value)} />
      <input className="input mono" inputMode="decimal" placeholder="с, до" value={end} onChange={(e) => setEnd(e.target.value)} />
      <button className="btn primary" type="submit" disabled={!url.trim() || add.isPending}>
        {add.isPending ? "Качаю…" : "Добавить по ссылке"}
      </button>
    </form>
  );
}

/**
 * «Видеомемы» — реакции 0.5–4 с для мем-монтажа (Апекс + «вау», молитва, крик). ИИ находит их по смыслу
 * (search_video_memes) и врезает в клип (place_video_meme), но только одобренные.
 */
export function VideoMemesPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [show, setShow] = useState<Show>("all");
  const [tag, setTag] = useState<string | null>(null);
  const [autoTrim, setAutoTrim] = useState(true);
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [pending, setPending] = useState<VideoMeme | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const typed = useDeferredValue(q);
  const filter = useMemo(
    () => ({ q: typed, tag: tag ?? undefined, approved: show === "all" ? undefined : show === "approved" }),
    [typed, tag, show],
  );
  const query = useQuery({
    queryKey: [...qk.videoMemes, filter],
    queryFn: () => videoMemesApi.list(filter),
    // Пока Whisper слушает новые мемы — подтягиваем расшифровку.
    refetchInterval: (qr) =>
      qr.state.data?.memes.some((m) => m.transcript_status === "pending" || m.transcript_status === "running") ? 4000 : false,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: qk.videoMemes });
  const memes = query.data?.memes ?? [];
  const tagChoices = useMemo(() => {
    const seen = new Set<string>(query.data?.suggested_tags ?? []);
    memes.forEach((m) => m.tags.forEach((t) => seen.add(t)));
    return [...seen];
  }, [memes, query.data?.suggested_tags]);

  const uploadAll = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(list.length);
    let ok = 0;
    for (const f of list) {
      try {
        await videoMemesApi.upload(f, { autoTrim });
        ok += 1;
      } catch (e) {
        toast.error(`${f.name}: ${err(e, "не загрузился")}`);
      }
      setUploading((n) => n - 1);
    }
    refresh();
    if (ok) toast.success(`Загружено мемов: ${ok}. Расшифровка идёт в фоне; одобрите, чтобы ИИ мог их ставить`);
  };

  const del = useMutation({
    mutationFn: (id: number) => videoMemesApi.remove(id),
    onSuccess: () => {
      toast.success("Мем удалён");
      setPending(null);
      refresh();
      qc.invalidateQueries({ queryKey: qk.montageAssets });
    },
    onError: (e) => toast.error(err(e, "Не удалось удалить")),
  });

  return (
    <>
      <div
        className={`asset-drop${drag ? " on" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          void uploadAll(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/*"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) void uploadAll(e.target.files);
            e.target.value = "";
          }}
        />
        <strong>{uploading ? `Загружаю… осталось ${uploading}` : "Перетащите видеомемы сюда или нажмите"}</strong>
        <span className="muted">
          короткие реакции со звуком, 0.5–4 с (до 8) · речь распознаётся сама · ИИ ставит только одобренные
        </span>
      </div>
      <div className="vmeme-tools">
        <UrlForm onDone={refresh} />
        <label className="vmeme-check" title="Срезать тишину и чёрный кадр в начале и в конце">
          <input type="checkbox" checked={autoTrim} onChange={(e) => setAutoTrim(e.target.checked)} />
          срезать тишину по краям
        </label>
      </div>
      <div className="vmeme-filter">
        <input
          className="input"
          type="search"
          placeholder="Поиск по смыслу: шок, фейл, «тиммейт»…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <nav className="pills">
          {SHOW.map(([key, label]) => (
            <button key={key} className={`pill${show === key ? " on" : ""}`} onClick={() => setShow(key)}>
              {label}
            </button>
          ))}
        </nav>
      </div>
      <nav className="pills vmeme-tags">
        {tagChoices.map((t) => (
          <button key={t} className={`pill${tag === t ? " on" : ""}`} onClick={() => setTag(tag === t ? null : t)}>
            #{t}
          </button>
        ))}
      </nav>
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !memes.length ? (
        <EmptyState
          icon="🎬"
          title={q || tag || show !== "all" ? "Ничего не нашлось" : "Видеомемов пока нет"}
          hint="Закиньте пару реакций («вау», молитва, крик) — ИИ-монтажёр вставит их в моменты клипа"
        />
      ) : (
        <div className="asset-grid">
          {memes.map((m) => (
            <MemeCard key={m.id} meme={m} onDelete={setPending} />
          ))}
        </div>
      )}
      <ConfirmDialog
        open={Boolean(pending)}
        title="Удалить видеомем?"
        body="Мем и его файл пропадут из библиотеки. Уже отрендеренные клипы не изменятся; во вставках клипов он будет пропущен."
        busy={del.isPending}
        onCancel={() => setPending(null)}
        onConfirm={() => pending && del.mutate(pending.id)}
      />
    </>
  );
}
