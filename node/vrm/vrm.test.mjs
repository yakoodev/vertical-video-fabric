// node --test node/vrm/vrm.test.mjs — чистая математика движения VRM (без браузера): веса дорожек мокапа,
// зеркало, взгляд с догонянием, пресеты пальцев в пределах суставов.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spans } from "./handspans.mjs";
import { emptyChecks } from "./checks.mjs";
import { FrameError, frameProblem, presenceSummary, renderChunkRetry } from "../scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "vrm-test-"));
const out = join(dir, "m.mjs");
await build({
  stdin: { contents: `export * from "./src/mocap.ts"; export { lookPoint, FINGERS, nodBow, armChain } from "./src/sequence.ts"; export { straightCut, CUT_X, CUT_Y } from "./src/cutcheck.ts"; export { speechMouth, envLevel } from "./src/anim.ts"; export { waveArm } from "./src/typing.ts"; export { crossArm, crossOpenArm, perfArm } from "./src/perfarms.ts"; export { chatLane } from "./src/chatlane.ts"; export { armTwist, twistAbout, TwistCheck, WRIST_TWIST_MAX, TWIST_JUMP_MAX } from "./src/twist.ts"; export * as THREE from "three";`, resolveDir: HERE, loader: "ts" },
  bundle: true, format: "esm", platform: "node", outfile: out, logLevel: "error",
});
const M = await import(pathToFileURL(out).href);
process.on("exit", () => rmSync(dir, { recursive: true, force: true }));

test("вес дорожки: плавный вход и выход, вне окна — 0", () => {
  const tr = { clip: "x", start: 1, end: 3, fadeIn: 0.5, fadeOut: 0.5 };
  assert.equal(M.trackWeight(tr, 0.9), 0);
  assert.equal(M.trackWeight(tr, 2), 1);
  assert.ok(M.trackWeight(tr, 1.25) > 0.4 && M.trackWeight(tr, 1.25) < 0.6);
  assert.equal(M.trackWeight(tr, 3.1), 0);
});

test("зеркало: левая кость берётся у правой, x сохраняется, y и z меняют знак", () => {
  const clip = { fps: 30, frames: 2, bones: { rightUpperArm: [[0.1, 0.2, 0.3, 0.927], [0.1, 0.2, 0.3, 0.927]] }, root: [] };
  const q = M.sampleBone(clip, "leftUpperArm", 0, false, true);
  assert.ok(Math.abs(q.x - 0.1) < 1e-6 && Math.abs(q.y + 0.2) < 1e-6 && Math.abs(q.z + 0.3) < 1e-6);
});

test("смешивание: базовая дорожка + верхняя на половине веса", () => {
  const a = { fps: 30, frames: 1, bones: { head: [[0, 0, 0, 1]] }, root: [] };
  const b = { fps: 30, frames: 1, bones: { head: [[0, Math.sin(Math.PI / 4), 0, Math.cos(Math.PI / 4)]] }, root: [] };
  const pose = M.mocapAt({ a, b }, [
    { clip: "a", start: 0, end: 10, fadeIn: 0, fadeOut: 0, mask: ["head"] },
    { clip: "b", start: 0, end: 10, fadeIn: 0, fadeOut: 0, mask: ["head"], weight: 0.5 },
  ], 5);
  const yaw = 2 * Math.asin(pose.head[1]);
  assert.ok(Math.abs(yaw - Math.PI / 4) < 1e-3, `поворот ${yaw}`);
});

test("взгляд догоняет цель плавно и без перелёта", () => {
  const keys = [{ t: 0, target: [0, 1, 1] }, { t: 1, target: [1, 1, 1] }];
  let prev = -1;
  for (let t = 1; t < 3; t += 0.05) {
    const x = M.lookPoint(keys, t, 0.28)[0];
    assert.ok(x >= prev - 1e-9 && x <= 1 + 1e-9);
    prev = x;
  }
  assert.ok(prev > 0.99);
});

test("пресеты пальцев: сгиб 0..1, каскад — мизинец не прямее указательного", () => {
  for (const [name, f] of Object.entries(M.FINGERS)) {
    for (const c of f.curl) assert.ok(c >= 0 && c <= 1, name);
    assert.ok(f.curl[4] >= f.curl[1] - 1e-9, name);
  }
});

test("кивки и поклон: только в своём окне, поклон доходит до глубины и возвращается", () => {
  const tl = { clips: {}, tracks: [], nods: [{ t: 1, n: 2, amp: 10 }], bows: [{ t: 3, dur: 2, deg: 16 }] };
  assert.equal(M.nodBow(tl, 0.9).head, 0);
  assert.ok(M.nodBow(tl, 1.225).head > 4); // низ первого кивка — середина периода 0.45 с
  assert.equal(M.nodBow(tl, 2.0).head, 0);
  const mid = M.nodBow(tl, 4.0);
  assert.ok(Math.abs(mid.spine + mid.chest + mid.neck + mid.head - 16 * 1.4) < 0.5);
  assert.equal(M.nodBow(tl, 5.01).spine, 0);
});

test("руки: новый ключ посреди перехода не дёргает кисть (цепочка), дальний переход — дугой вперёд", () => {
  const keys = [{ t: -3, left: "idle", right: "idle" }, { t: 1, dur: 0.8, left: "idle", right: "mouth" },
    { t: 2.5, dur: 0.8, left: "idle", right: "idle" }, { t: 2.9, dur: 0.8, left: "keys", right: "keys" }];
  let prev = null, maxStep = 0;
  for (let t = 0.5; t < 4.5; t += 1 / 120) {
    const p = M.armChain(keys, -1, t).target;
    if (prev) maxStep = Math.max(maxStep, Math.hypot(p[0] - prev[0], p[1] - prev[1], p[2] - prev[2]));
    prev = p;
  }
  assert.ok(maxStep < 0.01, `скачок цели ${maxStep.toFixed(4)} м за 1/120 с`);
  // Середина перехода «клавиатура → рот» — впереди прямой между ними (мимо груди).
  const a = M.armChain(keys, -1, 0.9).target, b = M.armChain(keys, -1, 2.4).target, mid = M.armChain(keys, -1, 1.4).target;
  assert.ok(mid[2] > (a[2] + b[2]) / 2 + 0.03, `дуга ${mid[2]}`);
});

test("касание клавиш выключено, пока кисть едет издалека", () => {
  const keys = [{ t: -3, left: "idle", right: "wave" }, { t: 1, dur: 0.8, left: "idle", right: "idle" }];
  assert.equal(!!M.armChain(keys, -1, 1.4).touch, false);
  assert.equal(!!M.armChain(keys, -1, 2.0).touch, true);
});

test("отрезки проникновений рук: подряд идущие кадры одной группы, худший зазор", () => {
  const bad = [{ t: 1, key: "torso", gap: -0.002, point: "a", against: "torso" }, { t: 1 + 1 / 30, key: "torso", gap: -0.005, point: "b", against: "torso" },
    { t: 3, key: "torso", gap: -0.001, point: "c", against: "torso" }, { t: 1, key: "head", gap: -0.0004, point: "d", against: "head" }];
  const s = spans(bad, 30);
  assert.equal(s.length, 3);
  const t = s.find((x) => x.key === "torso" && x.t0 === 1);
  assert.equal(t.gap_mm, -5);
  assert.equal(t.point, "b");
});

test("кадр без героини: потерян контекст GPU, модели нет, сеток нет, холст пуст — проблема; вставка — нет", () => {
  const ok = { luma: 40, presence: { expect: 1, models: 1, meshes: 12, cover: 0.31 }, lost: [] };
  assert.equal(frameProblem(ok), null);
  assert.match(frameProblem({ ...ok, lost: ["webgl 1080×1920"] }), /контекст GPU/);
  assert.match(frameProblem({ ...ok, presence: { ...ok.presence, models: 0 } }), /модели нет/);
  assert.match(frameProblem({ ...ok, presence: { ...ok.presence, meshes: 0 } }), /сеток/);
  assert.match(frameProblem({ ...ok, presence: { ...ok.presence, cover: 0 } }), /холст пуст/);
  assert.equal(frameProblem({ luma: 40, presence: { expect: 0, models: 1, meshes: 12, cover: 0 }, lost: [] }), null);
  assert.equal(frameProblem({ luma: 40 }), null); // движок scene3d присутствие не сообщает
  const sum = presenceSummary([{ presence: [{ frame: 0, expect: 1 }, { frame: 1, expect: 0 }] }, { presence: [{ frame: 2, expect: 1 }] }]);
  assert.deepEqual([sum.frames, sum.expected, sum.missing], [3, 2, 0]);
  assert.equal(presenceSummary([{ luma: [1] }]), null);
});

test("кусок с потерей контекста переснимается один раз; вторая потеря — ошибка", async () => {
  let n = 0;
  const flaky = async () => {
    if (n++ === 0) throw new FrameError("кадр 270: потерян контекст GPU");
    return { luma: [40], presence: [], warnings: [] };
  };
  const r = await renderChunkRetry({}, {}, 30, [0, 10], "seg.mp4", () => {}, flaky);
  assert.equal(n, 2);
  assert.match(r.retried, /контекст GPU/);
  const dead = async () => {
    throw new FrameError("кадр 270: модели нет в сцене");
  };
  await assert.rejects(renderChunkRetry({}, {}, 30, [0, 10], "seg.mp4", () => {}, dead), (e) => e instanceof FrameError && /после пересъёмки/.test(e.message));
  // Другие ошибки (ffmpeg, браузер) не переснимаются.
  let m = 0;
  const other = async () => {
    m++;
    throw new Error("ffmpeg: код 1");
  };
  await assert.rejects(renderChunkRetry({}, {}, 30, [0, 10], "seg.mp4", () => {}, other), /ffmpeg/);
  assert.equal(m, 1);
});

test("пустые проверки касаний — причина, а не «ок»", () => {
  const spec = { timeline: { surfaces: [{ name: "стол" }], chair: { pos: [0, 0.8, 0] } } };
  const good = { frames: 100, min: 0.008, chair: { min: 0.003 }, hair: { min: 0.005 }, hands: { frames: 100, gaps: { torso: { min: 0.02 } } },
    twist: { frames: 100, bad: 0 } };
  assert.deepEqual(emptyChecks(good, spec, 80), []);
  assert.deepEqual(emptyChecks(good, { ...spec, timeline: undefined }, 80), []);
  const empty = { frames: 0, min: Infinity, chair: { min: Infinity }, hair: { min: Infinity }, hands: { frames: 0, gaps: {} } };
  const why = emptyChecks(empty, spec, 80).join("; ");
  for (const w of ["пальцы", "кресло", "волосы", "руки"]) assert.match(why, new RegExp(w));
  assert.match(emptyChecks({ ...good, hands: { frames: 100, gaps: {} } }, spec, 80).join(), /руки: нет ни одного/);
  assert.match(emptyChecks({ ...good, hands: null }, spec, 80).join(), /не запускалась/);
  assert.match(emptyChecks(good, spec, 80, ["кадр 5: модели нет в сцене"]).join(), /модели нет на 1 кадрах/);
});

/** Рука из трёх костей (оси модели, как у нормализованных костей VRM: в покое кость вдоль ±X, ладонь −Y). */
function fakeArm(side, { wrist = 0, forearm = 0, flex = 40 } = {}) {
  const T = M.THREE;
  const up = new T.Object3D(), lo = new T.Object3D(), hand = new T.Object3D();
  up.add(lo);
  lo.add(hand);
  lo.position.set(side * 0.22, 0, 0);
  hand.position.set(side * 0.22, 0, 0);
  const ax = new T.Vector3(side, 0, 0);
  lo.quaternion.setFromAxisAngle(new T.Vector3(0, 1, 0), (side * flex * Math.PI) / 180)
    .multiply(new T.Quaternion().setFromAxisAngle(ax, (forearm * Math.PI) / 180));
  hand.quaternion.setFromAxisAngle(ax, (wrist * Math.PI) / 180);
  up.updateMatrixWorld(true);
  return { up, lo, hand };
}

function fakeRig(left, right, flex = { left: 40, right: 40 }) {
  const arms = { left: fakeArm(1, left), right: fakeArm(-1, right) };
  return {
    restInverse: new M.THREE.Quaternion(),
    info: { left: { flex: flex.left }, right: { flex: flex.right } },
    node: (n) => {
      const side = n.startsWith("left") ? arms.left : arms.right;
      return /UpperArm$/.test(n) ? side.up : /LowerArm$/.test(n) ? side.lo : /Hand$/.test(n) ? side.hand : null;
    },
  };
}

test("скрутка рук: запястье и предплечье по оси кости, ладонь вниз, сгиб не путается со скруткой", () => {
  const r = fakeRig({ wrist: 55 }, { wrist: -57 });
  const a = M.armTwist(r, 1), b = M.armTwist(r, -1);
  assert.ok(Math.abs(a.wrist - 55) < 0.2 && Math.abs(b.wrist + 57) < 0.2, JSON.stringify([a, b]));
  assert.equal(a.forearm, 0);
  // Сгиб локтя (качание вокруг Y) не даёт скрутки предплечья.
  assert.ok(Math.abs(M.armTwist(fakeRig({ wrist: 0 }, {}, undefined), 1).forearm) < 0.2);
  assert.ok(Math.abs(M.armTwist(fakeRig({ forearm: 70 }, {}), 1).forearm - 70) < 0.2);
  assert.ok(a.palm[1] < -0.5); // ладонь (−Y кисти) вниз
});

test("проверка скрутки: штопор, ладонь вверх при печати, рывок и прямая рука — нарушения", () => {
  const ok = new M.TwistCheck();
  for (let i = 0; i < 5; i++) ok.frame(fakeRig({ wrist: 55 + i }, { wrist: -57 }), i / 30, () => true, 1 / 30);
  assert.equal(ok.summary().bad, 0);
  assert.equal(ok.summary().frames, 5);
  // Серия 7 до правки: прямая левая рука (сгиб 3°), кисть 57° → −168° за кадр.
  const bad = new M.TwistCheck();
  bad.frame(fakeRig({ wrist: 57 }, { wrist: -57 }), 0, () => true, 1 / 30);
  bad.frame(fakeRig({ wrist: -168 }, { wrist: -57 }, { left: 3, right: 32 }), 1 / 30, () => true, 1 / 30);
  const what = bad.bad.map((b) => b.what);
  assert.ok(what.includes("wrist") && what.includes("jump") && what.includes("straight_arm"), JSON.stringify(bad.bad));
  assert.ok(bad.summary().wrist.max > M.WRIST_TWIST_MAX);
  // Ладонь вверх у руки на клавишах (кисть провёрнута на 180°) — нарушение; та же рука не на клавишах — нет «palm_up».
  const up = new M.TwistCheck();
  up.frame(fakeRig({ wrist: 180 }, { wrist: 0 }), 0, (s) => s > 0, 1 / 30);
  assert.ok(up.bad.some((b) => b.what === "palm_up" && b.side === "left"));
  assert.ok(!up.bad.some((b) => b.what === "palm_up" && b.side === "right"));
});

test("разрез персонажа: край сдвинутого холста — ровная вертикаль; живой силуэт и край кадра — нет", () => {
  const w = 270, h = 480;
  const mask = (inside) => {
    const a = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) a[y * w + x] = inside(x, y) ? 255 : 0;
    return a;
  };
  // Голова и плечи через плечо (эллипс у левого края), холст сдвинут вправо на 37 px: слева от x=37 — пусто.
  const head = (x, y) => ((x - 20) / 110) ** 2 + ((y - 380) / 230) ** 2 < 1;
  const cut = M.straightCut(mask((x, y) => x >= 37 && head(x, y)), w, h);
  assert.ok(cut.x.run >= M.CUT_X, `разрез ${JSON.stringify(cut)}`);
  assert.ok(Math.abs(cut.x.at - 37) <= 1);
  // Тот же силуэт целиком (уходит за левый край кадра) — разреза нет.
  assert.ok(M.straightCut(mask(head), w, h).x.run < M.CUT_X);
  // Волнистая прядь (почти вертикальная) — не разрез.
  assert.ok(M.straightCut(mask((x, y) => x > 120 + 6 * Math.sin(y / 9) && x < 200 + 5 * Math.cos(y / 7)), w, h).x.run < M.CUT_X);
  // RGBA с шагом 4 (альфа — четвёртый байт), горизонтальный разрез снизу.
  const rgba = new Uint8Array(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (y < 300 && x > 20 && x < 250) rgba[(y * w + x) * 4 + 3] = 255;
  const c2 = M.straightCut(rgba, w, h, 4, 3);
  assert.ok(c2.y.run >= M.CUT_Y && Math.abs(c2.y.at - 300) <= 1, JSON.stringify(c2));
});

test("кадр: разрез и камера в теле — проблема", () => {
  const p = { expect: 1, models: 1, meshes: 25, cover: 0.5, near: 1.2, cameraNear: 0.05, cutX: 0.2, cutY: 0.5,
    cut: { x: { at: 120, run: 0.09 }, y: { at: 900, run: 0.3 } } };
  assert.equal(frameProblem({ presence: p, lost: [] }), null);
  assert.match(frameProblem({ presence: { ...p, cut: { ...p.cut, x: { at: 148, run: 0.5 } } }, lost: [] }), /вертикалью x=148/);
  assert.match(frameProblem({ presence: { ...p, cut: { ...p.cut, y: { at: 1500, run: 0.7 } } }, lost: [] }), /горизонталью/);
  assert.match(frameProblem({ presence: { ...p, near: 0.1 }, lost: [] }), /камера в теле/);
});

test("рот по огибающей голоса: в тишине почти закрыт, на громком — открыт, без огибающей — как раньше", () => {
  const speech = [[0, 2]];
  const env = { fps: 10, start: 0, values: [...Array(10).fill(0), ...Array(10).fill(1)] };
  const peak = (from, to, e) => {
    let m = 0;
    for (let t = from; t < to; t += 0.01) m = Math.max(m, ...Object.values(M.speechMouth(speech, t, e)), 0);
    return m;
  };
  assert.ok(peak(0.2, 0.9, env) < 0.15, "тишина внутри окна — рот почти закрыт");
  assert.ok(peak(1.1, 1.8, env) > 0.5, "громко — рот открыт");
  assert.ok(Math.abs(peak(0.2, 0.9) - peak(0.2, 0.9, { values: [] })) < 1e-9, "пустая огибающая не меняет рот");
  assert.equal(M.envLevel(undefined, 3), 1);
  assert.equal(M.envLevel(env, 50), env.floor ?? 0.12, "за концом огибающей — тишина");
});

test("машет любой рукой: левая — зеркало правой по x, ключ arms left: wave — левая машет, правая на месте", () => {
  for (const t of [0, 0.3, 1.1]) {
    const r = M.waveArm(t), l = M.waveArm(t, 1);
    assert.deepEqual(l.target, [-r.target[0], r.target[1], r.target[2]]);
    assert.deepEqual(l.pole, [-r.pole[0], r.pole[1], r.pole[2]]);
    assert.ok(Math.abs(l.fingers[0] + r.fingers[0]) < 1e-12 && Math.abs(l.palm[0] + r.palm[0]) < 1e-12);
  }
  const keys = [{ t: -3, left: "idle", right: "idle" }, { t: 1, dur: 0.5, left: "wave", right: "idle" }];
  assert.ok(M.armChain(keys, 1, 2).target[1] > 1.35, "левая у головы");
  assert.ok(M.armChain(keys, -1, 2).target[1] < 1.2, "правая — на столе");
});

test("скрещённые руки: кисти перед грудью, нижняя левая под верхней правой, локти наружу; разводка — по своим сторонам", () => {
  const l = M.crossArm(1, 0), r = M.crossArm(-1, 0);
  assert.ok(r.target[1] - l.target[1] > 0.08, "предплечья друг над другом, не сквозь");
  assert.ok(l.target[2] < 0.22 && r.target[2] < 0.22, "перед краем стола (z 0.22): кисти не поднимает над столешницей");
  assert.ok(l.pole[0] > 0.5 && r.pole[0] < -0.5 && l.autoPole === false, "локти наружу");
  assert.ok(M.perfArm("cross", 1, 0) && M.perfArm("cross_open", -1, 0));
  const ol = M.crossOpenArm(1), or = M.crossOpenArm(-1);
  assert.ok(ol.target[0] > 0.1 && or.target[0] < -0.1);
});

test("чат-вставка и субтитры: карточка по ту сторону полосы, где больше места, с зазором", () => {
  const low = M.chatLane(1920, [1386, 1686]);
  assert.equal(low.edge, "bottom");
  assert.ok(low.at <= 1386 - 30 && low.h > 1100);
  const high = M.chatLane(1920, [300, 600]);
  assert.equal(high.edge, "top");
  assert.ok(high.at >= 630);
});