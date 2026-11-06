import { FxChainEditor } from "@/pages/workspace/FxChainEditor";
import { ApiError } from "@/api/client";
import { Group } from "@/pages/workspace/InspectorParts";
import { SourceAudioPanel } from "@/pages/workspace/SourceAudioPanel";
import { CENSOR_STYLES, muteTotal, type CensorStyle } from "@/api/mute";
import { GAIN_MAX_DB, GAIN_MIN_DB, normalizeGain } from "@/api/gain";
import { formatDuration } from "@/components/ui";
import { clipTimeOf, formatTimecode } from "@/pages/workspace/editor/editorHelpers";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Вкладка «Звук»: музыка, тишина, громкость, дорожки видео, сведение и эффекты. */
export function InspectorAudioPane({ session }: { session: DraftsEditorSession }) {
  const {
    videoRef, tracks, musicPreviewRef, musicFromUrl, mixReport, autoMix, censorMat, activePlan, tcSegsRef,
    tlSelected, setTlSelected, musicOn, setMusicOn, trackId, setTrackId, musicStartSec, setMusicStartSec, musicUrl,
    setMusicUrl, mute, setMute, gain, setGain, mixSettings, setMixSettings, censorStyle, setCensorStyle, source,
    isCanvas, seekClipTime, inspActive,
  } = session;
  return (
    <div className="insp-pane" hidden={inspActive !== "audio"} role="tabpanel" aria-label="Звук">
      <Group title="Музыка" badge={musicOn ? "вкл" : "выкл"}>
        <label className="switch">
          <input type="checkbox" checked={musicOn} onChange={(e) => setMusicOn(e.target.checked)} />
          <span className="switch-track" />
          <span>Фоновый трек</span>
        </label>
        {musicOn ? (
          <>
            <div className="lay-row">
              <label className="field lay-grow">
                <span>Трек</span>
                <select
                  className="input"
                  value={trackId}
                  onChange={(e) => {
                    setTrackId(Number(e.target.value));
                    setMusicStartSec(0);
                  }}
                >
                  <option value={0}>по умолчанию</option>
                  {tracks.data?.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                      {t.duration_sec ? ` · ${formatDuration(t.duration_sec)}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn ghost sm music-refresh"
                title="Обновить список треков"
                disabled={tracks.isFetching}
                onClick={() => void tracks.refetch()}
              >
                {tracks.isFetching ? "…" : "↻"}
              </button>
            </div>
            {trackId ? (
              <>
                <audio
                  ref={musicPreviewRef}
                  className="music-preview"
                  src={`/media/audio-tracks/${trackId}`}
                  controls
                  preload="none"
                  // Прослушка с выбранного старта: так и зазвучит на 0 с клипа.
                  onPlay={(e) => {
                    const a = e.currentTarget;
                    if (musicStartSec > 0 && a.currentTime < musicStartSec - 0.25) a.currentTime = musicStartSec;
                  }}
                />
                <label className="field range">
                  <span title="Музыка на 0 с клипа играет с этой секунды трека — пропустить тихое вступление и начать сразу с барабанов">
                    Старт трека · {musicStartSec.toFixed(1)} с
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={Math.max(1, Math.floor(tracks.data?.find((t) => t.id === trackId)?.duration_sec || 300))}
                    step={0.1}
                    value={musicStartSec}
                    onChange={(e) => setMusicStartSec(Number(e.target.value))}
                  />
                </label>
                <div className="lay-row">
                  <button
                    type="button"
                    className="btn ghost sm"
                    title="Нашли на слух, где вступают барабаны, — поставьте плеер на паузу там и нажмите"
                    onClick={() => setMusicStartSec(Math.round((musicPreviewRef.current?.currentTime ?? 0) * 10) / 10)}
                  >
                    ⏱ Старт отсюда (из плеера)
                  </button>
                  {musicStartSec > 0 ? (
                    <button type="button" className="btn ghost sm" onClick={() => setMusicStartSec(0)}>
                      С начала
                    </button>
                  ) : null}
                </div>
              </>
            ) : null}
            <div className="lay-row">
              <input
                className="input lay-grow"
                type="url"
                placeholder="Ссылка на трек: YouTube, SoundCloud…"
                value={musicUrl}
                disabled={musicFromUrl.isPending}
                onChange={(e) => {
                  setMusicUrl(e.target.value);
                  musicFromUrl.reset();
                }}
                onKeyDown={(e) => e.key === "Enter" && musicUrl.trim() && musicFromUrl.mutate()}
              />
              <button
                type="button"
                className="btn sm"
                disabled={musicFromUrl.isPending || !/^https?:\/\//i.test(musicUrl.trim())}
                onClick={() => musicFromUrl.mutate()}
                title="Сервер скачает только звук (до 15 минут) и добавит трек в список"
              >
                {musicFromUrl.isPending ? "Скачиваю…" : "Добавить по ссылке"}
              </button>
            </div>
            {musicFromUrl.isPending ? (
              <div className="lay-note ok">Скачиваю звук — это может занять минуту-другую, окно можно не трогать.</div>
            ) : musicFromUrl.isError ? (
              <div className="lay-note warn">
                {musicFromUrl.error instanceof ApiError ? musicFromUrl.error.message : "Не удалось скачать трек"}
              </div>
            ) : null}
          </>
        ) : null}
      </Group>
      {/* Холст: звука исходника нет — глушить, громкость и дорожки не нужны. */}
      {isCanvas ? null : (
      <>
      <Group
        title="Тишина вместо звука"
        badge={mute.length ? `${mute.length} · ${muteTotal(mute).toFixed(1)} с` : "нет"}
        forceOpen={tlSelected?.kind === "mute"}
      >
        <div className="muted li-note">
          Звук видео глушится, картинка остаётся (вшитый эффект, шум, мат); звуки слоёв и музыка играют.
          Протяните мышью по дорожке «Звук» или нажмите «🔇 Заглушить» над таймлайном.
        </div>
        <div className="ed-quick-row">
          <button
            type="button"
            className="btn sm"
            disabled={!activePlan || censorMat.isPending}
            onClick={() => activePlan && censorMat.mutate(activePlan.id)}
            title="По словам субтитров: каждое матерное слово глушится серединой (человек поймёт, автосубтитры площадок — нет), в субтитрах — «б****». Ручные отрезки не трогает, повторное нажатие заменяет только свои. Ctrl+Z откатит."
          >
            {censorMat.isPending ? "Ищу мат…" : "Заглушить мат"}
          </button>
          <select
            className="input"
            style={{ flex: "1 1 120px", minWidth: 0 }}
            value={censorStyle}
            onChange={(e) => setCensorStyle(e.target.value as CensorStyle | "")}
            title="Чем заполнить заглушённую середину слова. Превью глушит тишиной — как звучит, слышно в рендере"
          >
            <option value="">как раньше у клипа (новый — задом наперёд)</option>
            {(Object.keys(CENSOR_STYLES) as CensorStyle[]).map((s) => (
              <option key={s} value={s}>{CENSOR_STYLES[s]}</option>
            ))}
          </select>
        </div>
        {mute.length ? (
          <div className="ed-layer-list">
            {mute.map((m, i) => (
              <div
                key={`${m.start}-${m.end}-${m.by ?? ""}`}
                className={`ed-layer-row${tlSelected?.kind === "mute" && tlSelected.index === i ? " on" : ""}`}
                onClick={() => {
                  setTlSelected({ kind: "mute", index: i });
                  seekClipTime(Math.max(0, m.start - 1), false);
                }}
                title={
                  m.by === "censor"
                    ? `Мат, уровень ${m.level ?? 1}, ${CENSOR_STYLES[m.style ?? "silence"]} (кнопка «Заглушить мат» заменит). Перейти на секунду раньше`
                    : "Перейти на секунду раньше отрезка — послушать стык"
                }
              >
                <span className="ed-layer-ico">{m.by === "censor" ? "🤬" : "🔇"}</span>
                <span className="ed-layer-name mono">
                  {formatTimecode(m.start)} – {formatTimecode(m.end)}
                </span>
                <span className="mono muted ed-layer-time">{(m.end - m.start).toFixed(1)} с</span>
                <button
                  type="button"
                  className="btn ghost sm"
                  title="Вернуть звук"
                  onClick={(e) => {
                    e.stopPropagation();
                    setMute((prev) => prev.filter((_, j) => j !== i));
                    setTlSelected(null);
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </Group>
      <Group title="Громкость видео" badge={gain.length ? `${gain.length} отр.` : "как есть"}>
        <div className="muted li-note">
          Поднять тихий кусок (голос под игрой) или приглушить громкий: звук видео на отрезке громче/тише
          на столько дБ, картинка та же; звуки слоёв и музыка не меняются. Тихий микрофон по всему ролику —
          громкость дорожки в звуке проекта.
        </div>
        {gain.length ? (
          <div className="ed-layer-list">
            {gain.map((g, i) => (
              <div key={`${g.start}-${g.end}`} className="ed-layer-row" title="Перейти на секунду раньше отрезка">
                <span className="ed-layer-ico">🔊</span>
                <span
                  className="ed-layer-name mono"
                  onClick={() => seekClipTime(Math.max(0, g.start - 1), false)}
                  style={{ cursor: "pointer" }}
                >
                  {formatTimecode(g.start)} – {formatTimecode(g.end)}
                </span>
                <input
                  type="number"
                  className="input mono"
                  style={{ width: 70 }}
                  step={1}
                  min={GAIN_MIN_DB}
                  max={GAIN_MAX_DB}
                  value={g.gain_db}
                  title="дБ: плюс — громче, минус — тише"
                  onChange={(e) => {
                    const db = Number(e.target.value);
                    if (!Number.isFinite(db)) return;
                    const clamped = Math.round(Math.min(GAIN_MAX_DB, Math.max(GAIN_MIN_DB, db)) * 10) / 10;
                    setGain((prev) => prev.map((x, j) => (j === i ? { ...x, gain_db: clamped } : x)));
                  }}
                />
                <span className="muted">дБ</span>
                <button
                  type="button"
                  className="btn ghost sm"
                  title="Убрать отрезок"
                  onClick={() => setGain((prev) => prev.filter((_, j) => j !== i))}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <button
          type="button"
          className="btn ghost sm"
          title="Отрезок 3 с от курсора, +6 дБ — потом поправьте дБ"
          onClick={() => {
            const v = videoRef.current;
            const t = v ? clipTimeOf(tcSegsRef.current, v.currentTime) : null;
            const at = t ?? 0;
            setGain((prev) => normalizeGain([...prev, { start: at, end: at + 3, gain_db: 6 }]));
          }}
        >
          + Громче у курсора
        </button>
      </Group>
      <Group
        title="Звуковые дорожки"
        badge={
          (source.audio_tracks?.length ?? 0) > 1
            ? `смесь ${source.audio_tracks!.map((t) => t + 1).join("+")}`
            : source.audio_tracks?.length
              ? `дорожка ${source.audio_tracks[0] + 1}`
              : "по умолчанию"
        }
      >
        <SourceAudioPanel sourceId={source.id} />
      </Group>
      </>
      )}
      {/* Сведение — последний шаг звука: после музыки, тишины, громкости и дорожек. */}
      <Group
        title="Сведение и эффекты"
        badge={mixSettings.auto ? "авто" : mixSettings.music_db != null || mixSettings.voice_fx || mixSettings.music_fx ? "своё" : undefined}
      >
        <div
          className="muted li-note"
          title="«Проверить» меряет по стемам (как сведёт рендер); «Автосведение» выставит громкость музыки, ducking по фразам и звуки под голос"
        >
          Цель: музыка под речью на 12–18 дБ тише голоса, звуки не закрывают слова, ролик −14 LUFS.
        </div>
        <div className="lay-row">
          <button
            type="button"
            className="btn sm"
            disabled={!activePlan || mixReport.isPending}
            onClick={() => activePlan && mixReport.mutate(activePlan.id)}
          >
            {mixReport.isPending ? "Слушаю…" : "Проверить сведение"}
          </button>
          <button
            type="button"
            className="btn sm"
            disabled={!activePlan || autoMix.isPending}
            onClick={() => activePlan && autoMix.mutate(activePlan.id)}
            title="Громкость музыки под голосом, ducking на каждую фразу, звуки слоёв под голос, −14 LUFS"
          >
            {autoMix.isPending ? "Свожу…" : "Автосведение"}
          </button>
        </div>
        {mixReport.data ? (
          <div className="mix-report">
            <div className={mixReport.data.verdicts.some((v) => v.severity === "warn") ? "lay-note warn" : "lay-note ok"}>
              {mixReport.data.summary}
            </div>
            {mixReport.data.timeline?.line ? (
              <div className="mix-line" title={mixReport.data.timeline.line_scale}>
                {mixReport.data.timeline.line}
              </div>
            ) : null}
            {mixReport.data.verdicts.map((v, i) => (
              <div key={i} className={`lay-note ${v.severity === "warn" ? "warn" : ""}`}>
                {v.message}
                {v.fix ? <span className="muted"> → {v.fix}</span> : null}
              </div>
            ))}
          </div>
        ) : null}
        {mixSettings.music_db != null ? (
          <div className="lay-row">
            <span className="muted li-note">
              Музыка: {mixSettings.music_db.toFixed(1)} дБ к треку
              {mixSettings.sidechain === false ? " · ducking по фразам" : ""}
              {mixSettings.layer_gains ? ` · поправок звуков: ${Object.keys(mixSettings.layer_gains).length}` : ""}
            </span>
            <button
              type="button"
              className="btn ghost sm"
              title="Снять сведение: музыка снова подгоняется под голос"
              onClick={() => setMixSettings(({ voice_fx, music_fx }) => ({ ...(voice_fx ? { voice_fx } : {}), ...(music_fx ? { music_fx } : {}) }))}
            >
              Сбросить
            </button>
          </div>
        ) : null}
        <div className="rgroup-sub">Эффекты</div>
        <FxChainEditor
          title={isCanvas ? "Голос (слои-озвучка)" : "Голос (звук видео)"}
          chain={mixSettings.voice_fx}
          onChange={(voice_fx) => setMixSettings((m) => ({ ...m, voice_fx }))}
        />
        {musicOn ? (
          <FxChainEditor
            title="Музыка"
            chain={mixSettings.music_fx}
            onChange={(music_fx) => setMixSettings((m) => ({ ...m, music_fx }))}
          />
        ) : null}
      </Group>
    </div>
  );
}
