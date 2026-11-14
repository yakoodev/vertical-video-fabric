/**
 * Снаряжение героев-игрушек мем-серий (поле ``gear`` у ``critter`` и ``kodex2``): мультяшные доспехи и меч — серия 6
 * «Сравнение» (Клод и Кодекс «переспоривают» друг друга средневековой битвой). Всё — скруглённые коробки под toon-
 * обводку, крепится к частям корпуса (buildCritter): шлем и нагрудник — к ``squash`` (сжимаются с телом), наплечники и
 * меч — к ручкам (идут за позой руки).
 *
 * ``gear: {helmet?, plume?, plate?, name?, pauldrons?, sword?, swordHand?, metal?, trim?}``:
 * - ``helmet`` — шапка-шлем по верху корпуса (поля над глазами) и наносник между глаз; ``plume`` — цвет плюмажа;
 * - ``plate`` — нагрудник-пояс по низу корпуса (закрывает бирку — имя ``name`` пишется на самом нагруднике);
 * - ``pauldrons`` — наплечники на ручках; ``sword`` — меч в ручке ``swordHand`` (R по умолчанию — ``armR``);
 * - ``metal`` / ``trim`` — цвета стали и отделки.
 *
 * Поза меча — ключ ``swing`` в ``pose`` героя: 0 — клинок вверх, 1 — вперёд горизонтально, 1.4 — рубящий вниз;
 * ``swordRoll`` (°) — разворот клинка вокруг оси руки (парирование плашмя). Остальное — позой руки (``armR``).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

export interface GearSpec {
  helmet?: boolean;
  plume?: string;
  plate?: boolean;
  name?: string;
  pauldrons?: boolean;
  sword?: boolean;
  swordHand?: "R" | "L";
  metal?: string;
  trim?: string;
}

export interface GearDims { bodyW: number; bodyH: number; bodyD: number; armW: number; armH: number; armD: number; eyeX: number }

type Arm = { pivot: THREE.Group; nub: THREE.Mesh; side: number };

const D2R = Math.PI / 180;

function mat(c: string, metal = false, glow = 0) {
  const m = new THREE.MeshStandardMaterial({ color: new THREE.Color(c), roughness: metal ? 0.38 : 0.62, metalness: metal ? 0.35 : 0 });
  if (glow > 0) {
    m.emissive = new THREE.Color(c);
    m.emissiveIntensity = glow;
  }
  return m;
}

function rb(w: number, h: number, d: number, m: THREE.Material, r?: number) {
  const o = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, Math.min(r ?? Math.min(w, h, d) * 0.3, Math.min(w, h, d) * 0.49)), m);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

/** Надпись на нагруднике: канвас-плашка (имя героя, как бирка, только крупнее). */
function namePlate(text: string, w: number, h: number, bg: string, fg: string) {
  const cv = document.createElement("canvas");
  cv.width = 512;
  cv.height = Math.round((512 * h) / w);
  const g = cv.getContext("2d")!;
  g.fillStyle = bg;
  g.beginPath();
  g.roundRect(0, 0, cv.width, cv.height, cv.height * 0.3);
  g.fill();
  g.fillStyle = fg;
  g.textAlign = "center";
  g.textBaseline = "middle";
  let px = cv.height * 0.7;
  g.font = `900 ${Math.round(px)}px Montserrat, Arial, sans-serif`;
  const tw = g.measureText(text).width;
  if (tw > cv.width * 0.88) px *= (cv.width * 0.88) / tw;
  g.font = `900 ${Math.round(px)}px Montserrat, Arial, sans-serif`;
  g.fillText(text, cv.width / 2, cv.height * 0.55);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, transparent: true }));
  m.userData.toon = false;
  m.userData.outline = false;
  return m;
}

/**
 * Надеть снаряжение. ``S`` — ширина корпуса (м), ``P`` — пропорции корпуса (доли ширины). Возвращает update(t, num):
 * num(field, default) — значение ключа позы героя на t (меч берёт ``swing`` и ``swordRoll``).
 */
export function addGear(squash: THREE.Group, arms: Arm[], S: number, P: GearDims, gear: GearSpec) {
  const steel = mat(gear.metal || "#c9d0da", true);
  const dark = mat("#7c8594", true);
  const trim = mat(gear.trim || "#e2b13c", true, 0.1);
  const W = P.bodyW * S, H = P.bodyH * S, D = P.bodyD * S;
  const eyeTop = 0.9 * H; // поля шлема — над глазами (глаз до ~0.75 высоты корпуса)
  if (gear.helmet) {
    // Шапка-шлем: купол чуть шире корпуса, поля-обод, наносник между глаз, плюмаж.
    const cap = rb(W * 1.07, H - eyeTop + 0.1 * S, D * 1.1, steel, 0.09 * S);
    cap.position.y = eyeTop + (H - eyeTop + 0.1 * S) / 2 - 0.01 * S;
    squash.add(cap);
    const rim = rb(W * 1.12, 0.05 * S, D * 1.15, dark, 0.02 * S);
    rim.position.y = eyeTop - 0.005 * S;
    squash.add(rim);
    // Без наносника: между глаз он читался буквой «М» и прятал взгляд.
    // Гребень по макушке и плюмаж-перья (три скруглённых «лепестка» назад).
    const crest = rb(0.06 * S, 0.06 * S, D * 0.9, trim, 0.02 * S);
    crest.position.set(0, H + 0.1 * S, 0);
    squash.add(crest);
    if (gear.plume) {
      const pm = mat(gear.plume, false, 0.12);
      for (let i = 0; i < 3; i++) {
        const f = rb(0.07 * S, (0.2 - i * 0.03) * S, 0.12 * S, pm, 0.03 * S);
        f.position.set(0, H + (0.2 + i * 0.015) * S, (0.06 - i * 0.11) * S);
        f.rotation.x = -0.35 - i * 0.35;
        squash.add(f);
      }
    }
  }
  if (gear.plate) {
    // Нагрудник по низу корпуса (под глазами), с полосой отделки и именем.
    const ph = 0.145 * S;
    const plate = rb(W * 1.06, ph, D * 1.1, steel, 0.05 * S);
    plate.position.y = ph / 2 + 0.005 * S;
    squash.add(plate);
    const band = rb(W * 1.08, 0.03 * S, D * 1.13, trim, 0.012 * S);
    band.position.y = ph + 0.01 * S;
    squash.add(band);
    if (gear.name) {
      const lab = namePlate(gear.name, W * 0.62, ph * 0.62, "#2b2430", "#fff4e4");
      lab.position.set(0, ph * 0.5, (D * 1.1) / 2 + 0.002);
      lab.renderOrder = 3;
      squash.add(lab);
    }
  }
  if (gear.pauldrons) {
    for (const a of arms) {
      const p = rb((P.armW + 0.06) * S, 0.09 * S, (P.armD + 0.06) * S, steel, 0.035 * S);
      p.position.set(a.side * 0.01 * S, (P.armH / 2 + 0.03) * S, 0);
      a.nub.add(p);
    }
  }
  let sword: THREE.Group | null = null;
  let swordArm: Arm | null = null;
  if (gear.sword) {
    // Меч в кулачке: рукоять у внешнего конца ручки, клинок вверх (ось +y), гарда поперёк.
    swordArm = arms.find((a) => a.side === ((gear.swordHand || "R") === "R" ? -1 : 1)) || arms[0];
    sword = new THREE.Group();
    sword.position.set(swordArm.side * (P.armW / 2) * S, 0, 0.02 * S);
    swordArm.nub.add(sword);
    const grip = new THREE.Mesh(new THREE.CylinderGeometry(0.022 * S, 0.022 * S, 0.16 * S, 10), mat("#5a3a2a"));
    grip.castShadow = true;
    sword.add(grip);
    const pommel = new THREE.Mesh(new THREE.SphereGeometry(0.035 * S, 12, 10), trim);
    pommel.position.y = -0.09 * S;
    sword.add(pommel);
    const guard = rb(0.05 * S, 0.045 * S, 0.3 * S, trim, 0.018 * S);
    guard.position.y = 0.08 * S;
    sword.add(guard);
    const L = 0.72 * S;
    const blade = rb(0.025 * S, L, 0.085 * S, mat("#e8edf3", true, 0.06), 0.012 * S);
    blade.position.y = 0.1 * S + L / 2;
    sword.add(blade);
    const tip = new THREE.Mesh(new THREE.ConeGeometry(0.06 * S, 0.11 * S, 4), mat("#e8edf3", true, 0.06));
    tip.scale.set(0.3, 1, 1);
    tip.rotation.y = Math.PI / 4;
    tip.position.y = 0.1 * S + L + 0.05 * S;
    tip.castShadow = true;
    sword.add(tip);
    // Желобок-блик вдоль клинка.
    const fuller = new THREE.Mesh(new THREE.BoxGeometry(0.028 * S, L * 0.8, 0.012 * S), mat("#aeb8c6", true));
    fuller.position.y = 0.1 * S + L * 0.45;
    fuller.userData.outline = false;
    sword.add(fuller);
    sword.userData.tipLocal = new THREE.Vector3(0, 0.1 * S + L + 0.08 * S, 0);
  }
  void eyeTop;
  return {
    /** Кончик меча в мире (для искр удара). */
    swordTip(out = new THREE.Vector3()) {
      if (!sword) return out.set(0, 0, 0);
      sword.updateWorldMatrix(true, false);
      return out.copy(sword.userData.tipLocal).applyMatrix4(sword.matrixWorld);
    },
    update(_t: number, num: (field: string, d: number) => number) {
      if (!sword || !swordArm) return;
      // Ручка повёрнута вокруг вертикали на «вперёд» — клинок наклоняем вокруг оси руки (x ручки): вперёд = +z тела.
      sword.rotation.set(num("swing", 0) * 90 * D2R, 0, swordArm.side * num("swordRoll", 0) * D2R, "XZY");
    },
  };
}
