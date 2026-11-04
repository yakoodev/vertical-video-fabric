/** Геометрия холста обложки: попадание, привязка с направляющими, изменение размера с поворотом. */
export type Box = { x: number; y: number; w: number; h: number; rotation: number };
export type Guide = { axis: "x" | "y"; at: number };
export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
export type Bounds = { left: number; right: number; top: number; bottom: number };

const rad = (deg: number) => (deg * Math.PI) / 180;

export function center(box: Box): { x: number; y: number } {
  return { x: box.x + box.w / 2, y: box.y + box.h / 2 };
}

/** Рамка с учётом поворота: по ней слой привязывается к другим. */
export function boundsOf(box: Box): Bounds {
  const c = center(box);
  const cos = Math.abs(Math.cos(rad(box.rotation)));
  const sin = Math.abs(Math.sin(rad(box.rotation)));
  const hw = (box.w * cos + box.h * sin) / 2;
  const hh = (box.w * sin + box.h * cos) / 2;
  return { left: c.x - hw, right: c.x + hw, top: c.y - hh, bottom: c.y + hh };
}

export function contains(box: Box, px: number, py: number): boolean {
  const c = center(box);
  const a = rad(-box.rotation);
  const dx = px - c.x;
  const dy = py - c.y;
  const lx = dx * Math.cos(a) - dy * Math.sin(a);
  const ly = dx * Math.sin(a) + dy * Math.cos(a);
  return Math.abs(lx) <= box.w / 2 && Math.abs(ly) <= box.h / 2;
}

export type SnapLines = { x: number[]; y: number[] };

/** Куда можно привязаться: края и центр кадра, края и центры других видимых слоёв. */
export function snapLines(width: number, height: number, others: Box[]): SnapLines {
  const x = [0, width / 2, width];
  const y = [0, height / 2, height];
  for (const other of others) {
    const b = boundsOf(other);
    x.push(b.left, (b.left + b.right) / 2, b.right);
    y.push(b.top, (b.top + b.bottom) / 2, b.bottom);
  }
  return { x, y };
}

function nearest(values: number[], lines: number[], tolerance: number): { delta: number; at: number } | null {
  let best: { delta: number; at: number } | null = null;
  for (const value of values) {
    for (const line of lines) {
      const delta = line - value;
      if (Math.abs(delta) <= tolerance && (!best || Math.abs(delta) < Math.abs(best.delta))) best = { delta, at: line };
    }
  }
  return best;
}

export function moveWithSnap(box: Box, dx: number, dy: number, lines: SnapLines, tolerance: number) {
  const moved = { ...box, x: box.x + dx, y: box.y + dy };
  const b = boundsOf(moved);
  const sx = nearest([b.left, (b.left + b.right) / 2, b.right], lines.x, tolerance);
  const sy = nearest([b.top, (b.top + b.bottom) / 2, b.bottom], lines.y, tolerance);
  const guides: Guide[] = [];
  if (sx) {
    moved.x += sx.delta;
    guides.push({ axis: "x", at: sx.at });
  }
  if (sy) {
    moved.y += sy.delta;
    guides.push({ axis: "y", at: sy.at });
  }
  return { x: moved.x, y: moved.y, guides };
}

const SIGNS: Record<Handle, [number, number]> = {
  nw: [-1, -1], n: [0, -1], ne: [1, -1], e: [1, 0], se: [1, 1], s: [0, 1], sw: [-1, 1], w: [-1, 0],
};

export const isCorner = (handle: Handle) => handle.length === 2;

/**
 * Новая рамка, когда тянут за ручку: противоположная точка стоит на месте (с поворотом тоже).
 * keepRatio — углы держат пропорции. Привязка к линиям — только без поворота.
 */
export function resizeBox(
  start: Box,
  handle: Handle,
  px: number,
  py: number,
  keepRatio: boolean,
  lines: SnapLines | null,
  tolerance: number,
  minSide = 8,
): { box: Box; guides: Guide[] } {
  const [sx, sy] = SIGNS[handle];
  const c = center(start);
  const a = rad(start.rotation);
  const cos = Math.cos(a);
  const sin = Math.sin(a);
  const toWorld = (lx: number, ly: number) => ({ x: c.x + lx * cos - ly * sin, y: c.y + lx * sin + ly * cos });
  const anchor = toWorld((-sx * start.w) / 2, (-sy * start.h) / 2);
  const guides: Guide[] = [];
  let tx = px;
  let ty = py;
  if (lines && start.rotation % 360 === 0 && !(keepRatio && isCorner(handle))) {
    if (sx) {
      const snap = nearest([px], lines.x, tolerance);
      if (snap) {
        tx += snap.delta;
        guides.push({ axis: "x", at: snap.at });
      }
    }
    if (sy) {
      const snap = nearest([py], lines.y, tolerance);
      if (snap) {
        ty += snap.delta;
        guides.push({ axis: "y", at: snap.at });
      }
    }
  }
  const dx = tx - anchor.x;
  const dy = ty - anchor.y;
  const lx = dx * cos + dy * sin;
  const ly = -dx * sin + dy * cos;
  let w = sx ? Math.max(minSide, lx * sx) : start.w;
  let h = sy ? Math.max(minSide, ly * sy) : start.h;
  if (keepRatio && isCorner(handle)) {
    const scale = Math.max(w / start.w, h / start.h, minSide / Math.min(start.w, start.h));
    w = start.w * scale;
    h = start.h * scale;
  }
  // Центр — на середине между неподвижной точкой и новой ручкой.
  const offX = (sx * w) / 2;
  const offY = (sy * h) / 2;
  const nc = { x: anchor.x + offX * cos - offY * sin, y: anchor.y + offX * sin + offY * cos };
  return { box: { ...start, w, h, x: nc.x - w / 2, y: nc.y - h / 2 }, guides };
}

/** Угол из точки: ручка над рамкой, поэтому 0° — вверх. Близко к кратному 45° — притягивает. */
export function rotationAt(box: Box, px: number, py: number, free: boolean): number {
  const c = center(box);
  let deg = (Math.atan2(py - c.y, px - c.x) * 180) / Math.PI + 90;
  if (deg > 180) deg -= 360;
  if (!free) {
    const near = Math.round(deg / 45) * 45;
    if (Math.abs(deg - near) < 3) deg = near;
  }
  return Math.round(deg * 10) / 10;
}
