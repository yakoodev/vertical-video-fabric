/**
 * «Звуковые дорожки» источника (app/source_audio.py, GET/PATCH /api/sources/{id}/audio).
 *
 * Запись NVIDIA DVR / OBS несёт игру и микрофон отдельными дорожками; по умолчанию берётся
 * первая — и голос стримера пропадает. Здесь: какие дорожки идут в звук ролика, громкость
 * каждой (дБ, amix сводит поровну — тихий микрофон тонет), «Авто: голос над игрой» (сервер
 * замерит дорожки и поставит голос на ~9 дБ над игрой в моменты речи) и дорожка речи для субтитров.
 * Выбор общий для всех клипов источника. Сменился звук для распознавания — расшифровка
 * исходника сбрасывается сервером; предупреждаем до сохранения.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { sourcesApi, type SourceAudio, type SourceAudioSaved, type SourceAudioStream } from "@/api/sources";
import type { SourceDetail } from "@/api/types";

/** Как app/source_audio: MIN_GAIN_DB..MAX_GAIN_DB. */
const GAIN_MIN_DB = -30;
const GAIN_MAX_DB = 30;

type Draft = { on: Record<number, boolean>; gain: Record<number, number>; speech: number | null };

function draftOf(audio: SourceAudio): Draft {
  const on: Record<number, boolean> = {};
  const gain: Record<number, number> = {};
  for (const s of audio.streams) {
    const m = audio.mix.find((x) => x.track === s.index);
    on[s.index] = Boolean(m);
    gain[s.index] = m?.gain_db ?? 0;
  }
  return { on, gain, speech: audio.speech_separate ? audio.speech_tracks[0] ?? null : null };
}

function mixOf(d: Draft, streams: SourceAudioStream[]): { track: number; gain_db: number }[] {
  return streams.filter((s) => d.on[s.index]).map((s) => ({ track: s.index, gain_db: d.gain[s.index] ?? 0 }));
}

/** Звук для распознавания, как app/source_audio.speech_mix: своя дорожка (без усилений) или смесь ролика. */
function speechKey(d: Draft, streams: SourceAudioStream[]): string {
  const mix = d.speech != null ? [{ track: d.speech, gain_db: 0 }] : mixOf(d, streams);
  return JSON.stringify(mix.length ? mix : [{ track: 0, gain_db: 0 }]);
}

function channelsLabel(n?: number | null): string {
  if (!n) return "";
  return n === 1 ? "моно" : n === 2 ? "стерео" : `${n} кан.`;
}

function streamLabel(s: SourceAudioStream): string {
  return [`Дорожка ${s.index + 1}`, s.title, channelsLabel(s.channels), s.language && s.language !== "und" ? s.language : ""]
    .filter(Boolean)
    .join(" · ");
}

export function SourceAudioPanel({ sourceId }: { sourceId: number }) {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.sourceAudio(sourceId), queryFn: () => sourcesApi.audio(sourceId) });
  const audio = query.data;
  const streams = audio?.streams ?? [];
  const saved = useMemo(() => (audio ? draftOf(audio) : null), [audio]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "warn"; text: string } | null>(null);

  useEffect(() => setDraft(saved), [saved]);

  if (query.isLoading) return <div className="muted li-note">Смотрю дорожки записи…</div>;
  if (query.isError || !audio || !draft || !saved)
    return <div className="lay-note warn">Не удалось прочитать звуковые дорожки источника</div>;
  if (streams.length <= 1)
    return (
      <div className="muted li-note">
        {streams.length ? "В записи одна звуковая дорожка — выбирать нечего." : "Звуковых дорожек в записи не нашлось."}
      </div>
    );

  const mix = mixOf(draft, streams);
  const dirty =
    JSON.stringify(mix) !== JSON.stringify(mixOf(saved, streams)) || draft.speech !== saved.speech;
  const speechChanges = speechKey(draft, streams) !== speechKey(saved, streams);

  const remember = (res: SourceAudioSaved, speechReset: boolean) => {
    qc.setQueryData<SourceAudio>(qk.sourceAudio(sourceId), res.audio);
    // Источник в кэше редактора — те же поля, что отдаёт GET /api/sources/{id}.
    qc.setQueryData<SourceDetail>(qk.source(sourceId), (prev) =>
      prev
        ? {
            ...prev,
            audio_tracks: res.audio.selected,
            audio_mix: res.audio.mix,
            speech_tracks: res.audio.speech_separate ? res.audio.speech_tracks : null,
            ...(speechReset ? { has_transcript: false, transcript_segments: 0 } : {}),
          }
        : prev,
    );
  };

  const send = async (
    body: Parameters<typeof sourcesApi.setAudio>[1],
    okText: (r: SourceAudioSaved, speechReset: boolean) => string,
  ) => {
    setBusy(true);
    setNote(null);
    try {
      const res = await sourcesApi.setAudio(sourceId, body);
      // Сервер сбрасывает расшифровку, если сменился звук для распознавания (store.update_source).
      const speechReset = speechKey(draftOf(res.audio), streams) !== speechKey(saved, streams);
      remember(res, speechReset);
      setNote({ kind: "ok", text: okText(res, speechReset) });
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof ApiError ? e.message : "Не удалось сохранить дорожки" });
    } finally {
      setBusy(false);
    }
  };

  const resetText = "Расшифровка исходника сброшена и распознается заново; субтитры готовых клипов — «Распознать заново».";

  const apply = () =>
    void send(
      { audio_tracks: mix, speech_tracks: draft.speech == null ? [] : [draft.speech] },
      (_r, reset) => (reset ? `Сохранено. ${resetText}` : "Сохранено: так звучат все клипы источника"),
    );

  // Голос — дорожка речи (если задана), иначе последняя (у DVR/OBS микрофон — вторая).
  const voice = draft.speech ?? streams[streams.length - 1].index;
  const auto = () => {
    const body = {
      audio_tracks: streams.map((s) => ({ track: s.index, gain_db: s.index === voice ? ("auto" as const) : 0 })),
      speech_tracks: draft.speech == null ? [] : [draft.speech],
    };
    void send(body, (r, reset) => {
      const db = r.auto_gain?.[String(voice)];
      const head = db == null ? "Смешал все дорожки" : `Голос (дорожка ${voice + 1}) ${db >= 0 ? "+" : ""}${db} дБ — на ~9 дБ над игрой, пока говорит`;
      return reset ? `${head}. ${resetText}` : head;
    });
  };

  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });

  return (
    <div className="src-audio">
      <div className="muted li-note">
        Общие для всех клипов источника. Отмеченные дорожки смешиваются в звук ролика, у каждой своя громкость; ролик
        потом всё равно выравнивается до −14 LUFS — громкость задаёт баланс голоса и игры.
      </div>
      <div className="src-audio-list">
        {streams.map((s) => (
          <div key={s.index} className={`src-audio-row${draft.on[s.index] ? "" : " off"}`}>
            <label className="src-audio-name" title={`Номер для агента и API: ${s.index}`}>
              <input
                type="checkbox"
                checked={Boolean(draft.on[s.index])}
                disabled={busy}
                onChange={(e) => set({ on: { ...draft.on, [s.index]: e.target.checked } })}
              />
              <span>{streamLabel(s)}</span>
            </label>
            <input
              type="number"
              className="input mono src-audio-gain"
              step={1}
              min={GAIN_MIN_DB}
              max={GAIN_MAX_DB}
              value={draft.gain[s.index] ?? 0}
              disabled={busy || !draft.on[s.index]}
              title="Громкость дорожки в смеси, дБ: плюс — громче, минус — тише"
              aria-label={`Громкость: ${streamLabel(s)}`}
              onChange={(e) => {
                const db = Number(e.target.value);
                if (!Number.isFinite(db)) return;
                const clamped = Math.round(Math.min(GAIN_MAX_DB, Math.max(GAIN_MIN_DB, db)) * 10) / 10;
                set({ gain: { ...draft.gain, [s.index]: clamped } });
              }}
            />
            <span className="muted">дБ</span>
          </div>
        ))}
      </div>
      <label className="field">
        <span title="По ней — расшифровка исходника, субтитры, «послушать» и сверка субтитров с речью. На одном микрофоне распознавание чище, чем на смеси с игрой">
          Дорожка речи (для субтитров)
        </span>
        <select
          className="input"
          value={draft.speech == null ? "" : String(draft.speech)}
          disabled={busy}
          onChange={(e) => set({ speech: e.target.value === "" ? null : Number(e.target.value) })}
        >
          <option value="">Как звук ролика</option>
          {streams.map((s) => (
            <option key={s.index} value={s.index}>
              {streamLabel(s)}
            </option>
          ))}
        </select>
      </label>
      {!mix.length ? <div className="lay-note warn">Отметьте хотя бы одну дорожку — иначе ролик без звука</div> : null}
      {dirty && speechChanges ? (
        <div className="lay-note warn">
          Сменится звук для распознавания речи: расшифровка исходника пересчитается заново, субтитры готовых клипов
          придётся распознать заново.
        </div>
      ) : null}
      <div className="lay-row">
        <button type="button" className="btn sm primary" disabled={busy || !dirty || !mix.length} onClick={apply}>
          {busy ? "Сохраняю…" : "Применить"}
        </button>
        <button type="button" className="btn ghost sm" disabled={busy || !dirty} onClick={() => setDraft(saved)}>
          Отменить
        </button>
        <span className="lay-grow" />
        <button
          type="button"
          className="btn sm"
          disabled={busy}
          onClick={auto}
          title={`Смешать все дорожки и замерить громкость: голос (дорожка ${voice + 1}) встанет на ~9 дБ громче игры в моменты речи. Если речь не на своей дорожке — расшифровка исходника пересчитается`}
        >
          Авто: голос над игрой
        </button>
      </div>
      {note ? <div className={`lay-note ${note.kind}`}>{note.text}</div> : null}
    </div>
  );
}
