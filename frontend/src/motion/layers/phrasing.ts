/**
 * Страницы субтитров по фразам, а не по счёту слов — зеркало app/subtitles/phrasing.py (ASS):
 * тот же алгоритм, те же цены и словари; обе стороны сверяются с tests/fixtures/phrase_pages.json.
 *
 * Фразу (между паузами и концом предложения — это режет вызывающий) делим на страницы ≤ cap слов
 * так, чтобы разрыв шёл по запятой/тире или паузе ≥ 150 мс, страница не кончалась предлогом или
 * союзом («в, и, так, но, что, как, если…») и не начиналась с «же, ли, бы», страница из 2+ слов
 * держалась на экране ≥ 1 с, а страницы были примерно поровну.
 */

export const PAUSE_HINT_SEC = 0.15;
export const MIN_PAGE_SEC = 1.0;
export const MIN_WORD_SEC = 0.8;

const PAGE_COST = 1.0;
const BALANCE_COST = 1.2;
const ORPHAN_COST = 1.2;
const ORPHAN_PUNCT_COST = 0.4;
const SHORT_COST = 2.0;
const PUNCT_BONUS = 1.5;
const PAUSE_BONUS_MIN = 0.4;
const PAUSE_BONUS_MAX = 0.9;
const FUNCTION_COST = 3.0;
const WEAK_COST = 0.6;
const ATTACH_COST = 2.0;

export const FUNCTION_WORDS: readonly string[] = (
  "в во на с со к ко о об обо от ото до по за из изо у без безо для при про через над надо под подо перед пред " +
  "между сквозь и а но или либо что чтобы чтоб как так если когда пока хотя будто словно ни не то ну " +
  "a an the and or but of to in on at for with from by if so as than"
).split(" ");
export const WEAK_WORDS: readonly string[] = (
  "я ты он она оно мы вы они мой моя моё мое мои твой твоя твои наш наша наши ваш ваша ваши этот эта эти тот та те " +
  "вот очень самый самая самое такой такая такое такие i you we they my your our their this these"
).split(" ");
export const ATTACH_WORDS: readonly string[] = "же ли бы б ль ж".split(" ");

const FUNCTION = new Set(FUNCTION_WORDS.map((w) => w.replace(/ё/g, "е")));
const WEAK = new Set(WEAK_WORDS.map((w) => w.replace(/ё/g, "е")));
const ATTACH = new Set(ATTACH_WORDS);

const EDGE = /^[\s.,!?;:…«»"“”„()[\]—–-]+|[\s.,!?;:…«»"“”„()[\]—–-]+$/g;
const CLOSERS = /["'»”)\]]+$/;
const PUNCT_END = /[,;:—–-]$/;

/** Слово для словарей: без знаков по краям, строчными, ё → е. */
export function bareWord(word: string): string {
  return String(word ?? "").replace(EDGE, "").toLowerCase().replace(/ё/g, "е");
}

/** Запятая, точка с запятой, двоеточие или тире в конце слова (кавычки и скобки после — не в счёт). */
export function endsWithPausePunct(word: string): boolean {
  return PUNCT_END.test(String(word ?? "").trimEnd().replace(CLOSERS, ""));
}

/** Цена разрыва страницы после слова `text` (перед `next`, пауза `gap` с): меньше — лучше. */
export function breakCost(text: string, next: string, gap: number): number {
  let cost = 0;
  if (endsWithPausePunct(text)) cost -= PUNCT_BONUS;
  if (gap >= PAUSE_HINT_SEC) cost -= Math.min(PAUSE_BONUS_MAX, PAUSE_BONUS_MIN + (gap - PAUSE_HINT_SEC) * 1.5);
  const word = bareWord(text);
  if (FUNCTION.has(word)) cost += FUNCTION_COST;
  else if (WEAK.has(word)) cost += WEAK_COST;
  if (ATTACH.has(bareWord(next))) cost += ATTACH_COST;
  return cost;
}

/**
 * Разбить фразу на страницы ≤ `cap` слов; `fits(i, j)` — слова i..j-1 помещаются в строки страницы
 * (одно слово — всегда можно). Вернуть номера слов по страницам.
 */
export function splitPhrase(
  texts: readonly string[],
  starts: readonly number[],
  ends: readonly number[],
  cap: number,
  fits?: ((i: number, j: number) => boolean) | null,
  hold = 0,
): number[][] {
  const n = texts.length;
  if (!n) return [];
  cap = Math.max(1, Math.floor(cap));
  const count = Math.ceil(n / cap);
  const ideal = n / count;
  const best = new Array<number>(n + 1).fill(Infinity);
  const prev = new Array<number>(n + 1).fill(0);
  best[0] = 0;
  const breaks: number[] = [];
  for (let k = 0; k < n - 1; k++) breaks.push(breakCost(texts[k], texts[k + 1], starts[k + 1] - ends[k]));
  for (let j = 1; j <= n; j++) {
    for (let i = j - 1; i >= Math.max(0, j - cap); i--) {
      const size = j - i;
      if (size > 1 && fits && !fits(i, j)) break; // больше слов тем более не влезет
      if (best[i] === Infinity) continue;
      let cost = best[i] + PAGE_COST;
      const dev = (size - ideal) / ideal;
      cost += BALANCE_COST * dev * dev * (dev > 0 ? 1.0 : 0.6);
      if (size === 1 && n > 1) cost += endsWithPausePunct(texts[i]) ? ORPHAN_PUNCT_COST : ORPHAN_COST;
      if (n > 1) {
        const shown = ends[j - 1] - starts[i] + hold;
        const least = size >= 2 ? MIN_PAGE_SEC : MIN_WORD_SEC;
        if (shown < least) cost += SHORT_COST * (least - shown);
      }
      if (j < n) cost += breaks[j - 1];
      if (cost < best[j] - 1e-9) {
        best[j] = cost;
        prev[j] = i;
      }
    }
  }
  const pages: number[][] = [];
  for (let j = n; j > 0; ) {
    const i = prev[j];
    const page: number[] = [];
    for (let k = i; k < j; k++) page.push(k);
    pages.push(page);
    j = i;
  }
  return pages.reverse();
}
