import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { sourcesApi } from "@/api/sources";
import { clipPlansApi } from "@/api/clipPlans";
import { segmentsApi } from "@/api/segments";
import { audioTracksApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { brandsApi } from "@/api/brands";
import { type AiSegment, type ColorFix, type FocusPoint, type SourceDetail, type SpeedAudio, type TransitionSettings, type ClipPlan } from "@/api/types";
import { audioMixApi } from "@/api/audioMix";
import { api, ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { frameSize } from "@/motion";
import { CENSOR_STYLES, type CensorStyle } from "@/api/mute";
import { clipTimeOf, type SpecPiece } from "@/pages/workspace/editor/editorHelpers";

/**
 * Действия над клипом, что идут через сервер: автомонтаж, обложка, сведение, мат, бренд, откат, скорость и цвет кусков,
 * рендер, границы и фокус, звезда и скрытие. Перед запросом каждое сначала дописывает правки панели (flushSettings),
 * а после ответа перечитывает клип в панель (settingsNonce). Своих эффектов здесь нет.
 */
export function useClipActions({
  sourceId, videoRef, openEditor, activePlan, useVlmFocus, flushSettings, presetId, subsOn, subId, subEngine,
  bannerOn, bannerId, musicOn, setMusicOn, trackId, setTrackId, setMusicStartSec, mirror, transition,
  setSettingsNonce, bannerHeightPct, bannerPosPct, subPosPct, selected,
}: {
  sourceId: string;
  videoRef: React.RefObject<HTMLVideoElement>;
  openEditor: (planId?: number) => void;
  activePlan: ClipPlan | undefined;
  useVlmFocus: boolean;
  flushSettings: () => Promise<void>;
  presetId: number;
  subsOn: boolean;
  subId: number;
  subEngine: string;
  bannerOn: boolean;
  bannerId: number;
  musicOn: boolean;
  setMusicOn: React.Dispatch<React.SetStateAction<boolean>>;
  trackId: number;
  setTrackId: React.Dispatch<React.SetStateAction<number>>;
  setMusicStartSec: React.Dispatch<React.SetStateAction<number>>;
  mirror: boolean;
  transition: TransitionSettings;
  setSettingsNonce: React.Dispatch<React.SetStateAction<number>>;
  bannerHeightPct: number;
  bannerPosPct: number;
  subPosPct: number;
  selected: Set<number>;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  // Музыка по ссылке (POST /api/audio-tracks/from-url): сервер качает звук через yt-dlp.
  const [musicUrl, setMusicUrl] = useState("");
  const musicFromUrl = useMutation({
    mutationFn: () => audioTracksApi.fromUrl(musicUrl.trim()),
    onSuccess: async (track) => {
      await qc.invalidateQueries({ queryKey: qk.audioTracks });
      setMusicOn(true);
      setTrackId(track.id);
      setMusicStartSec(0);
      setMusicUrl("");
      toast.success(`Трек «${track.label}» добавлен и выбран`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось скачать трек"),
  });
  // 🖼 Обложка с заголовком: кадр под курсором + крупный титр (app/cover_compose.py).
  const [coverTitle, setCoverTitle] = useState("");
  const [coverStyle, setCoverStyle] = useState("yellow");
  const composeCover = useMutation({
    mutationFn: async () => {
      const plan = activePlan;
      if (!plan) throw new ApiError(400, "клип не открыт");
      await flushSettings();
      const segs = plan.segments;
      const t = clipTimeOf(segs, videoRef.current?.currentTime ?? 0) ?? 0;
      return api.post<{ url: string }>(`/api/clip-plans/${plan.id}/cover/compose`, {
        t: Number(t.toFixed(2)),
        title: coverTitle.trim() || plan.title || "СМОТРИ",
        style: coverStyle,
      });
    },
    onSuccess: async () => {
      toast.success("Обложка готова и включена — вшита в первые кадры");
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Обложка не получилась"),
  });
  // 🧩 Компиляция избранных в один клип «ТОП-N» — открывается сразу в монтаже.
  const compile = useMutation({
    mutationFn: (ids: number[]) =>
      api.post<{ clip_plan_id: number; clips: number; skipped: number[]; duration: number }>(
        `/api/sources/${sourceId}/compilation`,
        { plan_ids: ids, countdown: true },
      ),
    onSuccess: async (r) => {
      toast.success(
        `Компиляция из ${r.clips} клипов, ${Math.round(r.duration)} с` +
          (r.skipped.length ? ` · не влезло ${r.skipped.length} (лимит 12 кусков)` : ""),
      );
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      openEditor(r.clip_plan_id);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Компиляция не собралась"),
  });
  // ⚡ Автомонтаж всех избранных подряд: речь распознаётся, где её ещё нет.
  const [autoProgress, setAutoProgress] = useState("");
  const batchAuto = useMutation({
    mutationFn: async (ids: number[]) => {
      await flushSettings();
      let done = 0;
      const failed: string[] = [];
      for (const id of ids) {
        setAutoProgress(`${done + 1}/${ids.length}…`);
        try {
          const subs = await clipPlansApi.getSubtitles(id);
          if (!subs?.words?.length) await clipPlansApi.generateSubtitles(id, "whisper");
          await api.post(`/api/clip-plans/${id}/auto-edit`, { keep_layers: true });
          done += 1;
        } catch (e) {
          failed.push(`#${id}: ${e instanceof ApiError ? e.message : "ошибка"}`);
        }
      }
      return { done, failed };
    },
    onSuccess: async ({ done, failed }) => {
      setAutoProgress("");
      if (failed.length) toast.error(`Автомонтаж: готово ${done}, не вышло ${failed.length} — ${failed.slice(0, 2).join("; ")}`);
      else toast.success(`Автомонтаж готов у ${done} клипов — открой любой в монтаже, Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
  });
  // ⚡ Автомонтаж без ИИ — правила по словам и громкости (app/auto_edit.py).
  const autoEdit = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return api.post<{ layers: number; camera: number }>(`/api/clip-plans/${clipPlanId}/auto-edit`, {
        keep_layers: true,
      });
    },
    onSuccess: async (r) => {
      toast.success(`Автомонтаж: ${r.layers} слоёв, ${r.camera ? "наезды по речи" : "без наездов"} — Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Автомонтаж не удался"),
  });
  // 🎚 Сведение звука (app/audio_mix.py): замер по стемам и автосведение.
  const mixReport = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return audioMixApi.report(clipPlanId);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Сведение не измерено"),
  });
  const autoMix = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return audioMixApi.autoMix(clipPlanId);
    },
    onSuccess: async (r) => {
      const a = r.before.voice_vs_music_db;
      const b = r.after.voice_vs_music_db;
      toast.success(
        a != null && b != null
          ? `Свёл: голос над музыкой ${a.toFixed(0)} → ${b.toFixed(0)} дБ — перерендерь; Ctrl+Z откатит`
          : "Свёл звук — перерендерь; Ctrl+Z откатит",
      );
      mixReport.reset();
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Автосведение не вышло"),
  });
  // 🤬 Мат (app/censor.py): середина слова глушится (стиль — тишина, писк, шум…) + звёздочки в
  // субтитрах, без рендера. Проверку рендером и Whisper (verify) зовут агенты — это минуты.
  // Стиль "" — прежний стиль клипа (хранится у отрезков), у нового клипа — по умолчанию сервера.
  const [censorStyle, setCensorStyle] = useState<CensorStyle | "">("");
  const censorMat = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return api.post<{
        words: { i: number; word: string; level: number; style?: CensorStyle }[];
        stars: number;
        manual_kept: number;
      }>(`/api/clip-plans/${clipPlanId}/censor`, { verify: false, ...(censorStyle ? { style: censorStyle } : {}) });
    },
    onSuccess: async (r) => {
      const style = r.words[0]?.style;
      if (!r.words.length) toast.success("Мата в субтитрах не найдено");
      else
        toast.success(
          `Мат: ${r.words.length} сл. заглушено серединой${style ? ` (${CENSOR_STYLES[style]})` : ""}` +
            (r.stars ? `, в субтитрах звёздочки (${r.stars})` : "") +
            (r.manual_kept ? ` · ручные отрезки целы (${r.manual_kept})` : "") +
            " — Ctrl+Z откатит",
        );
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      await qc.invalidateQueries({ queryKey: ["plan-subtitles"] });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Мат не заглушён"),
  });
  // Бренд заказчика (app/brand_kit.py): субтитры и хук в его цветах, логотип, призыв в конце.
  const brands = useQuery({ queryKey: qk.brands, queryFn: brandsApi.list, staleTime: 60_000 });
  const [brandPick, setBrandPick] = useState(0);
  const applyBrand = useMutation({
    mutationFn: async ({ clipPlanId, brandId }: { clipPlanId: number; brandId: number }) => {
      await flushSettings();
      return brandsApi.apply(clipPlanId, brandId);
    },
    onSuccess: async (r) => {
      const note = r.warnings.length ? ` · ${r.warnings[0]}` : "";
      toast.success(`Бренд «${r.brand.name}»: ${r.changes.join(", ") || "без изменений"}${note} — Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Бренд не применился"),
  });
  const undoEdit = useMutation({
    mutationFn: async (clipPlanId: number) => {
      // Незаписанные правки панели сначала уезжают на сервер: иначе автосохранение
      // сработает после отмены и вернёт то, что мы только что откатили.
      await flushSettings();
      return clipPlansApi.undo(clipPlanId);
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
      toast.success(`Шаг назад: ${r.label}${r.left ? ` · осталось ${r.left}` : ""}`);
    },
    onError: (e) =>
      e instanceof ApiError && e.status === 404
        ? toast.push("Отменять нечего", "info")
        : toast.error(e instanceof ApiError ? e.message : "Не удалось отменить"),
  });

  // ⏩ Скорость/реверс куска — тем же инструментом, что у агента (set_segment_speed): сервер
  // сдвигает слои, камеру и субтитры за новой длиной куска, поэтому панель перечитываем.
  const segmentSpeed = useMutation({
    mutationFn: async (body: { planId: number; piece: number; speed: number; reverse: boolean; audio?: SpeedAudio }) => {
      await flushSettings();
      const { planId, ...rest } = body;
      return clipPlansApi.setSegmentSpeed(planId, rest);
    },
    onSuccess: async (r, body) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      await qc.invalidateQueries({ queryKey: ["plan-subtitles", body.planId] });
      setSettingsNonce((n) => n + 1);
      const what = r.reverse ? `реверс${r.speed !== 1 ? ` ×${r.speed}` : ""}` : r.speed !== 1 ? `×${r.speed}` : "как в видео";
      toast.success(`Кусок ${r.piece + 1}: ${what} · ${r.length.toFixed(1)} с`);
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось поменять скорость куска"),
  });

  // ♪ К долям (snap_to_beats): сервер двигает слои/ключи/камеру/склейки — настройки перечитываем.
  const [snapMode, setSnapMode] = useState<"beat" | "downbeat" | "joins">("beat");
  const snapBeats = useMutation({
    mutationFn: async (body: { planId: number; mode: "beat" | "downbeat" | "joins" }) => {
      await flushSettings();
      return clipPlansApi.snapBeats(body.planId, {
        targets: body.mode === "joins" ? ["all"] : ["layers", "keys", "camera"],
        grid: body.mode === "downbeat" ? "downbeat" : "beat",
      });
    },
    onSuccess: async (r, body) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      await qc.invalidateQueries({ queryKey: ["plan-subtitles", body.planId] });
      setSettingsNonce((n) => n + 1);
      const bpm = r.grid.bpm ? ` · ${Math.round(r.grid.bpm)} BPM` : "";
      if (r.moved) toast.success(`К долям: сдвинуто ${r.moved}${bpm}`);
      else toast.push(r.note, "info");
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось привязать к долям"),
  });

  // 🎨 Цвет куска и подгонка кусков — инструментами агента (set_piece_color / match_piece_colors).
  const pieceColor = useMutation({
    mutationFn: async (body: { planId: number; piece: number; reset?: boolean } & ColorFix) => {
      await flushSettings();
      const { planId, ...rest } = body;
      return clipPlansApi.setPieceColor(planId, rest);
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось поменять цвет куска"),
  });
  const matchColors = useMutation({
    mutationFn: async (body: { planId: number; reference: number }) => {
      await flushSettings();
      return clipPlansApi.matchPieceColors(body.planId, { reference: body.reference });
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      const gaps = r.pieces.map((p) => `${p.piece + 1}: ${p.luma_gap_before}→${p.luma_gap_after}`).join(", ");
      toast.success(`Цвет кусков подогнан к куску ${r.reference + 1} (разница яркости ${gaps})`);
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось подогнать цвет"),
  });

  const batch = useMutation({
    // No ids = everything ticked «в рендер»; the editor passes just its clip.
    mutationFn: async (ids: number[] | undefined) => {
      // Unsaved edits of the open clip go in before the render reads them.
      await flushSettings();
      return clipPlansApi.renderBatch(sourceId, {
        clip_plan_ids: ids ?? [...selected],
        ffmpeg_preset_id: presetId || undefined,
        subtitle_profile_id: subsOn ? subId || undefined : undefined,
        subtitle_provider: subsOn ? subEngine || undefined : undefined,
        // Запасной отступ для клипов без своих настроек (они 9:16); настроенные берут свой — от кадра своего формата.
        subtitle_margin_v: subsOn ? Math.round((subPosPct / 100) * frameSize("9:16").h) : undefined,
        banner_id: bannerOn ? bannerId || undefined : undefined,
        banner_height_frac: bannerOn ? bannerHeightPct / 100 : undefined,
        banner_y_frac: bannerOn ? bannerPosPct / 100 : undefined,
        mirror: mirror || undefined,
        music_track_id: musicOn ? trackId || undefined : undefined,
        // Fallback for clips never set up in the editor; set-up clips use their own.
        transition,
      });
    },
    onSuccess: (clips) => {
      toast.success(`Рендер запущен: ${clips.length} клип(ов)`);
      qc.invalidateQueries({ queryKey: qk.activeTasks });
      qc.invalidateQueries({ queryKey: qk.clips(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить рендер"),
  });

  const focusOptions = useQuery({ queryKey: ["focus-options"], queryFn: sourcesApi.focusOptions, staleTime: Infinity });

  const setFocus = useMutation({
    mutationFn: (body: { focus_preset?: string; focus_strategy?: string }) => sourcesApi.setFocus(sourceId, body),
    onSuccess: () => {
      toast.success("Сохранено. Перезапустите авто-фокус, чтобы пересчитать.");
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });

  const cutStrategies = useQuery({ queryKey: ["cut-strategies"], queryFn: sourcesApi.cutStrategies, staleTime: Infinity });
  const refineCuts = useMutation({
    mutationFn: (strategy: string) => sourcesApi.refineCuts(sourceId, strategy),
    onSuccess: (r) => {
      toast.success(`Границы пересчитаны (${r.updated} сегм.)`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось пересчитать границы"),
  });

  const autofocus = useMutation({
    mutationFn: (segmentIds: number[]) => segmentsApi.autofocus(sourceId, segmentIds, useVlmFocus),
    onSuccess: (res) => {
      toast.success(`Авто-фокус: обновлено ${res.updated} сегм.`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось вычислить фокус"),
  });

  const commit = useMutation({
    mutationFn: ({ id, s, e }: { id: number; s: number; e: number }) => segmentsApi.patchTimecodes(id, s, e),
    onMutate: async ({ id, s, e }) => {
      await qc.cancelQueries({ queryKey: qk.source(sourceId) });
      const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
      if (prev) {
        const patch = <T extends { id: number; start_sec: number; end_sec: number }>(seg: T): T =>
          seg.id === id ? { ...seg, start_sec: s, end_sec: e } : seg;
        qc.setQueryData<SourceDetail>(qk.source(sourceId), {
          ...prev,
          segments: prev.segments.map(patch),
          clip_plans: prev.clip_plans.map((p) => ({ ...p, segments: p.segments.map(patch) })),
        });
      }
      return { prev };
    },
    onError: (err, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.source(sourceId), ctx.prev);
      toast.error(err instanceof ApiError ? err.message : "Не удалось сохранить таймкоды");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.source(sourceId) }),
  });

  // Manual "fixed frame": write a CONSTANT focus track to the plan's segments
  // via the same focus-points system autofocus uses — so it never fights the
  // per-segment keyframe editor (which it used to override). Patch the cache
  // immediately (preview moves live), persist after a short debounce.
  const manualSaveTimer = useRef<number | null>(null);
  const constTrack = (seg: AiSegment, cx: number): FocusPoint[] => [
    { t: 0, x: cx },
    { t: Math.max(0.01, seg.end_sec - seg.start_sec), x: cx },
  ];
  const setFixedFrame = (planId: number, x: number) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    const segs = prev?.clip_plans.find((p) => p.id === planId)?.segments ?? [];
    if (!segs.length) return;
    const cx = Math.min(1, Math.max(0, x));
    const ids = new Set(segs.map((s) => s.id));
    if (prev) {
      const patch = (seg: AiSegment): AiSegment => (ids.has(seg.id) ? { ...seg, focus: constTrack(seg, cx) } : seg);
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        segments: prev.segments.map(patch),
        clip_plans: prev.clip_plans.map((p) => ({ ...p, segments: p.segments.map(patch) })),
      });
    }
    if (manualSaveTimer.current) window.clearTimeout(manualSaveTimer.current);
    manualSaveTimer.current = window.setTimeout(() => {
      segs.forEach((seg) =>
        segmentsApi
          .setFocus(seg.id, constTrack(seg, cx))
          .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить рамку")),
      );
    }, 350);
  };

  // Triage flags: ⭐ favourite / hide a candidate. Optimistic cache patch + save.
  const setPlanFlag = (planId: number, flags: { favorite?: boolean; hidden?: boolean }) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    if (prev) {
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        // ★ в браузере — выбор владельца: сервер отметит то же (app/agent_settings.py).
        clip_plans: prev.clip_plans.map((p) =>
          p.id === planId ? { ...p, ...flags, ...(flags.favorite === undefined ? {} : { owner_picked: flags.favorite }) } : p,
        ),
      });
    }
    clipPlansApi
      .setFlags(planId, flags)
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"));
  };

  // ✂ и 🗑 на таймлайне. Куски живут в клип-файле, поэтому правим его целиком
  // через тот же эндпоинт, что и ручная правка: одна проверка, один снимок для Ctrl+Z.
  const editPieces = useMutation({
    mutationFn: async (make: (pieces: SpecPiece[]) => SpecPiece[]) => {
      if (!activePlan) throw new ApiError(400, "клип не открыт");
      // Несохранённые правки панели — сначала на сервер, иначе перезагрузка их потеряет.
      await flushSettings();
      const spec = (await clipPlansApi.getSpec(activePlan.id)) as Record<string, unknown>;
      const pieces = make(spec.pieces as SpecPiece[]);
      if (!pieces.length) throw new ApiError(400, "в клипе должен остаться хотя бы один кусок");
      return clipPlansApi.putSpec(activePlan.id, { ...spec, pieces });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не вышло изменить куски"),
  });

  return {
    musicFromUrl, composeCover, compile, batchAuto, autoEdit, mixReport, autoMix, censorMat, brands, applyBrand,
    undoEdit, segmentSpeed, snapBeats, pieceColor, matchColors, batch, focusOptions, setFocus, cutStrategies,
    refineCuts, autofocus, commit, setFixedFrame, setPlanFlag, editPieces, musicUrl, setMusicUrl, coverTitle,
    setCoverTitle, coverStyle, setCoverStyle, autoProgress, censorStyle, setCensorStyle, brandPick, setBrandPick,
    snapMode, setSnapMode,
  };
}
