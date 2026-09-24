import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clipPlansApi, type SubtitleLine } from "@/api/clipPlans";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { formatDuration } from "@/components/ui";

const ENGINES: [string, string][] = [
  ["", "как в стиле"],
  ["whisper", "Whisper (локально)"],
  ["gemini", "Gemini"],
];

/**
 * Субтитры клипа ДО рендера: распознать речь, поправить текст и тайминги, и
 * только потом жечь их в видео. Раньше текст появлялся лишь в готовом клипе —
 * опечатку можно было увидеть только после рендера.
 *
 * `onLines` отдаёт строки наверх: редактор рисует текущую на превью-телефоне.
 * `onWords` — пословные тайминги: по ним превью рисует караоке ровно так же,
 * как его выжжет рендер.
 */
export function SubtitleEditor({
  clipPlanId,
  clipTime,
  onSeek,
  onLines,
  onWords,
}: {
  clipPlanId: number;
  /** Текущая позиция плеера в секундах клипа (для подсветки строки). */
  clipTime: () => number;
  onSeek: (clipSec: number) => void;
  onLines: (lines: SubtitleLine[]) => void;
  onWords?: (words: { word: string; start: number; end: number }[]) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [lines, setLines] = useState<SubtitleLine[]>([]);
  const [engine, setEngine] = useState("");
  const [dirty, setDirty] = useState(false);
  const [active, setActive] = useState(-1);
  const loadedFor = useRef<number | null>(null);

  const query = useQuery({
    queryKey: ["plan-subtitles", clipPlanId],
    queryFn: () => clipPlansApi.getSubtitles(clipPlanId),
  });

  useEffect(() => {
    if (query.data && loadedFor.current !== clipPlanId) {
      loadedFor.current = clipPlanId;
      setLines(query.data.lines ?? []);
      setDirty(false);
      onLines(query.data.lines ?? []);
      onWords?.(query.data.words ?? []);
    }
  }, [query.data, clipPlanId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Подсветка строки под курсором воспроизведения.
  useEffect(() => {
    const id = window.setInterval(() => {
      const t = clipTime();
      setActive(lines.findIndex((l) => t >= l.start && t < l.end));
    }, 200);
    return () => window.clearInterval(id);
  }, [lines, clipTime]);

  const generate = useMutation({
    mutationFn: () => clipPlansApi.generateSubtitles(clipPlanId, engine),
    onSuccess: (data) => {
      setLines(data.lines ?? []);
      setDirty(false);
      onLines(data.lines ?? []);
      qc.invalidateQueries({ queryKey: ["plan-subtitles", clipPlanId] });
      toast.success(`Распознано строк: ${data.lines?.length ?? 0} (${data.provider || "движок по умолчанию"})`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось распознать речь"),
  });

  const save = useMutation({
    mutationFn: () => clipPlansApi.saveSubtitles(clipPlanId, lines),
    onSuccess: (data) => {
      setDirty(false);
      onLines(data.lines ?? []);
      qc.invalidateQueries({ queryKey: ["plan-subtitles", clipPlanId] });
      toast.success("Субтитры сохранены — рендер возьмёт именно их");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });

  const edit = (i: number, patch: Partial<SubtitleLine>) => {
    setLines((prev) => {
      const next = prev.map((l, j) => (j === i ? { ...l, ...patch } : l));
      onLines(next);
      return next;
    });
    setDirty(true);
  };
  const removeLine = (i: number) => {
    setLines((prev) => {
      const next = prev.filter((_, j) => j !== i);
      onLines(next);
      return next;
    });
    setDirty(true);
  };
  // Разделить строку пополам по времени — самый частый ручной приём.
  const splitLine = (i: number) => {
    setLines((prev) => {
      const line = prev[i];
      const mid = Number(((line.start + line.end) / 2).toFixed(2));
      const words = line.text.split(/\s+/);
      const half = Math.max(1, Math.round(words.length / 2));
      const next = [
        ...prev.slice(0, i),
        { start: line.start, end: mid, text: words.slice(0, half).join(" ") },
        { start: mid, end: line.end, text: words.slice(half).join(" ") || "…" },
        ...prev.slice(i + 1),
      ];
      onLines(next);
      return next;
    });
    setDirty(true);
  };

  const busy = generate.isPending || save.isPending;
  return (
    <div className="subed">
      <div className="subed-top">
        <select className="input" value={engine} onChange={(e) => setEngine(e.target.value)} disabled={busy}>
          {ENGINES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button className="btn sm" disabled={busy} onClick={() => generate.mutate()}>
          {generate.isPending ? "Слушаю…" : lines.length ? "↻ Распознать заново" : "🎙 Распознать речь"}
        </button>
      </div>

      {!lines.length ? (
        <p className="muted subed-empty">
          Субтитров пока нет. Распознайте речь — строки можно будет поправить здесь, и рендер возьмёт именно их
          (без правки текст распознаётся прямо во время рендера).
        </p>
      ) : (
        <>
          <div className="subed-list">
            {lines.map((line, i) => (
              <div key={i} className={`subed-line${i === active ? " on" : ""}`}>
                <div className="subed-line-head">
                  <button className="subed-time mono" title="Перейти к строке" onClick={() => onSeek(line.start)}>
                    {formatDuration(line.start)}
                  </button>
                  <input
                    className="input subed-num mono"
                    type="number"
                    step={0.1}
                    value={line.start}
                    onChange={(e) => edit(i, { start: Number(e.target.value) })}
                    title="Начало, с"
                  />
                  <input
                    className="input subed-num mono"
                    type="number"
                    step={0.1}
                    value={line.end}
                    onChange={(e) => edit(i, { end: Number(e.target.value) })}
                    title="Конец, с"
                  />
                  <button className="subed-act" title="Разделить строку" onClick={() => splitLine(i)}>
                    ✂
                  </button>
                  <button className="subed-act" title="Удалить строку" onClick={() => removeLine(i)}>
                    ✕
                  </button>
                </div>
                <textarea
                  className="input subed-text"
                  rows={2}
                  value={line.text}
                  maxLength={300}
                  onChange={(e) => edit(i, { text: e.target.value })}
                />
              </div>
            ))}
          </div>
          <div className="subed-foot">
            <span className="muted">
              {lines.length} строк{query.data?.edited ? " · правлено" : ""}
            </span>
            <button className="btn primary sm" disabled={!dirty || busy} onClick={() => save.mutate()}>
              {save.isPending ? "…" : dirty ? "Сохранить" : "Сохранено"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
