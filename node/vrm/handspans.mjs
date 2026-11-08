/** Кадры с проникновением рук (handcheck.ts: {t, key, gap, point, against}) → отрезки подряд идущих кадров одной
 * группы с худшим зазором отрезка (м и мм) — список «время → что» для разбора и отчёта. */
export function spans(bad, fps) {
  const out = [];
  for (const b of [...bad].sort((x, y) => (x.key < y.key ? -1 : x.key > y.key ? 1 : x.t - y.t))) {
    const last = out[out.length - 1];
    if (last && last.key === b.key && b.t - last.t1 <= 1.5 / fps) {
      last.t1 = b.t;
      if (b.gap < last.gap) Object.assign(last, { gap: b.gap, point: b.point, against: b.against, worst: b.t });
    } else out.push({ key: b.key, t0: b.t, t1: b.t, gap: b.gap, point: b.point, against: b.against, worst: b.t });
  }
  return out.map((s) => ({ ...s, gap_mm: Math.round(s.gap * 10000) / 10 }));
}
