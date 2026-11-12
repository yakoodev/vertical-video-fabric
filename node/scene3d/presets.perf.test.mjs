// node --test node/scene3d/presets.perf.test.mjs — пресеты игры персонажа (эмоции и жесты): детерминированность,
// плавный вход и выход к прежней позе, лица и клипы существуют, руки героя из примитивов не входят в тело,
// зеркало списков в app/voxel3d.py, ключи VRM отсортированы и «на время» возвращают прежнее.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as THREE from "three";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { EMOTIONS, GESTURES, PERF_FACES, perfPatch } from "./src/presets/performance.ts";
import { FACE_EXPR } from "./src/presets/performance_vrm.ts";
import { expandPresets } from "./src/presets/index.ts";
import { VOXEL_CLIPS } from "./src/voxelclips.ts";
import { VOXEL_LAYERS, layerActions } from "./src/voxelgestures.ts";
import { VOXEL_FACES } from "./src/pixel.ts";
import { voxelRig, posePose, armHits } from "./src/voxelfk.ts";
import { expandHolds, perfBones } from "../vrm/src/perf.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const ALL = { ...EMOTIONS, ...GESTURES };
const NAMES = Object.keys(ALL);

test("эмоции и жесты: все 19, русские название и описание, оба движка", () => {
  assert.deepEqual(Object.keys(EMOTIONS), ["happy", "sad", "angry", "offended", "shocked", "smug", "tired", "thinking", "panic"]);
  assert.deepEqual(Object.keys(GESTURES), ["sigh", "eye_roll", "facepalm", "shrug", "point", "wave", "typing", "nod", "head_shake", "flinch"]);
  for (const [n, d] of Object.entries(ALL)) {
    assert.equal(d.meta.name, n);
    assert.match(d.meta.title, /[а-яё]/i, n);
    assert.ok(d.meta.ru.length > 30, n);
    assert.ok(d.dur > 0.5 && d.dur < 6, n);
    assert.equal(typeof d.voxel, "function", n);
    assert.equal(typeof d.vrm, "function", n);
  }
});

test("одна и та же реплика — та же заплатка (оба движка)", () => {
  for (const n of NAMES) {
    for (const eng of ["voxel", "vrm"]) {
      const cue = { who: "hero", preset: n, t: 1.3, dur: 2.7, intensity: 1.2 };
      assert.deepEqual(perfPatch(cue, eng), perfPatch({ ...cue }, eng), `${n}/${eng}`);
    }
  }
  // Робот мем-пилота: поза и лицо, края окна — нейтральная поза.
  for (const n of NAMES) {
    const bot = perfPatch({ who: "bot", preset: n, t: 1, dur: 2 }, "deskbot");
    assert.ok(bot.pose.length >= 3 && bot.face.length >= 2, n);
    assert.equal(bot.pose[0].t, 1, n);
    assert.equal(bot.pose.at(-1).t, 3, n);
    for (const k of [bot.pose[0], bot.pose.at(-1)]) assert.ok(k.lean === 0 && k.turn === 0 && k.armL === "rest" && k.armR === "rest", n);
    assert.equal(bot.face.at(-1).expr, "neutral", n);
    assert.ok(bot.pose.every((k, i) => i === 0 || k.t >= bot.pose[i - 1].t), n);
  }
});

test("voxel: клипы — слои из VOXEL_CLIPS, лица — из VOXEL_FACES, ключи лица на время внутри окна", () => {
  for (const n of NAMES) {
    for (const dur of [undefined, 1.6, 4.2]) {
      const t = 2;
      const p = perfPatch({ who: "hero", preset: n, t, dur }, "voxel");
      const d = dur ?? ALL[n].dur;
      assert.ok(p.actions.length >= 1, n);
      for (const a of p.actions) {
        assert.ok(VOXEL_CLIPS[a.clip]?.layer, `${n}: ${a.clip}`);
        assert.ok(a.start >= t - 1e-9 && a.end <= t + d + 1e-9, n);
      }
      for (const f of p.faces || []) {
        assert.ok(VOXEL_FACES.includes(f.face), `${n}: лицо ${f.face}`);
        assert.ok(f.dur > 0 && f.t >= t - 1e-9 && f.t + f.dur <= t + d + 1e-6, `${n}: ${JSON.stringify(f)}`);
      }
    }
  }
  for (const f of PERF_FACES) assert.ok(VOXEL_FACES.includes(f), f);
});

const BASES = ["Idle_Loop", "Sit_Loop", "Typing_Loop"];
const quat = (v = [0, 0, 0]) => new THREE.Quaternion().setFromEuler(new THREE.Euler(...v.map((x) => (x * Math.PI) / 180), "XYZ"));
const angle = (a, b) => (quat(a).angleTo(quat(b)) * 180) / Math.PI;
const poseAt = (base, clip, t, len, shape, extra = {}) => {
  const rot = VOXEL_CLIPS[base].pose(t), move = {};
  layerActions([{ clip, start: 0, end: len, ...extra }], t, rot, move, base !== "Idle_Loop", shape);
  return { rot, move };
};

test("voxel: слой в начале и в конце окна — ровно прежняя поза (плавный вход и возврат)", () => {
  for (const [clip, c] of Object.entries(VOXEL_LAYERS)) {
    for (const base of BASES) {
      for (const len of [c.dur, c.dur * 1.7]) {
        for (const t of [0, len]) {
          const ref = VOXEL_CLIPS[base].pose(t);
          const { rot, move } = poseAt(base, clip, t, len, "soft");
          for (const [k, v] of Object.entries(rot)) assert.ok(angle(v, ref[k]) < 0.6, `${clip}/${base} t=${t} ${k}: ${v} ≠ ${ref[k]}`);
          for (const [k, v] of Object.entries(move)) assert.ok(Math.hypot(...v) < 2e-3, `${clip} t=${t} сдвиг ${k}`);
        }
      }
    }
  }
});

test("voxel: внутри окна слой заметен и непрерывен (нет скачков между кадрами)", () => {
  for (const [clip, c] of Object.entries(VOXEL_LAYERS)) {
    let maxStep = 0, maxDev = 0, prev = null;
    for (let i = 0; i <= 120; i++) {
      const t = (c.dur * i) / 120;
      const { rot } = poseAt("Idle_Loop", clip, t, c.dur, "soft");
      const ref = VOXEL_CLIPS.Idle_Loop.pose(t);
      // Угол между поворотами (кватернионы: у углов Эйлера бывают равные по смыслу «перескоки» на 360°).
      for (const k of ["spine_03", "Head", "upperarm_l", "upperarm_r", "lowerarm_r", "hand_r"]) {
        maxDev = Math.max(maxDev, angle(rot[k], ref[k]));
        if (prev) maxStep = Math.max(maxStep, angle(rot[k], prev[k]));
      }
      prev = rot;
    }
    assert.ok(maxDev > 6, `${clip}: почти не двигается (${maxDev.toFixed(1)}°)`);
    // Шаг 1/120 длины (~1/50 с): рывки вздрагивания и «тычка» — быстрые, но без скачка в позу.
    assert.ok(maxStep < 40, `${clip}: скачок ${maxStep.toFixed(1)}° за кадр`);
  }
});

test("voxel: предплечья и кисти не входят в корпус, таз и голову (мягкий чиби строго, кубики — с допуском)", () => {
  const LIM = { soft: 0.02, block: 0.06 };
  for (const shape of ["soft", "block"]) {
    const rig = voxelRig(shape);
    for (const [clip, c] of Object.entries(VOXEL_LAYERS)) {
      for (const base of BASES) {
        for (let i = 0; i <= 40; i++) {
          const t = (c.dur * i) / 40;
          const { rot, move } = poseAt(base, clip, t, c.dur, shape);
          posePose(rig, rot, move);
          for (const h of armHits(rig)) {
            assert.ok(h.depth < LIM[shape], `${shape} ${clip}/${base} t=${t.toFixed(2)}: ${h.part} в «${h.body}» на ${(h.depth * 100).toFixed(1)} см`);
          }
        }
      }
    }
  }
});

test("voxel: стороны и параметры жестов (side, dir, n) меняют позу", () => {
  const r = poseAt("Idle_Loop", "Gest_Wave", 1.0, 2.2, "soft", { side: "right" }).rot;
  const l = poseAt("Idle_Loop", "Gest_Wave", 1.0, 2.2, "soft", { side: "left" }).rot;
  assert.ok(Math.abs(r.upperarm_r[2]) > Math.abs(l.upperarm_r[2]) + 20, "машет правой");
  assert.ok(Math.abs(l.upperarm_l[2]) > Math.abs(r.upperarm_l[2]) + 20, "машет левой");
  const up = poseAt("Idle_Loop", "Gest_Point", 1.0, 2.0, "soft", { dir: "up" }).rot;
  const fw = poseAt("Idle_Loop", "Gest_Point", 1.0, 2.0, "soft", { dir: "forward" }).rot;
  assert.notDeepEqual(up.upperarm_r, fw.upperarm_r);
});

test("зеркало app/voxel3d.py: те же клипы с длительностями и все лица", () => {
  const py = readFileSync(join(ROOT, "app", "voxel3d.py"), "utf8");
  const clips = py.slice(py.indexOf("VOXEL_CLIPS = {"), py.indexOf("}", py.indexOf("VOXEL_CLIPS = {")) + 1);
  const pyClips = Object.fromEntries([...clips.matchAll(/"(\w+)":\s*([\d.]+)/g)].map((m) => [m[1], Number(m[2])]));
  assert.deepEqual(Object.keys(pyClips).sort(), Object.keys(VOXEL_CLIPS).sort());
  for (const [n, c] of Object.entries(VOXEL_CLIPS)) assert.equal(pyClips[n], c.dur, n);
  const faces = py.slice(py.indexOf("VOXEL_FACES = ("), py.indexOf(")", py.indexOf("VOXEL_FACES = (")));
  const pyFaces = [...faces.matchAll(/"(\w+)"/g)].map((m) => m[1]);
  for (const f of VOXEL_FACES) assert.ok(pyFaces.includes(f), `нет лица ${f} в app/voxel3d.py`);
});

const ARM_PRESETS = new Set(["typing", "rest", "keys", "idle", "chin", "wave", "mouse", "mouth", "facepalm", "shrug", "point", "fist", "cheek", "guard", "yawn", "chinfist", "cross", "cross_open", null, undefined]);
const EXPRS = new Set(["happy", "angry", "sad", "relaxed", "surprised", "blink", "aa", "ih", "ou", "ee", "oh"]);

test("vrm: ключи отсортированы, «на время» внутри окна, руки и выражения существуют", () => {
  for (const n of NAMES) {
    const t0 = 3, d = ALL[n].dur;
    const { timeline } = perfPatch({ who: "hero", preset: n, t: t0 }, "vrm");
    assert.ok(timeline && Object.keys(timeline).length, n);
    for (const [k, keys] of Object.entries(timeline)) {
      const ts = keys.map((x) => x.t ?? x.start);
      assert.deepEqual(ts, [...ts].sort((a, b) => a - b), `${n}.${k} не по порядку`);
      for (const x of keys) {
        const s = x.t ?? x.start;
        assert.ok(s >= t0 - 1e-9 && s <= t0 + d, `${n}.${k} начало ${s}`);
        if (x.hold != null) assert.ok(x.hold > 0 && s + x.hold <= t0 + d + 1e-6, `${n}.${k} hold ${x.hold}`);
      }
    }
    for (const a of timeline.arms || []) for (const side of ["left", "right"]) assert.ok(ARM_PRESETS.has(a[side]), `${n}: рука ${a[side]}`);
    for (const f of timeline.faces || []) for (const e of Object.keys(f.expr)) assert.ok(EXPRS.has(e), `${n}: выражение ${e}`);
  }
  for (const f of PERF_FACES) assert.ok(FACE_EXPR[f], `нет смеси выражений для лица ${f}`);
});

test("vrm: ключ на время возвращает прежнее, непоименованная рука остаётся, более поздний ключ не перебивается", () => {
  const arms = [{ t: -3, left: "keys", right: "keys" }, { t: 2, dur: 0.4, right: "wave", hold: 1.5, out: 0.5 }];
  const ex = expandHolds(arms, ["left", "right"], { left: null, right: null });
  assert.deepEqual(ex.map((k) => [k.t, k.left, k.right]), [[-3, "keys", "keys"], [2, "keys", "wave"], [3.5, "keys", "keys"]]);
  assert.equal(ex[2].dur, 0.5);
  // Обычный ключ внутри окна — после окна возврата нет (действует он).
  const ex2 = expandHolds([...arms, { t: 3, left: "rest", right: "rest" }], ["left", "right"], { left: null, right: null });
  assert.deepEqual(ex2.map((k) => k.t), [-3, 2, 3]);
  // Вложенный ключ на время возвращает к внешнему, внешний — к базе.
  const faces = [{ t: 0, expr: { relaxed: 0.3 } }, { t: 1, expr: { sad: 1 }, hold: 3 }, { t: 2, expr: { surprised: 1 }, hold: 0.5 }];
  const ef = expandHolds(faces, ["expr"], { expr: {} });
  assert.deepEqual(ef.map((k) => [k.t, Object.keys(k.expr)[0]]), [[0, "relaxed"], [1, "sad"], [2, "surprised"], [2.5, "sad"], [4, "relaxed"]]);
  // Без ключей на время — тот же массив.
  const one = arms.slice(0, 1);
  assert.equal(expandHolds(one, ["left", "right"], {}), one);
});

test("vrm: слои игры только в своём окне и с нуля до нуля", () => {
  const tl = { shakes: [{ t: 1, n: 3, amp: 14, dur: 1.2 }], shrugs: [{ t: 3, dur: 1.4 }], flinches: [{ t: 5, dur: 1.5, side: 1 }],
    sighs: [{ t: 7, dur: 2 }], postures: [{ t: 10, dur: 2, fade: 0.4, bones: { head: [10, 0, 0] }, tremble: [11, 1] }] };
  const mag = (o) => Object.values(o).reduce((s, v) => s + Math.hypot(...v), 0);
  for (const [a, b] of [[1, 2.2], [3, 4.4], [5, 6.5], [7, 9], [10, 12]]) {
    assert.ok(mag(perfBones(tl, a + 1e-4)) < 0.5, `вход ${a}`);
    assert.ok(mag(perfBones(tl, (a + b) / 2)) > 1, `середина ${a}`);
    assert.ok(mag(perfBones(tl, b - 1e-4)) < 0.5, `выход ${b}`);
  }
  for (const t of [0.5, 2.5, 4.6, 6.6, 9.5, 12.5]) assert.equal(mag(perfBones(tl, t)), 0, `вне окон t=${t}`);
});

test("пресеты на сцене: реплика игры разворачивается в клипы и лица героя (voxel) и ключи timeline (vrm)", () => {
  const spec = { characters: [{ id: "hero", style: "voxel" }, { id: "girl", style: "vrm", timeline: { faces: [{ t: 0, expr: {} }] } }],
    presets: { performance: [{ who: "hero", preset: "facepalm", t: 1 }, { who: "girl", preset: "shrug", t: 2 }] } };
  const out = expandPresets(spec);
  const [hero, girl] = out.characters;
  assert.equal(hero.actions[0].clip, "Gest_Facepalm");
  assert.ok(hero.faces.some((f) => f.face === "wince"));
  assert.ok(girl.timeline.arms.some((a) => a.left === "shrug" && a.hold > 0));
  assert.ok(girl.timeline.shrugs.length === 1 && girl.timeline.faces.length >= 2);
});

test("жесты: wave hand left|right — та же сторона во всех движках (voxel, VRM, робот)", () => {
  const vl = perfPatch({ who: "hero", preset: "wave", t: 1, hand: "left" }, "voxel");
  assert.equal(vl.actions[0].side, "left");
  assert.equal(perfPatch({ who: "hero", preset: "wave", t: 1 }, "voxel").actions[0].side, "right");
  const left = perfPatch({ who: "g", preset: "wave", t: 1, hand: "left" }, "vrm").timeline.arms[0];
  assert.ok(left.left === "wave" && left.right === undefined);
  const right = perfPatch({ who: "g", preset: "wave", t: 1, hand: "right" }, "vrm").timeline.arms[0];
  assert.ok(right.right === "wave" && right.left === undefined);
  assert.equal(perfPatch({ who: "g", preset: "wave", t: 1, side: "left" }, "vrm").timeline.arms[0].left, "wave");
  assert.ok(GESTURES.wave.meta.params.some((p) => p.name === "hand" && p.options.join() === "left,right"));
  const bot = (hand) => JSON.stringify(perfPatch({ who: "b", preset: "wave", t: 1, hand }, "deskbot").pose);
  assert.notEqual(bot("left"), bot("right"));
});

test("обиделась (VRM): руки скрещены по очереди и расплетаются через «врозь», голова отвёрнута", () => {
  const { timeline } = perfPatch({ who: "g", preset: "offended", t: 2 }, "vrm");
  const a = timeline.arms;
  assert.deepEqual(a.map((k) => [k.left ?? "-", k.right ?? "-"]), [["-", "cross"], ["cross", "-"], ["cross_open", "cross_open"]]);
  assert.ok(a[1].t > a[0].t && a[2].t > a[1].t);
  const ex = expandHolds([{ t: -3, left: "keys", right: "keys" }, ...a], ["left", "right"], { left: null, right: null });
  assert.deepEqual(ex.at(-1).left, "keys", "в конце — снова на клавишах");
  assert.ok(ex.at(-1).t + ex.at(-1).dur <= 2 + EMOTIONS.offended.dur + 1e-6);
  assert.ok(!ex.some((k) => k.left === "rest"), "руки не лежат на столе");
  const head = timeline.postures[0].bones.head;
  assert.ok(Math.abs(head[1]) >= 8, "голова отвёрнута");
});