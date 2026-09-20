import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { montageAssetsApi, type AssetSearchResult } from "@/api/assets";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

/**
 * 🔎 ИИ ищет картинку в интернете. Поиск ничего не скачивает: он показывает
 * кандидатов с источником и лицензией, файл попадает в библиотеку только по
 * кнопке «Добавить» — что публиковать, решает владелец.
 */
export function AssetSearchPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AssetSearchResult[]>([]);
  const [adding, setAdding] = useState<string | null>(null);
  // По умолчанию ищем только там, откуда картинку можно взять законно.
  const [freeOnly, setFreeOnly] = useState(true);

  const search = useMutation({
    mutationFn: () => montageAssetsApi.search(query.trim(), 6, freeOnly),
    onSuccess: (r) => {
      setResults(r.results);
      if (!r.results.length) toast.push("Ничего подходящего не нашлось — попробуйте другой запрос", "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Поиск не удался"),
  });

  const add = async (item: AssetSearchResult) => {
    setAdding(item.url);
    try {
      await montageAssetsApi.fromUrl(item.url, item.title, item.license ? `Источник: ${item.source} · ${item.license}` : "");
      qc.invalidateQueries({ queryKey: qk.montageAssets });
      toast.success("Добавлено в библиотеку — ИИ сейчас опишет файл");
      setResults((prev) => prev.filter((r) => r.url !== item.url));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось скачать");
    } finally {
      setAdding(null);
    }
  };

  return (
    <div className="asearch">
      <button className="btn sm" onClick={() => setOpen((v) => !v)}>
        🔎 Найти картинку в интернете {open ? "▾" : "▸"}
      </button>
      {open ? (
        <div className="asearch-body">
          <div className="asearch-head">
            <input
              className="input"
              placeholder="Что нужно: «стрелка вниз», «табличка стоп», «грустный смайлик»…"
              value={query}
              maxLength={200}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && query.trim() && search.mutate()}
            />
            <button className="btn primary" disabled={search.isPending || query.trim().length < 2} onClick={() => search.mutate()}>
              {search.isPending ? "Ищу…" : "Искать"}
            </button>
          </div>
          <label className="switch">
            <input type="checkbox" checked={freeOnly} onChange={(e) => setFreeOnly(e.target.checked)} />
            <span className="switch-track" />
            <span>только свободные источники (Wikimedia, openclipart, CC0)</span>
          </label>
          <p className="muted asearch-note">
            {freeOnly
              ? "Ищем там, откуда картинку можно взять законно. Мемов с таких сайтов мало — снимите галочку, чтобы искать по всему интернету."
              : "Ищем по всему интернету, включая мем-сайты. Это чужие картинки: смотрите источник и решайте сами — за опубликованный клип отвечаете вы."}{" "}
            Ничего не скачивается, пока вы не нажмёте «Добавить».
          </p>
          {results.length ? (
            <div className="asearch-grid">
              {results.map((item) => (
                <div key={item.url} className="asearch-item">
                  <img
                    src={`/media/asset-preview?url=${encodeURIComponent(item.url)}`}
                    alt=""
                    loading="lazy"
                    onError={(e) => ((e.currentTarget as HTMLImageElement).style.opacity = "0.25")}
                  />
                  <span className="asearch-title">{item.title || "без описания"}</span>
                  <span className="asearch-src muted">
                    {item.source}
                    {item.license ? ` · ${item.license}` : ""}
                  </span>
                  <div className="asearch-acts">
                    <a className="btn sm" href={item.url} target="_blank" rel="noreferrer" title="Открыть источник">
                      ↗
                    </a>
                    <button className="btn primary sm" disabled={adding === item.url} onClick={() => void add(item)}>
                      {adding === item.url ? "…" : "Добавить"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
