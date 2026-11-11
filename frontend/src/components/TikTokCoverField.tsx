/** Поле «Обложка TikTok, секунда» карточки публикации: пусто — обложка не задаётся (выбранная обложка клипа
 *  или кадр TikTok по умолчанию), иначе кадр ролика на этой секунде (app/providers/tiktok.py). */
export function TikTokCoverField({
  value,
  onChange,
  maxSec,
}: {
  value: string;
  onChange: (value: string) => void;
  maxSec?: number;
}) {
  return (
    <label className="field">
      <span>Обложка TikTok, секунда</span>
      <input
        className="input"
        type="number"
        inputMode="decimal"
        min={0}
        max={maxSec && maxSec > 0 ? Math.floor(maxSec * 10) / 10 : 3600}
        step={0.1}
        placeholder="как в TikTok"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        title="Кадр ролика на этой секунде станет обложкой в TikTok. Пусто — выбранная обложка клипа или кадр TikTok по умолчанию."
      />
    </label>
  );
}

/** Значение поля → ``cover_sec`` запроса: пусто или не число — не задано. */
export function coverSecValue(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const sec = Number(value.replace(",", "."));
  return Number.isFinite(sec) && sec >= 0 ? sec : undefined;
}
