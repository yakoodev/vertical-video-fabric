/**
 * «Разобрать на слои» в карточке снимка страницы (PagesPanel): сервис находит на странице блоки — шапку,
 * заголовки, кнопки, цены, карточки товара — и снимает каждый отдельной прозрачной картинкой
 * (app/webpage_layers.py). Здесь их видно сеткой: отмечаешь нужные, выбираешь раскладку и ставишь на таймлайн —
 * фон страницы и блоки отдельными слоями, они входят по очереди сверху вниз.
 */
import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { webPagesApi, type PageBlocks, type PageRegion } from "@/api/webPages";
import { useToast } from "@/components/Toast";
import type { AddLayers } from "@/pages/workspace/motion/LibraryPanels";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

const REGIONS: { value: PageRegion; label: string }[] = [
  { value: "auto", label: "обрезанное окно (или вся)" },
  { value: "screen", label: "первый экран" },
  { value: "page", label: "вся страница" },
];

export function PageBlocksPicker({
  pageId,
  version,
  clipId,
  onAdd,
}: {
  pageId: number;
  version: number;
  clipId?: number | null;
  onAdd: AddLayers;
}) {
  const toast = useToast();
  const [region, setRegion] = useState<PageRegion>("auto");
  const [found, setFound] = useState<PageBlocks | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [asText, setAsText] = useState<Set<string>>(new Set());
  const [plate, setPlate] = useState(true);
  const [layout, setLayout] = useState<"fit_width" | "as_page">("fit_width");
  const scan = useMutation({
    mutationFn: () => webPagesApi.blocks(pageId, version, region),
    onSuccess: (r) => {
      setFound(r);
      setPicked(new Set(r.blocks.map((b) => b.id)));
      setAsText(new Set());
      if (!r.blocks.length) toast.push("Блоков не нашлось — попробуйте другую область", "info");
    },
    onError: (e) => toast.error(errText(e, "Не получилось разобрать страницу")),
  });
  const place = useMutation({
    mutationFn: () =>
      webPagesApi.toLayers(pageId, {
        version,
        region,
        blocks: [...picked],
        layout,
        plate,
        as_text: [...asText].filter((id) => picked.has(id)),
        clip_id: clipId ?? null,
      }),
    onSuccess: (r) => {
      onAdd(r.layers);
      const texts = r.blocks.filter((b) => b.as === "text").length;
      toast.success(`На таймлайне: ${r.layers.length} ${plural(r.layers.length)}${texts ? `, текстом — ${texts}` : ""}`);
      for (const note of r.notes) toast.push(note, "info");
    },
    onError: (e) => toast.error(errText(e, "Не получилось поставить слои")),
  });
  const names = useMemo(() => new Map((found?.blocks ?? []).map((b) => [b.id, b.name])), [found]);
  const toggle = (set: Set<string>, id: string, on: boolean) => {
    const next = new Set(set);
    if (on) next.add(id);
    else next.delete(id);
    return next;
  };
  const count = picked.size + (plate ? 1 : 0);
  return (
    <div className="wp-layers">
      <div className="lib-row">
        <select className="input" value={region} onChange={(e) => setRegion(e.target.value as PageRegion)} title="Какую часть страницы разбирать">
          {REGIONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
        <button type="button" className="btn" disabled={scan.isPending} onClick={() => scan.mutate()}>
          {scan.isPending ? "Разбираю… (до минуты)" : "Разобрать на слои"}
        </button>
      </div>
      {found && found.blocks.length ? (
        <>
          <p className="muted wp-note">
            Нашлось блоков: {found.blocks.length}. Каждый станет своим слоем — его можно двигать и анимировать отдельно.
          </p>
          <div className="lib-row wp-layers-bulk">
            <button type="button" className="btn ghost" onClick={() => setPicked(new Set(found.blocks.map((b) => b.id)))}>
              Отметить все
            </button>
            <button type="button" className="btn ghost" onClick={() => setPicked(new Set())}>
              Снять все
            </button>
          </div>
          <div className="wp-blocks" role="list">
            {found.blocks.map((b) => (
              <div key={b.id} role="listitem" className={`wp-block${picked.has(b.id) ? " on" : ""}${b.parent ? " child" : ""}`}>
                <label className="wp-block-pick" title={b.parent ? `внутри: ${names.get(b.parent) ?? ""}` : b.name}>
                  <input type="checkbox" checked={picked.has(b.id)} onChange={(e) => setPicked((s) => toggle(s, b.id, e.target.checked))} />
                  <span className="wp-block-img">
                    <img src={b.url} alt="" loading="lazy" />
                  </span>
                  <span className="wp-block-name">{b.name}</span>
                </label>
                {b.can_text ? (
                  <label className="wp-block-text" title="Поставить живым текстом — его можно править и анимировать по словам">
                    <input type="checkbox" checked={asText.has(b.id)} onChange={(e) => setAsText((s) => toggle(s, b.id, e.target.checked))} />
                    текстом
                  </label>
                ) : null}
              </div>
            ))}
          </div>
          <label className="switch">
            <input type="checkbox" checked={plate} onChange={(e) => setPlate(e.target.checked)} />
            <span className="switch-track" />
            <span>фон страницы под блоками</span>
          </label>
          <div className="lib-row">
            <select className="input" value={layout} onChange={(e) => setLayout(e.target.value as "fit_width" | "as_page")} title="Как разложить блоки в кадре">
              <option value="fit_width">вписать в кадр</option>
              <option value="as_page">как на странице, во всю ширину</option>
            </select>
            <button type="button" className="btn primary" disabled={place.isPending || count === 0} onClick={() => place.mutate()}>
              {place.isPending ? "Ставлю…" : `На таймлайн (${count})`}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}

function plural(n: number): string {
  const d = n % 10;
  const dd = n % 100;
  if (d === 1 && dd !== 11) return "слой";
  if (d >= 2 && d <= 4 && (dd < 12 || dd > 14)) return "слоя";
  return "слоёв";
}
