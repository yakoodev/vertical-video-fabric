import { clipPlansApi } from "@/api/clipPlans";
import { qk } from "@/api/keys";
import { type Aspect, type TransitionSettings } from "@/api/types";
import { ApiError } from "@/api/client";
import { Group, MoreSection } from "@/pages/workspace/InspectorParts";
import { clipAccent, frameSize } from "@/motion";
import { LayoutPanel } from "@/pages/workspace/LayoutPanel";
import { CutsPanel } from "@/pages/workspace/CutsPanel";
import { canvasLabel } from "@/pages/workspace/canvasClip";
import { cutsTotal } from "@/api/cuts";
import { playbackApi } from "@/api/playback";
import { focusWindowPos, focusXAt } from "@/pages/workspace/editor/editorHelpers";
import { LookPicker } from "@/pages/workspace/editor/LookPicker";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Вкладка «Клип»: формат, быстрые действия, лук, раскладка, переходы, обложка, баннер, паузы, «Ещё». */
export function InspectorClipPane({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, qc, toast, videoRef, sourceMissing, banners, playbackRef, patchTransition, patchCover, plans,
    transitionOpts, flushSettings, composeCover, autoEdit, brands, applyBrand, focusOptions, setFocus, cutStrategies,
    refineCuts, autofocus, activePlan, uniquePresets, selectedPreset, lookFrames, presetId, setPresetId, bannerOn,
    setBannerOn, bannerId, setBannerId, mirror, setMirror, transition, cover, layers, camera, layout, setLayout,
    cuts, setCuts, backdrop, setBackdrop, aspect, setAspect, setLayersFmt, endHoldSec, setEndHoldSec, motionBlur,
    setMotionBlur, coverUploading, setCoverUploading, useVlmFocus, setUseVlmFocus, packagePieces, setPackagePieces,
    bannerHeightPct, setBannerHeightPct, bannerPosPct, setBannerPosPct, coverTitle, setCoverTitle, coverStyle,
    setCoverStyle, brandPick, setBrandPick, moduleOn, source, isCanvas, seekClipTime, inspActive,
  } = session;
  return (
    <div className="insp-pane" hidden={inspActive !== "clip"} role="tabpanel" aria-label="Клип">
      <Group title="Формат и концовка" badge={aspect} defaultOpen>
        <div className="sub-options">
        <label
          className="field"
          title="Формат готового ролика: видео кадрируется окном нужной пропорции, слои сами переносятся в кадр одним масштабом, фон — на весь кадр. Превью сразу показывает кадр этого формата"
        >
          <span>Формат</span>
          <select
            className="input"
            value={aspect}
            onChange={(e) => {
              const next = e.target.value as Aspect;
              setAspect(next);
              // Слоёв ещё нет — новые сразу размечаются в кадре нового формата.
              if (!layers.length && !camera.length) setLayersFmt(next);
            }}
          >
            <option value="9:16">9:16 — вертикаль</option>
            <option value="1:1">1:1 — квадрат</option>
            <option value="4:5">4:5 — лента</option>
            <option value="16:9">16:9 — горизонталь</option>
          </select>
        </label>
        <label className="field" title="Последний кадр стоит, пока затухает звук (музыка доигрывает на нём). Мему — «без стоп-кадра»: ролик обрывается на последнем кадре">
          <span>Стоп-кадр в конце</span>
          <select
            className="input"
            value={endHoldSec == null ? "" : String(endHoldSec)}
            onChange={(e) => setEndHoldSec(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">как у сервиса</option>
            <option value="0">без стоп-кадра</option>
            <option value="0.25">0.25 с</option>
            <option value="0.5">0.5 с</option>
            <option value="1">1 с</option>
            <option value="1.5">1.5 с</option>
          </select>
        </label>
        <label
          className="field"
          title="Motion blur: быстрые слои, наезды и whip камеры смазываются по пути движения (затвор — доля кадра; 50% — киношные 180°). В покое кадр не меняется. Свой у слоя — в его инспекторе, у ключа камеры — в панели камеры"
        >
          <span>Motion blur · {motionBlur ? `${Math.round(motionBlur * 100)}%` : "выкл"}</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={Math.round((motionBlur ?? 0) * 100)}
            onChange={(e) => setMotionBlur(Number(e.target.value) ? Number(e.target.value) / 100 : null)}
          />
        </label>
        </div>
      </Group>
      {/* Быстрые действия: «🤖 ИИ-монтаж» — в шапке редактора, автофокус клипа — «Авто»
          под превью (и в «Ещё… → Умный кадр»): здесь их больше не дублируем. */}
      <div className="ed-quick">
        <span className="ed-quick-title">Быстрые действия</span>
        <div className="ed-quick-row">
          <button
            className="btn sm"
            disabled={!activePlan || autoEdit.isPending}
            onClick={() => activePlan && autoEdit.mutate(activePlan.id)}
            title="Без ИИ, за секунду: хук-титр, анимированные субтитры по словам (ключевые цветом, другие голоса отдельно), наезды в героя и звуки на ударные слова. Ctrl+Z откатит."
          >
            {autoEdit.isPending ? "Собираю…" : "Автомонтаж"}
          </button>
          <a
            className="btn sm"
            href="/files"
            target="_blank"
            rel="noreferrer"
            title="Библиотека мемов (откроется в новой вкладке): ИИ ищет картинки в интернете и описывает файлы"
          >
            Найти мемы ↗
          </a>
        </div>
        {brands.data?.length ? (
          <div className="ed-quick-row">
            <select
              className="input"
              style={{ flex: "1 1 120px", minWidth: 0 }}
              value={brandPick || brands.data[0].id}
              onChange={(e) => setBrandPick(Number(e.target.value))}
              title="Бренд заказчика (Настройки → Бренды)"
            >
              {brands.data.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
            <button
              className="btn sm"
              disabled={!activePlan || applyBrand.isPending}
              onClick={() =>
                activePlan &&
                applyBrand.mutate({ clipPlanId: activePlan.id, brandId: brandPick || brands.data![0].id })
              }
              title="Субтитры и хук в цветах бренда, логотип в углу, призыв на последние секунды, музыка и фон бренда. Делай после монтажа; повтор не дублирует. Ctrl+Z откатит."
            >
              {applyBrand.isPending ? "Применяю…" : "Применить бренд"}
            </button>
          </div>
        ) : null}
      </div>
      <Group title="Картинка (лук)" badge={selectedPreset?.label ?? "по умолчанию"} defaultOpen>
        <label className="field">
          <span>Пресет</span>
          <select className="input" value={presetId} onChange={(e) => setPresetId(Number(e.target.value))}>
            <option value={0}>По умолчанию</option>
            {uniquePresets.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>
        {/* Кадры основы холста — зелёные: смотреть лук не на чем. */}
        <LookPicker frames={isCanvas ? [] : lookFrames} presets={uniquePresets} value={presetId} onPick={setPresetId} />
        <label className="switch" title="Отразить видео по горизонтали (поменять лево и право) — например чтобы репост отличался от оригинала">
          <input type="checkbox" checked={mirror} onChange={(e) => setMirror(e.target.checked)} />
          <span className="switch-track" />
          <span>Зеркало (лево↔право)</span>
        </label>
      </Group>
      <Group
        title={isCanvas ? "Фон холста" : "Раскладка кадра"}
        badge={
          isCanvas
            ? canvasLabel(backdrop).replace("Холст · ", "")
            : { single: "обычная", split: "вебка + игра", pip_cam: "вебка в углу", meme: "мем: видео сверху", avatar_full: "только аватар" }[layout.mode] ?? "обычная"
        }
        defaultOpen={isCanvas}
      >
        <LayoutPanel
          noSourceFile={sourceMissing}
          sourceId={source.id}
          layout={layout}
          onChange={setLayout}
          frameAt={videoRef.current?.currentTime ?? 0}
          videoRef={videoRef}
          segments={activePlan?.segments ?? []}
          onSeekSource={(src) => {
            const v = videoRef.current;
            if (!v) return;
            v.pause();
            playbackRef.current = null;
            v.currentTime = src;
          }}
          chromaKey={source.chroma_key}
          backdrop={backdrop}
          onBackdrop={setBackdrop}
          accent={clipAccent(layers)}
          canvas={isCanvas}
        />
      </Group>
      <Group
        title="Переходы"
        badge={transitionOpts.data?.types.find((t) => t.key === transition.type)?.label ?? "встык"}
      >
        {(activePlan?.segments.length ?? 0) < 2 ? (
          <p className="muted field-hint">
            В этом клипе один кусок — склеивать нечего. Переход сработает, когда кусков станет больше.
          </p>
        ) : null}
        <label className="field">
          <span>Склейка кусков</span>
          <select
            className="input"
            value={transition.type}
            onChange={(e) => patchTransition({ type: e.target.value as TransitionSettings["type"] })}
          >
            {(transitionOpts.data?.types ?? [{ key: "cut", label: "Встык" }]).map((t) => (
              <option key={t.key} value={t.key}>{t.label}</option>
            ))}
          </select>
        </label>
        {transition.type !== "cut" ? (
          <label className="switch" title="Внутри одной сцены (вырезанная пауза) склейка остаётся встык — эффект тратится только на смену сцены.">
            <input
              type="checkbox"
              checked={transition.smart !== false}
              onChange={(e) => patchTransition({ smart: e.target.checked })}
            />
            <span className="switch-track" />
            <span>умные склейки: внутри сцены — встык</span>
          </label>
        ) : null}
        {transition.type !== "cut" && !(transitionOpts.data?.snap ?? {})[transition.type] ? (
          <label className="field">
            <span>Длительность — {transition.duration.toFixed(2)} с</span>
            <input
              type="range"
              min={0.1}
              max={1.2}
              step={0.05}
              value={transition.duration}
              onChange={(e) => patchTransition({ duration: Number(e.target.value) })}
            />
          </label>
        ) : null}
        <label className="field">
          <span>Звук на стыке</span>
          <select
            className="input"
            value={transition.audio}
            onChange={(e) => patchTransition({ audio: e.target.value as TransitionSettings["audio"] })}
            title="Плавно — звук перетекает (на резкой склейке — без щелчка). Резко — обрывается точно на стыке."
          >
            {(transitionOpts.data?.audio ?? []).map((a) => (
              <option key={a.key} value={a.key}>{a.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Звук-эффект на каждом стыке</span>
          <select
            className="input"
            value={transition.sfx}
            onChange={(e) => patchTransition({ sfx: e.target.value as TransitionSettings["sfx"] })}
          >
            {(transitionOpts.data?.sfx ?? []).map((a) => (
              <option key={a.key} value={a.key}>{a.label}</option>
            ))}
          </select>
        </label>
        {transition.sfx !== "none" ? (
          <label className="field">
            <span>Громкость эффекта — {Math.round(transition.sfx_volume * 100)}%</span>
            <input
              type="range"
              min={0.05}
              max={1}
              step={0.05}
              value={transition.sfx_volume}
              onChange={(e) => patchTransition({ sfx_volume: Number(e.target.value) })}
            />
          </label>
        ) : null}
      </Group>
      <Group
        title="Обложка"
        badge={cover.mode === "none" ? "нет" : cover.mode === "frame" ? "кадр" : "картинка"}
      >
        <div className="cover-compose">
          <span className="li-cap">Обложка с заголовком — кадр под курсором + крупный текст</span>
          <div className="li-row">
            <input
              className="input li-grow"
              placeholder={activePlan?.title ? activePlan.title.toUpperCase() : "ЗАГОЛОВОК"}
              value={coverTitle}
              onChange={(e) => setCoverTitle(e.target.value)}
              maxLength={80}
            />
            <select className="input" value={coverStyle} onChange={(e) => setCoverStyle(e.target.value)}>
              <option value="yellow">Жёлтый</option>
              <option value="fire">Огонь</option>
              <option value="white">Белый</option>
              <option value="plate">Плашка</option>
            </select>
          </div>
          {moduleOn("covers") ? (
            <button
              type="button"
              className="btn sm"
              disabled={!activePlan || composeCover.isPending}
              onClick={() => composeCover.mutate()}
            >
              {composeCover.isPending ? "Рисую…" : "Сделать обложку"}
            </button>
          ) : null}
        </div>
        <div className="seg-toggle" role="radiogroup" aria-label="Источник обложки">
          {([
            ["none", "Нет"],
            ["frame", "Кадр из клипа"],
            ["image", "Своя картинка"],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={cover.mode === key}
              className={cover.mode === key ? "active" : ""}
              onClick={() => patchCover({ mode: key })}
            >
              {label}
            </button>
          ))}
        </div>

        {cover.mode === "frame" && activePlan ? (() => {
          const pieces = activePlan.segments;
          const idx = Math.min(cover.piece, Math.max(0, pieces.length - 1));
          const seg = pieces[idx];
          const len = seg ? seg.end_sec - seg.start_sec : 0;
          const off = Math.min(cover.offset, Math.max(0, len - 0.05));
          const t = seg ? seg.start_sec + off : 0;
          return (
            <>
              {pieces.length > 1 ? (
                <label className="field">
                  <span>Кусок</span>
                  <select
                    className="input"
                    value={idx}
                    onChange={(e) => patchCover({ piece: Number(e.target.value), offset: 0 })}
                  >
                    {pieces.map((p, i) => (
                      <option key={p.id} value={i}>
                        {i + 1}. {p.title}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <label className="field">
                <span>Момент — {off.toFixed(1)} с от начала куска</span>
                <input
                  type="range"
                  min={0}
                  max={Math.max(0.1, len - 0.05)}
                  step={0.1}
                  value={off}
                  onChange={(e) => patchCover({ offset: Number(e.target.value) })}
                />
              </label>
              {sourceMissing ? (
                <p className="muted field-hint">Кадр обложки берётся из записи стрима: её нет.</p>
              ) : seg ? (
                aspect === "9:16" ? (
                  <img
                    className="cover-preview"
                    alt="Кадр для обложки"
                    src={`/api/sources/${source.id}/frame?t=${t.toFixed(2)}&x=${focusXAt(seg, off).toFixed(3)}`}
                  />
                ) : (
                  // Другой формат: полный кадр, обрезанный окном формата у той же точки фокуса.
                  <img
                    className="cover-preview"
                    alt="Кадр для обложки"
                    src={`/api/sources/${source.id}/frame?t=${t.toFixed(2)}&w=640`}
                    style={{
                      aspectRatio: `${frameSize(aspect).w} / ${frameSize(aspect).h}`,
                      maxWidth: frameSize(aspect).w > frameSize(aspect).h ? 320 : undefined,
                      objectPosition: `${(focusWindowPos(focusXAt(seg, off), source.width, source.height, aspect) * 100).toFixed(1)}% 50%`,
                    }}
                  />
                )
              ) : null}
              <p className="muted field-hint">В рендере кадр будет уже с цветом, субтитрами и баннером.</p>
            </>
          );
        })() : null}

        {cover.mode === "image" ? (
          <>
            <label className="btn cover-upload">
              {coverUploading ? "Загружаю…" : cover.image ? "Заменить картинку…" : "Выбрать картинку…"}
              <input
                type="file"
                accept="image/*"
                hidden
                disabled={coverUploading}
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (!f) return;
                  setCoverUploading(true);
                  try {
                    const res = await clipPlansApi.uploadCover(f);
                    patchCover({ image: res.image });
                  } catch (err) {
                    toast.error(err instanceof ApiError ? err.message : "Не удалось загрузить картинку");
                  } finally {
                    setCoverUploading(false);
                  }
                }}
              />
            </label>
            {cover.image ? (
              <img
                className="cover-preview"
                alt="Обложка"
                src={`/media/covers/${cover.image}`}
                style={aspect === "9:16" ? undefined : { aspectRatio: `${frameSize(aspect).w} / ${frameSize(aspect).h}` }}
              />
            ) : (
              <p className="muted field-hint">Картинка подгонится под 1080×1920: заполнит кадр, лишнее обрежется.</p>
            )}
          </>
        ) : null}

        {cover.mode !== "none" ? (
          <>
            <label
              className="switch"
              title="Площадки, которые берут превью с первого кадра (Reels, лента TikTok), покажут обложку"
            >
              <input type="checkbox" checked={cover.burn} onChange={(e) => patchCover({ burn: e.target.checked })} />
              <span className="switch-track" />
              <span>Вшить первым кадром</span>
            </label>
            {cover.burn ? (
              <label className="field">
                <span>Сколько держать — {cover.burn_sec.toFixed(2)} с</span>
                <input
                  type="range"
                  min={0.04}
                  max={0.5}
                  step={0.02}
                  value={cover.burn_sec}
                  onChange={(e) => patchCover({ burn_sec: Number(e.target.value) })}
                />
              </label>
            ) : null}
          </>
        ) : null}
      </Group>
      <Group title="Баннер" badge={bannerOn ? "вкл" : "выкл"}>
        <label className="switch">
          <input type="checkbox" checked={bannerOn} onChange={(e) => setBannerOn(e.target.checked)} />
          <span className="switch-track" />
          <span>Накладывать баннер</span>
        </label>
        {bannerOn ? (
          <>
            <label className="field">
              <span>Картинка</span>
              <select className="input" value={bannerId} onChange={(e) => setBannerId(Number(e.target.value))}>
                <option value={0}>по умолчанию</option>
                {banners.data?.map((b) => (
                  <option key={b.id} value={b.id}>{b.label}</option>
                ))}
              </select>
            </label>
            <label className="field range">
              <span>Высота · {bannerHeightPct}%</span>
              <input
                type="range" min={6} max={30} value={bannerHeightPct}
                onChange={(e) => setBannerHeightPct(Number(e.target.value))}
              />
            </label>
            <label className="field range">
              <span>Положение · {bannerPosPct}% сверху</span>
              <input
                type="range" min={0} max={80} value={bannerPosPct}
                onChange={(e) => setBannerPosPct(Number(e.target.value))}
              />
            </label>
          </>
        ) : null}
      </Group>
      <Group
        title="Паузы и слова-паразиты"
        badge={cuts.length ? `−${cutsTotal(cuts).toFixed(1)} с` : "нет"}
      >
        {activePlan && !moduleOn("jump_cuts") ? (
          <div className="muted li-note">Модуль «Джамп-каты» выключен (Настройки → Модули).</div>
        ) : activePlan ? (
          <CutsPanel
            clipPlanId={activePlan.id}
            cuts={cuts}
            onChange={(next) => {
              // Панель уже сохранила вырезы; автосохранение повторит то же самое — не страшно.
              setCuts(next);
              void qc.invalidateQueries({ queryKey: qk.source(sourceId) });
            }}
            onSeek={(t) => seekClipTime(t, false)}
          />
        ) : null}
      </Group>
      <MoreSection id="clip" label="Ещё" hint="умный кадр, границы клипов, экспорт в монтажку">
        {/* Холст: умный кадр не нужен — кадр 9:16 целиком. */}
        {isCanvas ? null : (
        <Group
          title="Умный кадр"
          badge={focusOptions.data?.strategies.find((s) => s.key === (source.focus_strategy || "shot"))?.label ?? ""}
        >
          <label className="field">
            <span>Стратегия — как ведёт себя кадр</span>
            <select
              className="input"
              value={source.focus_strategy || "shot"}
              onChange={(e) => setFocus.mutate({ focus_strategy: e.target.value })}
              disabled={setFocus.isPending}
            >
              {focusOptions.data?.strategies.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </label>
          <span className="muted" style={{ fontSize: 11 }}>
            {focusOptions.data?.strategies.find((s) => s.key === (source.focus_strategy || "shot"))?.hint ?? ""}
          </span>
          <label className="field">
            <span>Пресет детекции — под тип видео</span>
            <select
              className="input"
              value={source.focus_preset || "balanced"}
              onChange={(e) => setFocus.mutate({ focus_preset: e.target.value })}
              disabled={setFocus.isPending}
            >
              {focusOptions.data?.presets.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
          </label>
          <label
            className="check"
            title="Детектор находит границы планов, а Gemini по одному кадру каждого плана решает, где главный объект. Точнее на сложном контенте, но тратит токены."
          >
            <input type="checkbox" checked={useVlmFocus} onChange={(e) => setUseVlmFocus(e.target.checked)} />
            <span>🤖 Уточнять кадр через Gemini (1 кадр на план)</span>
          </label>
          {moduleOn("autofocus") ? <div className="ed-btn-row">
            <button
              className="btn primary sm"
              disabled={autofocus.isPending || !plans.length}
              title="Детектор фокуса для всех черновиков этого видео (займёт время)"
              onClick={() => autofocus.mutate([])}
            >
              {autofocus.isPending ? "Анализ кадров…" : "Автофокус: все клипы"}
            </button>
            <button
              className="btn sm"
              disabled={autofocus.isPending || !activePlan?.segments.length}
              title="Только сегменты этого клипа"
              onClick={() => activePlan && autofocus.mutate(activePlan.segments.map((s) => s.id))}
            >
              Только этот клип
            </button>
          </div> : <div className="muted li-note">Модуль «Автофокус» выключен (Настройки → Модули).</div>}
        </Group>
        )}
        {/* Холст: границ клипов по речи нет — основа немая. */}
        {isCanvas ? null : (
        <Group title="Границы клипов" badge={cutStrategies.data?.find((s) => s.key === (source.cut_strategy || "phrase"))?.label ?? ""}>
          <select
            className="input"
            value={source.cut_strategy || "phrase"}
            onChange={(e) => refineCuts.mutate(e.target.value)}
            disabled={refineCuts.isPending}
          >
            {cutStrategies.data?.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
          <span className="muted" style={{ fontSize: 11.5 }}>
            {cutStrategies.data?.find((s) => s.key === (source.cut_strategy || "phrase"))?.hint ??
              "Подгоняет начало/конец клипов к фразам речи. Нужен транскрипт."}
          </span>
          <button
            className="btn sm"
            disabled={refineCuts.isPending}
            onClick={() => refineCuts.mutate(source.cut_strategy || "phrase")}
          >
            {refineCuts.isPending ? "Считаю…" : "Пересчитать"}
          </button>
        </Group>
        )}
        <Group title="Отдать в монтажку" badge="OTIO · пакет">
          <span className="muted" style={{ fontSize: 11.5 }}>
            Пакет для Premiere и DaVinci Resolve — одной кнопкой и без рендера: монтаж с дорожками, субтитры,
            картинки, звуки и музыка отдельными файлами, README на русском — что доехало, что нет и как открыть.
          </span>
          <div className="ed-foot-tools">
            <a
              className="btn sm primary"
              href={activePlan ? playbackApi.packageUrl(activePlan.id, packagePieces) : undefined}
              download
              onClick={() => void flushSettings()}
              title="Zip: FCP7 .xml + .otio, .srt, файлы мемов и звуков. Запись стрима в архив не кладём"
            >
              ⬇ Пакет для монтажки (.zip)
            </a>
            <label className="switch" title="Положить в архив короткие куски записи 720p, чтобы монтаж открылся сразу. Без галочки — только ссылка на запись и инструкция перелинковки">
              <input type="checkbox" checked={packagePieces} onChange={(e) => setPackagePieces(e.target.checked)} />
              <span className="switch-track" />
              <span>с кусками видео</span>
            </label>
          </div>
          <span className="muted" style={{ fontSize: 11.5 }}>
            Только куски и таймкоды, без файлов (субтитры, мемы и кадрирование туда не переносятся):
          </span>
          <div className="ed-foot-tools">
            <a
              className="btn sm"
              href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "otio") : undefined}
              download
              title="OpenTimelineIO — Kdenlive 25.04+ открывает нативно"
            >
              ⬇ .otio
            </a>
            <a
              className="btn sm"
              href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "edl") : undefined}
              download
              title="CMX 3600 EDL — понимают почти все монтажки"
            >
              ⬇ .edl
            </a>
            <a
              className="btn sm"
              href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "fcpxml") : undefined}
              download
              title="FCP7 XML — DaVinci Resolve, Premiere"
            >
              ⬇ .xml
            </a>
          </div>
        </Group>
      </MoreSection>
    </div>
  );
}
