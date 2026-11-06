// Время клипа по времени исходника. Модуль без импортов: node запускает его как есть (tests/test_frontend_editor_keys.py).

// Плеер хранит время в микросекундах и округляет вниз: встали на начало куска 268.4, а currentTime читается
// как 268.399998, то есть формально мимо куска. Кадр тот же, поэтому такая мелочь — ещё этот кусок.
export const SEEK_SLACK_SEC = 0.001;

// Clip time of a source time for back-to-back pieces (null outside the clip).
export function clipTimeOf(segments: { start_sec: number; end_sec: number; speed?: number }[], cur: number): number | null {
  let t = 0;
  for (const s of segments) {
    const speed = s.speed && s.speed > 0 ? s.speed : 1;
    if (cur >= s.start_sec - SEEK_SLACK_SEC && cur <= s.end_sec) return t + Math.max(0, cur - s.start_sec) / speed;
    t += Math.max(0, s.end_sec - s.start_sec) / speed;
  }
  return null;
}
