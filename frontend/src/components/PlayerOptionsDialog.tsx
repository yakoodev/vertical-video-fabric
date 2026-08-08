import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { sourcesApi, type PlayerOption } from "@/api/sources";
import type { Source } from "@/api/types";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading, formatDuration } from "@/components/ui";

// Sort "1", "2", "10" like a human would, and keep non-numeric labels alphabetical.
function byLabel(a: string, b: string): number {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return a.localeCompare(b, "ru");
}

function values(options: PlayerOption[], key: keyof PlayerOption): string[] {
  return [...new Set(options.map((o) => String(o[key] ?? "")))].filter(Boolean).sort(byLabel);
}

// Render-time fallback: a value that vanished after a narrower filter falls back
// to the first available one, so the dialog never sits on an impossible combo.
function pick(list: string[], value: string): string {
  return list.includes(value) ? value : (list[0] ?? "");
}

function Row({ label, list, value, onChange }: {
  label: string;
  list: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  if (list.length === 0) return null;
  return (
    <label className="field">
      <span>
        {label} <span className="muted">({list.length})</span>
      </span>
      <select className="input" value={value} disabled={list.length === 1} onChange={(e) => onChange(e.target.value)}>
        {list.map((item) => (
          <option key={item} value={item}>
            {item}
          </option>
        ))}
      </select>
    </label>
  );
}

export function PlayerOptionsDialog({
  url,
  quality,
  onClose,
  onDone,
}: {
  url: string;
  quality: string;
  onClose: () => void;
  onDone: (source: Source) => void;
}) {
  const toast = useToast();
  const [provider, setProvider] = useState("");
  const [season, setSeason] = useState("");
  const [episode, setEpisode] = useState("");
  const [translation, setTranslation] = useState("");

  const options = useQuery({
    queryKey: ["player-options", url],
    queryFn: () => sourcesApi.playerOptions(url),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const download = useMutation({
    mutationFn: (option: PlayerOption) => sourcesApi.ingestUrl(url, quality, option),
    onSuccess: onDone,
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось скачать выбранную серию"),
  });

  const all = options.data ?? [];
  const providers = values(all, "provider");
  const activeProvider = pick(providers, provider);
  const byProvider = all.filter((o) => !activeProvider || o.provider === activeProvider);

  const seasons = values(byProvider, "season");
  const activeSeason = pick(seasons, season);
  const bySeason = byProvider.filter((o) => !activeSeason || o.season === activeSeason);

  const episodes = values(bySeason, "episode");
  const activeEpisode = pick(episodes, episode);
  const byEpisode = bySeason.filter((o) => !activeEpisode || o.episode === activeEpisode);

  const translations = values(byEpisode, "translation");
  const activeTranslation = pick(translations, translation);
  const selected =
    byEpisode.find((o) => !activeTranslation || o.translation === activeTranslation) ?? byEpisode[0] ?? null;

  return (
    <div className="modal-backdrop" onClick={download.isPending ? undefined : onClose}>
      <div className="modal" style={{ maxWidth: 560 }} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <h3 style={{ margin: "0 0 4px" }}>Серия и озвучка</h3>
        <div className="muted" style={{ fontSize: 13, marginBottom: 14, wordBreak: "break-all" }}>{url}</div>

        {options.isLoading ? (
          <Loading />
        ) : options.isError ? (
          <ErrorState error={options.error} onRetry={() => options.refetch()} />
        ) : !all.length ? (
          <div className="muted" style={{ fontSize: 14 }}>
            Плеер не отдал список серий. Добавьте ссылку обычной кнопкой «Добавить» — сервис возьмёт то,
            что играет на странице по умолчанию.
          </div>
        ) : (
          <div style={{ display: "grid", gap: 12 }}>
            <Row label="Плеер" list={providers} value={activeProvider} onChange={setProvider} />
            <Row label="Сезон" list={seasons} value={activeSeason} onChange={setSeason} />
            <Row label="Серия" list={episodes} value={activeEpisode} onChange={setEpisode} />
            <Row label="Озвучка" list={translations} value={activeTranslation} onChange={setTranslation} />
            {selected ? (
              <div className="muted" style={{ fontSize: 13 }}>
                {[
                  selected.title,
                  selected.quality,
                  selected.duration_sec ? formatDuration(selected.duration_sec) : "",
                ]
                  .filter(Boolean)
                  .join(" · ") || "Выбранный вариант готов к скачиванию"}
              </div>
            ) : null}
          </div>
        )}

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 18 }}>
          <button className="btn ghost" onClick={onClose} disabled={download.isPending}>
            Отмена
          </button>
          <button
            className="btn primary"
            disabled={!selected || download.isPending}
            onClick={() => selected && download.mutate(selected)}
          >
            {download.isPending ? "Скачивание…" : "Скачать выбранное"}
          </button>
        </div>
      </div>
    </div>
  );
}
