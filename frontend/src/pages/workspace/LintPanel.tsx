/**
 * 🔎 «Проверка» перед сдачей (app/quality_lint.py — то же, что MCP `lint_clip`): текст и
 * субтитры под кнопками TikTok/Reels/Shorts, титры, которые не успеть прочитать, вспышки
 * готового рендера и стык конца с началом (луп). Только предупреждения — ничего не меняет.
 *
 * Проверяется сохранённый клип: перед запуском редактор дописывает отложенное автосохранение
 * (`beforeRun`). Время находок — шкала клипа (как таймлайн): «▶ 0:03.2» перематывает превью.
 */
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import { LINT_CHECK_LABELS, lintApi, type LintCheck, type LintFinding, type LintResult } from "@/api/lint";
import { useToast } from "@/components/Toast";

type Props = {
  clipPlanId: number;
  /** Дописать отложенное автосохранение настроек перед проверкой. */
  beforeRun?: () => Promise<void> | void;
  /** Перемотать превью на секунду клипа (время ДО вырезов). */
  onSeek: (t: number) => void;
};

const PLATFORM_NAMES: Record<string, string> = { tiktok: "TikTok", reels: "Reels", shorts: "Shorts" };

const fmt = (t: number) => {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
};

export function LintPanel({ clipPlanId, beforeRun, onSeek }: Props) {
  const toast = useToast();
  const [result, setResult] = useState<LintResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [withMedia, setWithMedia] = useState(true);
  const [filter, setFilter] = useState<LintCheck | null>(null);
  const [showInfo, setShowInfo] = useState(true);

  // Другой клип — другие находки.
  useEffect(() => {
    setResult(null);
    setFilter(null);
  }, [clipPlanId]);

  const run = async () => {
    setBusy(true);
    try {
      await beforeRun?.();
      const res = await lintApi.run(clipPlanId, { media: withMedia ? "auto" : "false" });
      setResult(res);
      if (!res.findings.length) toast.success("Замечаний нет");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось проверить клип");
    } finally {
      setBusy(false);
    }
  };

  const shown = useMemo<LintFinding[]>(
    () =>
      (result?.findings ?? []).filter(
        (f) => (!filter || f.check === filter) && (showInfo || f.severity === "warn"),
      ),
    [result, filter, showInfo],
  );
  const loopScore = result?.loop?.score;

  return (
    <div className="lint-panel">
      <div className="lint-run">
        <button type="button" className="btn primary sm" disabled={busy} onClick={() => void run()}>
          {busy ? "Проверяю…" : result ? "Проверить снова" : "🔎 Проверить"}
        </button>
        <label
          className="lint-opt"
          title="Вспышки и луп считаются по последнему готовому рендеру (несколько секунд). Без них — мгновенно, только план"
        >
          <input type="checkbox" checked={withMedia} onChange={(e) => setWithMedia(e.target.checked)} />
          по рендеру
        </label>
      </div>

      {result ? (
        <>
          <div className="lint-summary">
            <b>{result.summary}</b>
            {result.platforms.length ? (
              <> · площадки: {result.platforms.map((p) => PLATFORM_NAMES[p] ?? p).join(", ")}</>
            ) : null}
            {result.render_id ? <> · рендер #{result.render_id}</> : null}
            {result.render_stale ? <div className="cut-dim">⚠ {result.render_stale}</div> : null}
          </div>
          <div className="lint-chips">
            {result.checks.map((c) => (
              <button
                key={c}
                type="button"
                className={`lint-chip${filter === c ? " on" : ""}`}
                onClick={() => setFilter((prev) => (prev === c ? null : c))}
                title="Показать только эту проверку"
              >
                {LINT_CHECK_LABELS[c]} {result.by_check[c] ?? 0}
              </button>
            ))}
            {typeof loopScore === "number" ? (
              <span className={`lint-chip ${loopScore >= 0.75 ? "good" : "bad"}`} title="Насколько конец стыкуется с началом">
                луп {loopScore.toFixed(2)}
              </span>
            ) : null}
            {result.flashes ? (
              <span
                className={`lint-chip ${result.flashes.max_per_sec.general > result.flashes.limit_per_sec ? "bad" : "good"}`}
                title="Больше всего вспышек за секунду (норма ≤ 3)"
              >
                вспышек ≤ {Math.max(result.flashes.max_per_sec.general, result.flashes.max_per_sec.red)}/с
              </span>
            ) : null}
            <label className="lint-opt">
              <input type="checkbox" checked={showInfo} onChange={(e) => setShowInfo(e.target.checked)} />
              подсказки
            </label>
          </div>
          {shown.length ? (
            <ul className="lint-list">
              {shown.map((f, i) => (
                <li key={`${f.check}-${i}`} className={`lint-item ${f.severity}`}>
                  <span className="lint-sev" aria-label={f.severity === "warn" ? "предупреждение" : "подсказка"}>
                    {f.severity === "warn" ? "⚠" : "ℹ"}
                  </span>
                  <span className="lint-msg">{f.message}</span>
                  {f.fix ? <span className="lint-fix">→ {f.fix}</span> : null}
                  <span className="lint-meta">
                    {typeof f.t === "number" ? (
                      <button
                        type="button"
                        className="cut-when"
                        title="Перейти к этому месту"
                        onClick={() => onSeek(f.t as number)}
                      >
                        ▶ {fmt(f.t)}
                        {typeof f.until === "number" && f.until > f.t + 0.05 ? `–${fmt(f.until)}` : ""}
                      </button>
                    ) : null}
                    <span>{LINT_CHECK_LABELS[f.check]}</span>
                    {f.layer ? <span className="mono">{f.layer}</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : result.findings.length ? (
            <p className="cut-empty">По этому фильтру замечаний нет.</p>
          ) : null}
          {Object.keys(result.skipped).length ? (
            <ul className="lint-skip">
              {Object.entries(result.skipped).map(([c, why]) => (
                <li key={c}>
                  {LINT_CHECK_LABELS[c as LintCheck] ?? c}: {why}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <p className="cut-empty">
          Текст и субтитры под кнопками TikTok/Reels/Shorts, титры, которые не успеть прочитать, вспышки чаще 3 в секунду
          и стык конца с началом. Только предупреждения — сдавать не мешает.
        </p>
      )}
    </div>
  );
}
