import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { montageAssetsApi, type MontageAsset } from "@/api/assets";
import { qk } from "@/api/keys";
import { formatDuration } from "@/components/ui";

const KIND_ICON: Record<MontageAsset["kind"], string> = { image: "🖼", gif: "🎞", video: "▶", audio: "♪" };

/**
 * Библиотека прямо в редакторе: мем перетаскивается мышью на дорожку «Вставки».
 * Раньше вставку можно было добавить только через выпадающий список с числами —
 * не видя, что это за файл.
 */
export function AssetDrawer({ onAdd, clipId }: { onAdd: (assetId: number) => void; clipId?: number | null }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  // Только разрешённое клипу: общие файлы и файлы его проекта (без клипа — общие).
  const filter = clipId ? { clipId } : { shared: true };
  const assets = useQuery({
    queryKey: qk.montageAssetsScoped(filter),
    queryFn: () => montageAssetsApi.listScoped(filter),
    staleTime: 60_000,
    select: (list) => list.filter((a) => !a.in_clip_only),
  });

  const needle = q.trim().toLowerCase();
  const shown = (assets.data ?? []).filter(
    (a) => !needle || `${a.label} ${a.description} ${a.tags}`.toLowerCase().includes(needle),
  );

  return (
    <div className={`adrawer${open ? " open" : ""}`}>
      <button className="btn sm adrawer-toggle" onClick={() => setOpen((v) => !v)}>
        🧩 Библиотека {open ? "▾" : "▸"} <span className="muted">{assets.data?.length ?? 0}</span>
      </button>
      {open ? (
        <div className="adrawer-body">
          <div className="adrawer-head">
            <input
              className="input"
              placeholder="Поиск: фейл, деньги, смех…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Link className="btn sm" to="/assets" title="Загрузить новые файлы">
              + Файлы
            </Link>
          </div>
          {!shown.length ? (
            <p className="muted adrawer-empty">
              {assets.data?.length ? "Ничего не нашлось" : "Библиотека пуста — загрузите мемы на странице «Файлы для монтажа»"}
            </p>
          ) : (
            <div className="adrawer-grid">
              {shown.map((a) => (
                <div
                  key={a.id}
                  className="adrawer-item"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData("application/x-asset-id", String(a.id));
                    e.dataTransfer.effectAllowed = "copy";
                  }}
                  onDoubleClick={() => onAdd(a.id)}
                  title={`${a.label}${a.description ? ` — ${a.description}` : ""}\n\nПеретащите на дорожку «Вставки» или двойной клик — поставить на текущий момент`}
                >
                  {a.kind === "image" || a.kind === "gif" ? (
                    <img src={`${a.url}/thumb`} alt="" loading="lazy" decoding="async" onError={(ev) => { ev.currentTarget.src = a.url; }} />
                  ) : (
                    <span className="adrawer-ico">{KIND_ICON[a.kind]}</span>
                  )}
                  <span className="adrawer-name">{a.label}</span>
                  {a.duration_sec ? <span className="adrawer-dur mono">{formatDuration(a.duration_sec)}</span> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
