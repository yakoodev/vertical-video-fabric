/**
 * Инспектор «✂ Джамп-каты»: найти паузы и слова-паразиты по словам субтитров
 * клипа, отметить, что вырезать, и вырезать. Рендер делит куски в обход
 * вырезов и сдвигает всё остальное (субтитры, слои, камеру, вставки).
 *
 * Значение — `render.cuts` клипа. «Вырезать» / «Вернуть всё» сохраняют сразу
 * (POST …/cuts/apply — со снимком для Ctrl+Z) и отдают итог в `onChange`, чтобы
 * редактор держал его в своём состоянии настроек.
 */
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import {
  cutsApi,
  cutsTotal,
  normalizeCuts,
  type CutItem,
  type CutMode,
  type CutSuggestion,
  type JumpCut,
} from "@/api/cuts";
import { useToast } from "@/components/Toast";

type Props = {
  clipPlanId: number;
  /** Сохранённые вырезы клипа (`render.cuts`). */
  cuts: JumpCut[];
  onChange: (cuts: JumpCut[]) => void;
  /** Перемотать превью на секунду клипа (время ДО вырезов). */
  onSeek: (t: number) => void;
};

type Row = CutItem & { on: boolean };

/** Сколько секунд до выреза включать при прослушивании — чтобы слышать контекст. */
const LEAD_IN_SEC = 1.0;

const fmt = (t: number) => {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
};
const fmtSec = (t: number) => `${t.toFixed(1).replace(".", ",")} с`;

export function CutsPanel({ clipPlanId, cuts, onChange, onSeek }: Props) {
  const toast = useToast();
  const [rows, setRows] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Omit<CutSuggestion, "cuts" | "items"> | null>(null);
  const [busy, setBusy] = useState<CutMode | "apply" | null>(null);
  const [minPause, setMinPause] = useState(0.45);
  const [keepPad, setKeepPad] = useState(0.12);
  const [tuneOpen, setTuneOpen] = useState(false);

  // Другой клип — другие подсказки.
  useEffect(() => {
    setRows([]);
    setSummary(null);
  }, [clipPlanId]);

  const saved = useMemo(() => normalizeCuts(cuts), [cuts]);
  const savedTotal = cutsTotal(saved);
  // Уже вырезанное не предлагаем ещё раз.
  const fresh = rows.filter((r) => !saved.some((c) => r.start >= c.start - 1e-3 && r.end <= c.end + 1e-3));
  const chosen = fresh.filter((r) => r.on);
  const chosenTotal = cutsTotal(normalizeCuts([...saved, ...chosen])) - savedTotal;
  const before = summary?.before_sec ?? 0;

  const find = async (mode: CutMode) => {
    setBusy(mode);
    try {
      const res = await cutsApi.suggest(clipPlanId, { mode, min_pause: minPause, keep_pad: keepPad });
      setRows(res.items.map((it) => ({ ...it, on: !(it as { risky?: boolean }).risky })));
      setSummary({ saved_sec: res.saved_sec, before_sec: res.before_sec, after_sec: res.after_sec });
      if (!res.items.length) toast.success(mode === "pauses" ? "Длинных пауз нет" : "Слов-паразитов не нашлось");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось найти, что вырезать");
    } finally {
      setBusy(null);
    }
  };

  const save = async (next: JumpCut[], done: string) => {
    setBusy("apply");
    try {
      const res = await cutsApi.apply(clipPlanId, normalizeCuts(next));
      onChange(res.cuts);
      toast.success(done);
      return true;
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить вырезы");
      return false;
    } finally {
      setBusy(null);
    }
  };

  const cutChosen = async () => {
    if (!chosen.length) return;
    const ok = await save([...saved, ...chosen], `Вырезано ${chosen.length} — минус ${fmtSec(chosenTotal)}`);
    if (ok) setRows((prev) => prev.filter((r) => !r.on));
  };

  const restore = (index: number) =>
    void save(saved.filter((_c, i) => i !== index), "Фрагмент возвращён");

  const toggle = (index: number) =>
    setRows((prev) => {
      const target = fresh[index];
      return prev.map((r) => (r === target ? { ...r, on: !r.on } : r));
    });
  const toggleAll = (on: boolean) =>
    setRows((prev) => prev.map((r) => (fresh.includes(r) ? { ...r, on } : r)));

  return (
    <div className="cut-panel">
      <div className="cut-find">
        <button type="button" className="btn sm" disabled={busy !== null} onClick={() => void find("pauses")}>
          {busy === "pauses" ? "Ищу…" : "Найти паузы"}
        </button>
        <button type="button" className="btn sm" disabled={busy !== null} onClick={() => void find("fillers")}>
          {busy === "fillers" ? "Ищу…" : "Найти слова-паразиты"}
        </button>
        <button
          type="button"
          className={`btn ghost sm cut-tune-toggle${tuneOpen ? " on" : ""}`}
          aria-expanded={tuneOpen}
          title="Порог паузы и запас тишины у слов"
          onClick={() => setTuneOpen((v) => !v)}
        >
          ⚙
        </button>
      </div>

      {tuneOpen && (
        <div className="cut-tune">
          <label className="cut-tune-row">
            <span>Пауза длиннее</span>
            <input
              type="range"
              min={0.2}
              max={1.5}
              step={0.05}
              value={minPause}
              onChange={(e) => setMinPause(Number(e.target.value))}
            />
            <b>{fmtSec(minPause)}</b>
          </label>
          <label className="cut-tune-row">
            <span>Запас у слов</span>
            <input
              type="range"
              min={0}
              max={0.4}
              step={0.02}
              value={keepPad}
              onChange={(e) => setKeepPad(Number(e.target.value))}
            />
            <b>{fmtSec(keepPad)}</b>
          </label>
        </div>
      )}

      {fresh.length > 0 && (
        <div className="cut-found">
          <div className="cut-found-head">
            <label className="cut-check-all">
              <input
                type="checkbox"
                checked={chosen.length === fresh.length}
                ref={(el) => {
                  if (el) el.indeterminate = chosen.length > 0 && chosen.length < fresh.length;
                }}
                onChange={(e) => toggleAll(e.target.checked)}
              />
              <span>
                Найдено {fresh.length}, отмечено {chosen.length}
              </span>
            </label>
            <span className="cut-gain" title="Сколько станет короче клип">
              −{fmtSec(chosenTotal)}
            </span>
          </div>
          <ul className="cut-list">
            {fresh.map((r, i) => (
              <li key={`${r.start}-${r.end}`} className={`cut-row${r.on ? " on" : ""}`}>
                <input
                  type="checkbox"
                  checked={r.on}
                  aria-label={`Вырезать ${fmt(r.start)}–${fmt(r.end)}`}
                  onChange={() => toggle(i)}
                />
                <button
                  type="button"
                  className="cut-play"
                  title="Послушать: с секунды до выреза"
                  aria-label={`Послушать ${fmt(r.start)}`}
                  onClick={() => onSeek(Math.max(0, r.start - LEAD_IN_SEC))}
                >
                  ▶
                </button>
                <button type="button" className="cut-when" title="Перейти к началу выреза" onClick={() => onSeek(r.start)}>
                  {fmt(r.start)}
                </button>
                <span className="cut-reason" title={r.reason}>
                  {r.reason}
                </span>
                <span className="cut-len">−{(r.end - r.start).toFixed(2)}</span>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="btn primary sm cut-apply"
            disabled={busy !== null || !chosen.length}
            onClick={() => void cutChosen()}
          >
            {busy === "apply" ? "Сохраняю…" : `Вырезать ${chosen.length ? `(${chosen.length})` : ""}`}
          </button>
        </div>
      )}

      {saved.length > 0 ? (
        <div className="cut-saved">
          <div className="cut-found-head">
            <span>
              Вырезано {saved.length} · <b>−{fmtSec(savedTotal)}</b>
              {before > 0 && (
                <span className="cut-dim">
                  {" "}
                  ({fmt(before)} → {fmt(Math.max(0, before - savedTotal))})
                </span>
              )}
            </span>
            <button
              type="button"
              className="btn ghost sm"
              disabled={busy !== null}
              onClick={() => void save([], "Всё вырезанное возвращено")}
            >
              Вернуть всё
            </button>
          </div>
          <ul className="cut-list cut-list-saved">
            {saved.map((c, i) => (
              <li key={`${c.start}-${c.end}`} className="cut-row">
                <button
                  type="button"
                  className="cut-play"
                  title="Послушать склейку: с секунды до выреза"
                  onClick={() => onSeek(Math.max(0, c.start - LEAD_IN_SEC))}
                >
                  ▶
                </button>
                <button type="button" className="cut-when" onClick={() => onSeek(c.start)}>
                  {fmt(c.start)}–{fmt(c.end)}
                </button>
                <span className="cut-len">−{(c.end - c.start).toFixed(2)}</span>
                <button
                  type="button"
                  className="cut-undo"
                  title="Вернуть этот фрагмент"
                  aria-label={`Вернуть ${fmt(c.start)}`}
                  disabled={busy !== null}
                  onClick={() => restore(i)}
                >
                  ↺
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        !fresh.length && (
          <p className="cut-empty">
            Паузы и «ээ» ищутся по словам субтитров клипа — сначала сделайте субтитры. Вырезанное не теряется: его
            можно вернуть.
          </p>
        )
      )}
    </div>
  );
}
