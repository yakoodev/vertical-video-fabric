import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clipPlansApi, type DubResult, type PlanSubtitles, type SubtitleLine, type TranslationMode } from "@/api/clipPlans";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { formatDuration } from "@/components/ui";
import { useModuleOn, useModules } from "@/hooks/useModules";
import { resolveCaptions, speakerColor } from "@/motion";

const VOICES = ["A", "B", "C", "D"];
const PLAIN = resolveCaptions({});
/** Цвет голоса как в субтитрах-слое: главный — белый, остальные — палитра движка. */
const voiceColor = (s: string | undefined) => speakerColor(PLAIN, s) ?? "#ffffff";
/** Плашка голоса в списке: цвет субтитров фоном (главный — нейтральной). */
const voiceTag = (s: string | undefined) =>
  s && s !== "A" ? { background: voiceColor(s), color: "#0b0b0f" } : { background: "var(--surface-3)", color: "var(--text)" };

const ENGINES: [string, string][] = [
  ["", "как в стиле"],
  ["whisper", "Whisper (локально)"],
  ["gemini", "Gemini"],
];

const SILENT_HINT =
  "У холста нет звука — распознавать нечего. Текст на экране — слой «Текст» в библиотеке; слова субтитров со временем задаёт агент (set_subtitle_words).";

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
  silent,
  onServerChange,
}: {
  clipPlanId: number;
  /** Текущая позиция плеера в секундах клипа (для подсветки строки). */
  clipTime: () => number;
  onSeek: (clipSec: number) => void;
  onLines: (lines: SubtitleLine[]) => void;
  onWords?: (words: { word: string; start: number; end: number }[]) => void;
  /** Звука для распознавания нет (пустой холст — немая основа): распознавание и «Голоса» выключены. */
  silent?: boolean;
  /** Сервер поменял настройки клипа (слой оригинала, слои озвучки) — перечитать их в редактор. */
  onServerChange?: () => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  // «Голоса» и «Перевод» — модули (app/modules.py): выключены — кнопок нет.
  const { on: moduleOn } = useModules();
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

  const applyFresh = (data: { lines?: SubtitleLine[]; words?: { word: string; start: number; end: number }[] }) => {
    setLines(data.lines ?? []);
    setDirty(false);
    onLines(data.lines ?? []);
    onWords?.(data.words ?? []);
    qc.invalidateQueries({ queryKey: ["plan-subtitles", clipPlanId] });
  };

  const voices = useMutation({
    mutationFn: () => clipPlansApi.separateVoices(clipPlanId, true),
    onSuccess: (data) => {
      applyFresh(data.subtitles);
      const n = Object.keys(data.counts).length;
      if (data.warning) toast.error(data.warning);
      else
        toast.success(
          n > 1
            ? `Голосов: ${n}${data.added ? ` · вернул ${data.added} потерянных слов` : ""}`
            : "Голос один — субтитры уточнены",
        );
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось разобрать голоса"),
  });

  const assign = useMutation({
    mutationFn: ({ line, speaker }: { line: SubtitleLine; speaker: string }) =>
      clipPlansApi.assignVoice(clipPlanId, line.start, line.end + 0.001, speaker),
    onSuccess: (data) => applyFresh(data.subtitles),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сменить голос"),
  });

  const speakers = query.data?.speakers ?? {};
  const hasVoices = lines.some((l) => l.speaker && l.speaker !== "A");
  const counts: Record<string, number> = {};
  for (const w of query.data?.words ?? []) counts[w.speaker || "A"] = (counts[w.speaker || "A"] ?? 0) + 1;
  // Клик по метке строки — следующий голос по кругу (A → B → C → D → A).
  const nextVoice = (line: SubtitleLine) => {
    if (!moduleOn("voices")) {
      toast.error("Модуль «Голоса в субтитрах» выключен (Настройки → Модули)");
      return;
    }
    if (dirty) {
      toast.error("Сначала сохраните правку строк");
      return;
    }
    const cur = VOICES.indexOf(line.speaker || "A");
    assign.mutate({ line, speaker: VOICES[(cur + 1) % VOICES.length] });
  };

  const busy = generate.isPending || save.isPending || voices.isPending || assign.isPending;
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
        <button
          className="btn sm"
          disabled={busy || silent}
          title={silent ? SILENT_HINT : undefined}
          onClick={() => generate.mutate()}
        >
          {generate.isPending ? "Слушаю…" : lines.length ? "↻ Распознать заново" : "🎙 Распознать речь"}
        </button>
        {moduleOn("voices") ? <button
          className="btn sm"
          disabled={busy || silent}
          onClick={() => voices.mutate()}
          title={silent ? SILENT_HINT : "Gemini слушает клип и расшифровывает каждый голос отдельно: стример, гость, донат, ролик. Перебитые чужим голосом фразы возвращаются, субтитры разных людей — разным цветом и не в одной строке. Сначала речь распознаётся точной моделью — это 1–3 минуты."}
        >
          {voices.isPending ? "Разбираю голоса…" : "Голоса"}
        </button> : null}
      </div>

      {query.data && lines.length && moduleOn("translation") ? (
        <TranslatePanel
          clipPlanId={clipPlanId}
          subs={query.data}
          dirty={dirty}
          onFresh={(data) => {
            applyFresh(data);
            onServerChange?.();
          }}
          onServerChange={onServerChange}
        />
      ) : null}


      {hasVoices ? (
        <div className="subed-voices">
          {VOICES.filter((v) => counts[v]).map((v) => (
            <span key={v} className="subed-voice" title={`${counts[v]} слов`}>
              <i style={voiceTag(v)} />
              <b>{v}</b> {speakers[v] || (v === "A" ? "главный" : "голос")}
              <span className="muted"> · {counts[v]}</span>
            </span>
          ))}
          <span className="muted subed-voices-hint">Метка у строки — сменить голос</span>
        </div>
      ) : null}

      {!lines.length && silent ? (
        <p className="muted subed-empty">{SILENT_HINT}</p>
      ) : !lines.length ? (
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
                  {hasVoices ? (
                    <button
                      className="subed-voice-tag"
                      style={voiceTag(line.speaker)}
                      title={`Голос ${line.speaker || "A"}${speakers[line.speaker || "A"] ? ` — ${speakers[line.speaker || "A"]}` : ""}. Клик — следующий голос`}
                      disabled={busy}
                      onClick={() => nextVoice(line)}
                    >
                      {line.speaker || "A"}
                    </button>
                  ) : null}
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

const LANG_NAMES: Record<string, string> = {
  en: "английский", ja: "японский", ko: "корейский", zh: "китайский", de: "немецкий", fr: "французский",
  es: "испанский", it: "итальянский", pt: "португальский", pl: "польский", uk: "украинский", tr: "турецкий",
};
const langName = (code?: string) => (code ? LANG_NAMES[code] || code : "не известен");

/**
 * Перевод иноязычного клипа на русский (Gemini, бесплатно) и «Озвучка RU» (платно).
 * Показывается, если речь не русская или перевод уже есть. Оценка озвучки бесплатна; синтез —
 * только по кнопке с суммой и подтверждением, и только если владелец включил DUBBING_ENABLED.
 */
function TranslatePanel({
  clipPlanId,
  subs,
  dirty,
  onFresh,
  onServerChange,
}: {
  clipPlanId: number;
  subs: PlanSubtitles;
  dirty: boolean;
  onFresh: (data: PlanSubtitles) => void;
  onServerChange?: () => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const dubOn = useModuleOn("dubbing"); // «Озвучка RU» — свой модуль (app/modules.py)
  const tr = subs.translation;
  const source = tr?.source_language || subs.language;
  const mode: TranslationMode = tr ? tr.mode : "off";
  const russian = !tr && ["ru", "rus", "russian"].includes(String(subs.language || "").toLowerCase());
  const [estimate, setEstimate] = useState<DubResult | null>(null);

  const translate = useMutation({
    mutationFn: ({ next, again }: { next: TranslationMode; again?: boolean }) =>
      clipPlansApi.translateSubtitles(clipPlanId, next, again),
    onSuccess: (data) => {
      onFresh(data.subtitles);
      setEstimate(null);
      toast.success(
        data.mode === "off"
          ? "Вернул оригинал"
          : `Перевод на русский: ${data.lines} строк${data.layer ? ` · ${data.layer}` : ""}${data.note ? ` · ${data.note}` : ""}`,
      );
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось перевести"),
  });

  const dub = useMutation({
    mutationFn: (body: { provider?: string; confirm?: boolean; remove?: boolean }) => clipPlansApi.dub(clipPlanId, body),
    onSuccess: (data, body) => {
      if (data.status === "estimate") {
        setEstimate(data);
        return;
      }
      qc.invalidateQueries({ queryKey: ["plan-subtitles", clipPlanId] });
      qc.invalidateQueries({ queryKey: ["montage-assets"] });
      onServerChange?.();
      setEstimate(null);
      if (data.status === "removed") toast.success("Озвучка снята");
      else
        toast.success(
          `${body.provider === "none" ? "Сухой прогон" : "Озвучка RU"} готова: ${data.dub?.layers?.length ?? 0} слоёв` +
            (body.provider === "none" ? "" : ` · ≈ ${(data.dub?.cost_rub ?? 0).toFixed(2)} ₽`) +
            (data.dub?.overflows ? ` · ${data.dub.overflows} фраз не влезли — сократите их` : ""),
        );
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Озвучка не удалась"),
  });

  if (russian) return null;
  const busy = translate.isPending || dub.isPending;
  const est = estimate?.estimate;
  const done = tr?.dub;
  const askPaid = () => {
    if (!est) return;
    const ok = window.confirm(
      `Озвучка платная (Polza TTS, ${est.model}, голос ${est.voice}).\n` +
        `${est.chars} символов ≈ ${est.rub.toFixed(2)} ₽ (цена от ${est.price_checked}, потолок ${est.budget_rub.toFixed(0)} ₽).\n\nОзвучить?`,
    );
    if (ok) dub.mutate({ provider: "polza", confirm: true });
  };
  return (
    <div className="subed-tr">
      <div className="subed-tr-row">
        <span className="muted">
          Речь: <b>{langName(source)}</b>
        </span>
        <select
          className="input"
          value={mode}
          disabled={busy || dirty}
          title={dirty ? "Сначала сохраните правку строк" : "Только на русский: перевод по фразам, время фраз оригинала"}
          onChange={(e) => translate.mutate({ next: e.target.value as TranslationMode })}
        >
          <option value="off">Оригинал</option>
          <option value="ru">Русский</option>
          <option value="both">Русский + оригинал</option>
        </select>
        {tr ? (
          <button
            className="btn sm"
            disabled={busy || dirty}
            title="Перевести заново (Gemini). Ручные правки русских строк пропадут"
            onClick={() => translate.mutate({ next: mode, again: true })}
          >
            {translate.isPending ? "Перевожу…" : "↻"}
          </button>
        ) : null}
      </div>
      {tr && dubOn ? (
        <div className="subed-dub">
          <div className="subed-tr-row">
            <b>Озвучка RU</b>
            <button
              className="btn sm"
              disabled={busy}
              onClick={() => dub.mutate({ provider: "polza" })}
              title="Посчитать стоимость — бесплатно, ничего не синтезирует"
            >
              {dub.isPending && !est ? "Считаю…" : "Оценить"}
            </button>
            <button
              className="btn sm"
              disabled={busy}
              onClick={() => dub.mutate({ provider: "none", confirm: true })}
              title="Бесплатно: тон вместо речи — проверить окна фраз и приглушение оригинала"
            >
              Сухой прогон
            </button>
            {done ? (
              <button className="btn sm" disabled={busy} onClick={() => dub.mutate({ remove: true })}>
                Снять
              </button>
            ) : null}
          </div>
          {est ? (
            <div className="subed-tr-row">
              <span className="muted">
                {est.chars} символов · {est.phrases} фраз ≈ <b>{est.rub.toFixed(2)} ₽</b> ({est.model}, цена от{" "}
                {est.price_checked})
              </span>
              <button
                className="btn primary sm"
                disabled={busy || !est.enabled || est.over_budget}
                title={
                  !est.enabled
                    ? "Озвучка выключена: владелец включает её DUBBING_ENABLED=1 в .env"
                    : est.over_budget
                      ? `Дороже потолка ${est.budget_rub} ₽`
                      : "Платно: спросит подтверждение с суммой"
                }
                onClick={askPaid}
              >
                {dub.isPending ? "Озвучиваю…" : `Озвучить ≈ ${est.rub.toFixed(2)} ₽`}
              </button>
            </div>
          ) : null}
          {est && !est.enabled ? (
            <p className="muted subed-tr-hint">Платная озвучка выключена (DUBBING_ENABLED=0) — включает владелец.</p>
          ) : null}
          {done ? (
            <p className="muted subed-tr-hint">
              Готово: {done.provider === "none" ? "сухой прогон" : `${done.voice}, ≈ ${(done.cost_rub ?? 0).toFixed(2)} ₽`} ·
              слои {done.layers?.join(", ")}
              {done.overflows ? ` · не влезли: ${done.overflows}` : ""}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
