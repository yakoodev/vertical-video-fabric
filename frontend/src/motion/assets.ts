/**
 * Какие картинки нужны композиции. Рендер (Node) грузит их заранее — во время
 * отрисовки кадра `getImage` синхронный; превью по этому же списку ставит
 * загрузку и перерисовывает кадр, когда картинка пришла.
 */
import { emojiRef, graphemes, isEmoji } from "./fonts";
import type { Layer, VideoLayer } from "./types";

function emojiIn(text: string | undefined, out: Set<string>): void {
  if (!text) return;
  for (const g of graphemes(text)) if (isEmoji(g)) out.add(emojiRef(g));
}

/** Все ссылки на картинки: asset_id, `sticker:…`, `emoji:…` (в том числе эмодзи внутри текста). */
export function collectImageRefs(layers: Layer[]): string[] {
  const out = new Set<string>();
  for (const layer of layers || []) {
    switch (layer.type) {
      case "image": {
        const ref = layer.props.asset_id || layer.props.src;
        if (ref) out.add(String(ref));
        break;
      }
      case "emoji":
        if (layer.props.char) out.add(emojiRef(layer.props.char));
        break;
      case "text":
        emojiIn(layer.props.text, out);
        break;
      case "counter":
        emojiIn(`${layer.props.prefix ?? ""} ${layer.props.suffix ?? ""}`, out);
        break;
      case "callout":
        emojiIn(layer.props.title, out);
        emojiIn(layer.props.subtitle, out);
        if (layer.props.icon) out.add(emojiRef(layer.props.icon));
        break;
      default:
        break;
    }
  }
  return [...out];
}

/** Ссылки на звуки (`audio`-слои) — для микшера рендера. */
export function collectAudioRefs(layers: Layer[]): string[] {
  const out = new Set<string>();
  for (const layer of layers || []) if (layer.type === "audio" && layer.props.asset_id) out.add(String(layer.props.asset_id));
  return [...out];
}

/** Видимые видео-слои с файлом — для подготовки кадров в рендере и загрузки в превью. */
export function collectVideoLayers(layers: Layer[]): VideoLayer[] {
  return (layers || []).filter((l): l is VideoLayer => l.type === "video" && !l.hidden && Boolean(l.props?.asset_id));
}
