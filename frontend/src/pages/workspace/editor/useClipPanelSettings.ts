import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { clipPlansApi } from "@/api/clipPlans";
import { qk } from "@/api/keys";
import { type Aspect, type CoverSettings, type FreezeFrame, type MixSettings, type MontageInsert, type RenderSettings, type SourceDetail, type TransitionSettings, type ClipPlan } from "@/api/types";
import { type SubtitleProfile } from "@/api/assets";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { layersFormat, type CameraKey, type Layer, clipFormat } from "@/motion";
import { DEFAULT_LAYOUT, normalizeLayout, type StreamLayout } from "@/pages/workspace/layout";
import { DEFAULT_BACKDROP, normalizeBackdrop, type Backdrop } from "@/pages/workspace/chromaKey";
import { type JumpCut } from "@/api/cuts";
import { type MuteSpan } from "@/api/mute";
import { type GainSpan } from "@/api/gain";
import { compactAudio, type AudioEdit } from "@/api/audioEdit";
import { useClipRevision } from "@/pages/workspace/preview/MontagePlayer";
import { type Selection } from "@/pages/workspace/ClipTimeline";
import { DEFAULT_COVER, DEFAULT_TRANSITION } from "@/pages/workspace/editor/editorHelpers";

/**
 * Настройки клипа на панели редактора и их автосохранение: загрузка настроек клипа в панель, отложенная запись
 * снимка, запись при уходе и правка мимо этой вкладки (агент, другая вкладка). Порядок эффектов здесь важен:
 * подбор положения субтитров по стилю смотрит на флаг загрузки, который снимает эффект автосохранения.
 */
export function useClipPanelSettings({
  sourceId, query, plans, subs, view, editorPlanId, layerDragRef, setTlSelected,
}: {
  sourceId: string;
  query: UseQueryResult<SourceDetail>;
  plans: ClipPlan[];
  subs: UseQueryResult<SubtitleProfile[]>;
  view: "triage" | "editor";
  editorPlanId: number | null;
  layerDragRef: { current: unknown };
  setTlSelected: React.Dispatch<React.SetStateAction<Selection>>;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  // render options
  const [presetId, setPresetId] = useState(0);
  const [subsOn, setSubsOn] = useState(false);
  const [subId, setSubId] = useState(0);
  const [subEngine, setSubEngine] = useState(""); // "" = из стиля, иначе whisper/gemini
  const [bannerOn, setBannerOn] = useState(false);
  const [bannerId, setBannerId] = useState(0);
  const [musicOn, setMusicOn] = useState(false);
  const [trackId, setTrackId] = useState(0);
  // С какой секунды трека музыка играет на 0 с клипа (сразу с барабанов, без вступления).
  const [musicStartSec, setMusicStartSec] = useState(0);
  const [mirror, setMirror] = useState(false);
  // How the pieces of a multi-piece clip are joined, and the sound on the join.
  const [transition, setTransition] = useState<TransitionSettings>(DEFAULT_TRANSITION);
  const patchTransition = (next: Partial<TransitionSettings>) => setTransition((t) => ({ ...t, ...next }));
  // Cover of the clip (see app/cover.py): a frame of the clip or an uploaded picture.
  const [cover, setCover] = useState<CoverSettings>(DEFAULT_COVER);
  // «Файлы для монтажа» placed in this clip (clip-timeline seconds).
  const [inserts, setInserts] = useState<MontageInsert[]>([]);
  // Моушн-слои и камера клипа — часть его настроек рендера (как вставки).
  const [layers, setLayers] = useState<Layer[]>([]);
  const [camera, setCamera] = useState<CameraKey[]>([]);
  const [layout, setLayout] = useState<StreamLayout>(DEFAULT_LAYOUT);
  const [cuts, setCuts] = useState<JumpCut[]>([]);
  const [backdrop, setBackdrop] = useState<Backdrop>(DEFAULT_BACKDROP);
  // 🔇 Тишина вместо звука исходника (app/audio_mute.py): полосы на дорожке «Звук»
  // таймлайна; агент ставит их же инструментом mute_audio.
  const [mute, setMute] = useState<MuteSpan[]>([]);
  // Стоп-кадры посреди ролика ставит агент (freeze_frame): панель их не правит, но сохраняет как есть.
  const [freezes, setFreezes] = useState<FreezeFrame[]>([]);
  const cutsRef = useRef<JumpCut[]>([]);
  cutsRef.current = cuts;
  const muteRef = useRef<MuteSpan[]>([]);
  muteRef.current = mute;
  // 🔊 Громкость звука исходника на отрезках (render.gain): поднять тихий кусок, приглушить
  // громкий; агент ставит её инструментом set_source_gain.
  const [gain, setGain] = useState<GainSpan[]>([]);
  const gainRef = useRef<GainSpan[]>([]);
  gainRef.current = gain;
  // 🎚 Звуковой монтаж (render.audio, app/audio_edit.py): J/L на стыках тянут на дорожке «Звук»,
  // ключи громкости, ducking, провалы музыки и эффекты ставит агент — панель их показывает и хранит.
  const [audioEdit, setAudioEdit] = useState<AudioEdit>({});
  // 🎚 Сведение (render_settings.mix: auto_mix / set_mix / эффекты голоса и музыки) — целиком, как пришло.
  const [mixSettings, setMixSettings] = useState<MixSettings>({});
  const audioEditRef = useRef<AudioEdit>({});
  audioEditRef.current = audioEdit;
  // Формат вывода клипа (app/formats.py): 9:16, 1:1, 4:5 или 16:9 из того же монтажа.
  // Превью, холст слоёв и окно кропа — в пропорциях этого формата.
  const [aspect, setAspect] = useState<Aspect>("9:16");
  // В каком формате размечены слои (render.layers_format, их опорный кадр). Не совпадает с
  // форматом вывода — превью показывает перекомпоновку, как сделает рендер (motion/reframe.ts).
  const [layersFmt, setLayersFmt] = useState<Aspect>("9:16");
  // Стоп-кадр в конце готового ролика, с (render.end_hold_sec): null — как у сервиса, 0 — мем без «замирания».
  const [endHoldSec, setEndHoldSec] = useState<number | null>(null);
  // Motion blur клипа по умолчанию (render.motion_blur), затвор 0..1: null — выключен.
  const [motionBlur, setMotionBlur] = useState<number | null>(null);
  const patchCover = (next: Partial<CoverSettings>) => setCover((c) => ({ ...c, ...next }));
  // True while a clip's saved settings are being poured into the panel, so the
  // panel's own effects (autosave, subtitle position) don't react to it.
  const loadingSettingsRef = useRef(false);
  const [settingsNonce, setSettingsNonce] = useState(0);
  // Safe-zone overlay on the preview: where the banner sits (top) and where the
  // subtitles land (bottom). Percentages of the final 9:16 frame height.
  const [showZones, setShowZones] = useState(true);
  const [bannerHeightPct, setBannerHeightPct] = useState(14);
  const [bannerPosPct, setBannerPosPct] = useState(4); // верх баннера, % сверху
  const [subPosPct, setSubPosPct] = useState(12);

  // When a subtitle style is picked, derive its vertical position from margin_v
  // (ASS units on a 1920-tall frame) so the band matches the real output.
  useEffect(() => {
    // A clip's saved settings carry their own position — loading them must not
    // be overridden by the style's default right after.
    if (loadingSettingsRef.current) return;
    const p = subs.data?.find((s) => s.id === subId);
    if (p && typeof p.margin_v === "number") {
      setSubPosPct(Math.round(Math.min(40, Math.max(2, (p.margin_v / 1920) * 100))));
    }
  }, [subId, subs.data]);

  // ---- per-clip settings: each clip is set up on its own -------------------
  const transitionOpts = useQuery({
    queryKey: ["transition-options"],
    queryFn: clipPlansApi.transitionOptions,
    staleTime: Infinity,
  });
  const panelSettings = (): RenderSettings => ({
    preset_id: presetId || null,
    subs_on: subsOn,
    sub_id: subId || null,
    sub_engine: subEngine,
    sub_pos_pct: subPosPct,
    banner_on: bannerOn,
    banner_id: bannerId || null,
    banner_height_pct: bannerHeightPct,
    banner_pos_pct: bannerPosPct,
    music_on: musicOn,
    track_id: trackId || null,
    music_start_sec: musicStartSec,
    mirror,
    transition,
    cover,
    inserts,
    layers,
    camera,
    layout,
    cuts,
    backdrop,
    mute,
    gain,
    audio: audioEdit,
    format: aspect,
    layers_format: layersFmt,
    end_hold_sec: endHoldSec,
    motion_blur: motionBlur,
    freezes,
    mix: mixSettings,
  });
  const settingsKey = JSON.stringify(panelSettings());

  const lastJlKeyRef = useRef<Record<number, string>>({});
  // Что сервер последним принял от этой вкладки: правка извне (агент, другая вкладка) — всё остальное.
  const lastServerSettingsRef = useRef<Record<number, string>>({});
  const saveInFlightRef = useRef(0);
  const saveSettings = (planId: number, settings: RenderSettings) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    if (prev) {
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        // Концовку ставит агент (set_end_screen) — панель её не шлёт, сервер её хранит; в кэше тоже держим.
        clip_plans: prev.clip_plans.map((p) =>
          p.id === planId ? { ...p, render_settings: { end_screen: p.render_settings?.end_screen, ...settings } } : p,
        ),
      });
    }
    const jlKey = JSON.stringify(
      ((settings as { audio?: { joins?: unknown } }).audio?.joins ?? null),
    );
    saveInFlightRef.current += 1;
    return clipPlansApi
      .setRenderSettings(planId, settings)
      .then((r) => {
        lastServerSettingsRef.current[planId] = JSON.stringify(r.render_settings);
        // J/L поменялись — субтитры превью (heard_words) идут за звуком: перечитать.
        if (jlKey !== lastJlKeyRef.current[planId]) {
          lastJlKeyRef.current[planId] = jlKey;
          void qc.invalidateQueries({ queryKey: ["plan-subtitles", planId] });
        }
        return r;
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить настройки клипа"))
      .finally(() => {
        saveInFlightRef.current -= 1;
      });
  };

  // Autosave with a snapshot: the timer must save what was on the panel FOR THAT
  // clip, not whatever the panel shows when it fires (you may have switched clips).
  const pendingSaveRef = useRef<{ planId: number; settings: RenderSettings; timer: number } | null>(null);
  // Пока ИИ-ассистент правит клип на сервере, панель не сохраняется: иначе
  // автосохранение затёрло бы его правки старым состоянием редактора.
  const agentRunningRef = useRef(false);
  const flushSettings = async () => {
    const pending = pendingSaveRef.current;
    if (!pending) return;
    pendingSaveRef.current = null;
    window.clearTimeout(pending.timer);
    await saveSettings(pending.planId, pending.settings);
  };

  // Switching clips: save the previous clip's pending edits, then load this
  // clip's own settings. A clip never set up keeps the panel as its start.
  useEffect(() => {
    void flushSettings();
    if (!editorPlanId) return;
    const s = plans.find((p) => p.id === editorPlanId)?.render_settings;
    if (s) lastServerSettingsRef.current[editorPlanId] = JSON.stringify(s);
    if (!s) {
      // Ручки панели остаются стартом для нового клипа, но то, что лежит на
      // таймлайне прошлого клипа (слои, вставки), сюда не переезжает.
      setLayers([]);
      setCamera([]);
      setInserts([]);
      setCuts([]);
      setMute([]);
      setGain([]);
      setAudioEdit({});
      setMixSettings({});
      setFreezes([]);
      // Обложка — тоже своя у клипа: титр чужой обложки не должен вшиться сюда.
      setCover(DEFAULT_COVER);
      setTlSelected(null);
      return;
    }
    // Same key order as panelSettings(): if the clip's settings already equal the
    // panel, nothing changes, the autosave effect never runs — and a raised
    // "loading" flag would then swallow the user's NEXT real edit.
    const loaded: RenderSettings = {
      preset_id: s.preset_id ?? null,
      subs_on: s.subs_on,
      sub_id: s.sub_id ?? null,
      sub_engine: s.sub_engine ?? "",
      sub_pos_pct: s.sub_pos_pct,
      banner_on: s.banner_on,
      banner_id: s.banner_id ?? null,
      banner_height_pct: s.banner_height_pct,
      banner_pos_pct: s.banner_pos_pct,
      music_on: s.music_on,
      track_id: s.track_id ?? null,
      music_start_sec: s.music_start_sec ?? 0,
      mirror: s.mirror,
      transition: { ...DEFAULT_TRANSITION, ...(s.transition ?? {}) },
      cover: { ...DEFAULT_COVER, ...(s.cover ?? {}) },
      inserts: s.inserts ?? [],
      layers: s.layers ?? [],
      camera: s.camera ?? [],
      layout: normalizeLayout(s.layout),
      cuts: s.cuts ?? [],
      backdrop: normalizeBackdrop(s.backdrop),
      mute: s.mute ?? [],
      gain: s.gain ?? [],
      audio: compactAudio(s.audio),
      format: clipFormat(s),
      layers_format: layersFormat(s),
      end_hold_sec: s.end_hold_sec ?? null,
      motion_blur: s.motion_blur ?? null,
      freezes: s.freezes ?? [],
      mix: s.mix ?? {},
    };
    if (JSON.stringify(loaded) === settingsKey) return;
    loadingSettingsRef.current = true;
    setPresetId(s.preset_id ?? 0);
    setSubsOn(s.subs_on);
    setSubId(s.sub_id ?? 0);
    setSubEngine(s.sub_engine ?? "");
    setSubPosPct(s.sub_pos_pct);
    setBannerOn(s.banner_on);
    setBannerId(s.banner_id ?? 0);
    setBannerHeightPct(s.banner_height_pct);
    setBannerPosPct(s.banner_pos_pct);
    setMusicOn(s.music_on);
    setTrackId(s.track_id ?? 0);
    setMusicStartSec(s.music_start_sec ?? 0);
    setMirror(s.mirror);
    setTransition({ ...DEFAULT_TRANSITION, ...(s.transition ?? {}) });
    setCover({ ...DEFAULT_COVER, ...(s.cover ?? {}) });
    setInserts(s.inserts ?? []);
    setLayers(s.layers ?? []);
    setCamera(s.camera ?? []);
    setLayout(normalizeLayout(s.layout));
    setCuts(s.cuts ?? []);
    setBackdrop(normalizeBackdrop(s.backdrop));
    setMute(s.mute ?? []);
    setFreezes(s.freezes ?? []);
    setGain(s.gain ?? []);
    setAudioEdit(compactAudio(s.audio));
    setMixSettings(s.mix ?? {});
    setAspect(clipFormat(s));
    setLayersFmt(layersFormat(s));
    setEndHoldSec(s.end_hold_sec ?? null);
    setMotionBlur(s.motion_blur ?? null);
    // Выделение — номер куска/строки/вставки: в другом клипе (или после отката)
    // он указывал бы на чужой блок, и Delete удалил бы не то.
    setTlSelected(null);
  }, [editorPlanId, query.isSuccess, settingsNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (loadingSettingsRef.current) {
      loadingSettingsRef.current = false;
      return;
    }
    if (!editorPlanId || agentRunningRef.current) return;
    const settings = panelSettings();
    if (pendingSaveRef.current) window.clearTimeout(pendingSaveRef.current.timer);
    const timer = window.setTimeout(() => void flushSettings(), 600);
    pendingSaveRef.current = { planId: editorPlanId, settings, timer };
  }, [settingsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Правка мимо этой вкладки (MCP-агент, другая вкладка): новые настройки, куски и субтитры — сами,
  // без перезагрузки. Свою запись узнаём по ответу сервера: она ничего в панели не сбрасывает.
  useClipRevision(editorPlanId, async () => {
    const planId = editorPlanId;
    if (!planId || pendingSaveRef.current || saveInFlightRef.current || agentRunningRef.current || layerDragRef.current) return;
    await Promise.all([
      qc.invalidateQueries({ queryKey: qk.source(sourceId) }),
      qc.invalidateQueries({ queryKey: ["plan-subtitles", planId] }),
    ]);
    const fresh = qc.getQueryData<SourceDetail>(qk.source(sourceId))?.clip_plans.find((p) => p.id === planId);
    const json = JSON.stringify(fresh?.render_settings);
    if (json !== lastServerSettingsRef.current[planId] && !pendingSaveRef.current) {
      lastServerSettingsRef.current[planId] = json;
      setSettingsNonce((n) => n + 1);
    }
  }, view === "editor");

  const applyToFavorites = async () => {
    const targets = plans.filter((p) => p.favorite && p.id !== editorPlanId);
    if (!targets.length) {
      toast.error("Нет других избранных клипов");
      return;
    }
    const settings = panelSettings();
    // Inserts are placed on each clip's own timeline — never copy them across.
    await Promise.all(
      targets.map((p) =>
        saveSettings(p.id, {
          ...settings,
          inserts: p.render_settings?.inserts ?? [],
          layers: p.render_settings?.layers ?? [],
          camera: p.render_settings?.camera ?? [],
          // Вырезы — диапазоны времени ЭТОГО клипа, обложка-картинка — его кадр: не копируем.
          cuts: p.render_settings?.cuts ?? [],
          mute: p.render_settings?.mute ?? [],
          gain: p.render_settings?.gain ?? [],
          freezes: p.render_settings?.freezes ?? [],
          cover: settings.cover.mode === "image" ? (p.render_settings?.cover ?? DEFAULT_COVER) : settings.cover,
        }),
      ),
    );
    toast.success(`Настройки скопированы в избранные: ${targets.length}`);
  };

  return {
    cutsRef, muteRef, gainRef, audioEditRef, flushSettings, presetId, subsOn, subId, subEngine, bannerOn, bannerId,
    musicOn, setMusicOn, trackId, setTrackId, musicStartSec, setMusicStartSec, mirror, transition, cover, layers,
    camera, mute, aspect, layersFmt, setSettingsNonce, bannerHeightPct, bannerPosPct, subPosPct, patchTransition,
    patchCover, transitionOpts, agentRunningRef, applyToFavorites, setPresetId, setSubsOn, setSubId, setSubEngine,
    setBannerOn, setBannerId, setMirror, inserts, setInserts, setLayers, setCamera, layout, setLayout, cuts, setCuts,
    backdrop, setBackdrop, setMute, gain, setGain, audioEdit, setAudioEdit, mixSettings, setMixSettings, setAspect,
    setLayersFmt, endHoldSec, setEndHoldSec, motionBlur, setMotionBlur, showZones, setShowZones, setBannerHeightPct,
    setBannerPosPct, setSubPosPct,
  };
}
