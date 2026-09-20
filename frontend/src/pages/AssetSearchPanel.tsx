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

  const search = useMutation({
    mutationFn: () => montageAssetsApi.search(query.trim(), 6),
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
          <p className="muted asearch-note">
            ИИ ищет прежде всего свободные источники (Wikimedia Commons, openclipart, CC0). Ничего не скачивается, пока
            вы не нажмёте «Добавить». Проверяйте права: за то, что попадёт в опубликованный клип, отвечаете вы.
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
