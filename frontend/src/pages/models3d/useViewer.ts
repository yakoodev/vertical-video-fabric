/**
 * Окно просмотра 3D как хук: сцена, загрузка модели с ходом, VRM-риг и цикл кадров. Состояние кнопок — в React,
 * цикл кадров читает его через ref (без перерисовки страницы 60 раз в секунду); время клипа отдаётся ~10 раз в
 * секунду для полосы прокрутки. У клипов «лестница» и «сидит» — реквизит по самой анимации (viewer/props.ts).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { fetchWithProgress, type AnimClip, type Asset3d } from "@/api/assets3d";
import { ClipProp, PROP_BONES, type PropInfo, type PropKind } from "./viewer/props";
import { Stage, type Background, type LightPreset, type ViewPreset } from "./viewer/stage";
import { parseModel, VrmRig, type PoseName } from "./viewer/vrmRig";

export interface ViewerOptions {
  turntable: boolean;
  light: LightPreset;
  background: Background;
  wireframe: boolean;
  expression: string;
  pose: PoseName;
  breathing: boolean;
  autoBlink: boolean;
  playing: boolean;
  speed: number;
  loop: boolean;
  move: boolean;
  /** Показать реквизит клипа (ступени, стул), если он у клипа есть. */
  prop: boolean;
}

export const DEFAULT_OPTIONS: ViewerOptions = {
  turntable: false,
  light: "studio",
  background: "dark",
  wireframe: false,
  expression: "",
  pose: "relaxed",
  breathing: true,
  autoBlink: true,
  playing: true,
  speed: 1,
  loop: true,
  move: false,
  prop: true,
};

export type Phase = "loading" | "parsing" | "ready" | "error";

export function useViewer(asset: Asset3d | undefined, opts: ViewerOptions) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<Stage | null>(null);
  const rigRef = useRef<VrmRig | null>(null);
  const optsRef = useRef(opts);
  const clipRef = useRef<AnimClip | null>(null);
  const timeRef = useRef(0);
  const seekRef = useRef<number | null>(null);
  const propRef = useRef<ClipProp | null>(null);
  const propCache = useRef(new Map<string, ClipProp | null>());
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState("");
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [expressions, setExpressions] = useState<string[]>([]);
  const [clipTime, setClipTime] = useState(0);
  const [clipDuration, setClipDuration] = useState(0);
  const [ended, setEnded] = useState(false);
  const [vrmReady, setVrmReady] = useState(false);
  const [propInfo, setPropInfo] = useState<PropInfo | null>(null);
  optsRef.current = opts;

  // Сцена и модель: заново при смене модели.
  const previewUrl = asset?.status === "ready" ? asset.urls.preview : null;
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !previewUrl) return;
    const stage = new Stage(host);
    stageRef.current = stage;
    rigRef.current = null;
    clipRef.current = null;
    propRef.current = null;
    propCache.current = new Map();
    setPropInfo(null);
    setVrmReady(false);
    setPhase("loading");
    setProgress({ done: 0, total: 0 });
    const abort = new AbortController();
    let raf = 0;
    let alive = true;
    (async () => {
      try {
        const buf = await fetchWithProgress(previewUrl, (done, total) => setProgress({ done, total }), abort.signal);
        if (!alive) return;
        setPhase("parsing");
        const loaded = await parseModel(buf);
        if (!alive) return;
        stage.setModel(loaded.root);
        if (loaded.vrm) {
          const rig = new VrmRig(loaded.vrm);
          rigRef.current = rig;
          setExpressions(rig.expressions);
          rig.update(0, 1 / 60, true);
          setVrmReady(true);
        }
        setPhase("ready");
        // Дев-сборка: ручка для проверок из браузера (камера, риг) — в выкладке её нет.
        if ((import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV) (window as unknown as Record<string, unknown>).__m3d = { stage, rig: rigRef.current, THREE };
      } catch (e) {
        if (!alive) return;
        setPhase("error");
        setError(e instanceof Error && e.name !== "AbortError" ? e.message : "модель не загрузилась");
      }
    })();
    let lastT = performance.now();
    let lastUi = 0;
    let sceneT = 0;
    const prevHips = new THREE.Vector3();
    let hadHips = false;
    let wrapped = false;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const nowT = performance.now();
      const dt = Math.min((nowT - lastT) / 1000, 0.1);
      lastT = nowT;
      const o = optsRef.current;
      const rig = rigRef.current;
      if (rig) {
        const clip = clipRef.current;
        let step = dt;
        if (clip) {
          const dur = rig.duration();
          if (seekRef.current !== null) {
            timeRef.current = seekRef.current;
            seekRef.current = null;
            step = 1 / 60;
          } else if (o.playing) {
            step = dt * o.speed;
            timeRef.current += step;
            if (!o.loop && timeRef.current >= dur) {
              timeRef.current = dur;
              setEnded(true);
            } else if (o.loop && dur > 0 && timeRef.current > dur) {
              timeRef.current %= dur;
              wrapped = true;
            }
          } else {
            step = 0;
          }
          rig.update(timeRef.current, step, o.loop);
          const prop = propRef.current;
          if (prop) {
            prop.update(rig.frameIndex(timeRef.current, o.loop));
            stage.placeProp(rig.propShift(timeRef.current, o.loop), o.prop);
          }
        } else {
          sceneT += dt;
          rig.update(sceneT, dt, true);
        }
        // Персонаж ходит по сцене — камера плавно идёт за тазом (без покачивания шага).
        const hp = rig.hipsPosition();
        if (!hadHips) {
          prevHips.copy(hp);
          hadHips = true;
        } else if (clip && o.move && wrapped) {
          stage.follow(hp.clone().sub(prevHips));
          prevHips.copy(hp);
          wrapped = false;
        } else if (clip && o.move) {
          const next = prevHips.clone().lerp(hp, Math.min(1, dt * 4));
          stage.follow(next.clone().sub(prevHips));
          prevHips.copy(next);
        } else {
          prevHips.copy(hp);
        }
        const now = performance.now();
        if (clip && now - lastUi > 100) {
          lastUi = now;
          setClipTime(timeRef.current);
        }
      }
      stage.frame(!!rig);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      alive = false;
      abort.abort();
      cancelAnimationFrame(raf);
      for (const p of propCache.current.values()) p?.dispose();
      stage.dispose();
      stageRef.current = null;
      rigRef.current = null;
    };
  }, [previewUrl]);

  // Кнопки сцены.
  useEffect(() => stageRef.current?.setTurntable(opts.turntable), [opts.turntable, phase]);
  useEffect(() => stageRef.current?.setLight(opts.light), [opts.light, phase]);
  useEffect(() => stageRef.current?.setBackground(opts.background), [opts.background, phase]);
  useEffect(() => stageRef.current?.setWireframe(opts.wireframe), [opts.wireframe, phase]);
  useEffect(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rig.expression = opts.expression;
    rig.pose = opts.pose;
    rig.breathing = opts.breathing;
    rig.autoBlink = opts.autoBlink;
  }, [opts.expression, opts.pose, opts.breathing, opts.autoBlink, vrmReady]);

  const setClip = useCallback((clip: AnimClip | null, move: boolean, loop = true, propKind: PropKind | null = null) => {
    const rig = rigRef.current;
    const stage = stageRef.current;
    clipRef.current = clip;
    timeRef.current = 0;
    setEnded(false);
    setClipTime(0);
    if (!rig || !stage) return;
    rig.setClip(clip, move, loop);
    setClipDuration(rig.duration());
    // Реквизит меряется по клипу один раз (путь «с перемещением» от режима не зависит).
    let prop: ClipProp | null = null;
    if (clip && propKind) {
      const key = `${propKind}:${clip.id}`;
      if (!propCache.current.has(key)) {
        const tr = rig.trace(PROP_BONES);
        propCache.current.set(key, !tr ? null : propKind === "stairs"
          ? ClipProp.stairs(tr, (b) => rig.restHeight(b), stage.propMat, stage.propEdge)
          : ClipProp.chair(tr, rig.legLength, stage.propMat, stage.propEdge));
      }
      prop = propCache.current.get(key) ?? null;
    }
    propRef.current = prop;
    stage.setProp(prop?.root ?? null);
    setPropInfo(prop?.info ?? null);
    if (!move || !clip) stageRef.current?.reset(true);
  }, []);

  const seek = useCallback((t: number) => {
    seekRef.current = t;
    setClipTime(t);
    setEnded(false);
  }, []);

  const restart = useCallback(() => {
    seekRef.current = 0;
    setEnded(false);
  }, []);

  const view = useCallback((v: ViewPreset) => stageRef.current?.view(v), []);
  const reset = useCallback(() => stageRef.current?.reset(), []);
  const snapshot = useCallback(() => stageRef.current?.snapshot() ?? Promise.resolve(null), []);

  return { hostRef, phase, error, progress, expressions, vrmReady, clipTime, clipDuration, ended, setClip, seek, restart,
    view, reset, snapshot, propInfo };
}
