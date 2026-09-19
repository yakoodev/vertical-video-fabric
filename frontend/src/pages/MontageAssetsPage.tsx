import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { montageAssetsApi, type MontageAsset } from "@/api/assets";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState, ErrorState, Loading, PageHead, formatDuration } from "@/components/ui";

const KIND_LABEL: Record<MontageAsset["kind"], string> = {
  image: "Картинка",
  gif: "GIF",
  video: "Видео",
  audio: "Звук",
};

function AssetCard({ asset, onDelete }: { asset: MontageAsset; onDelete: (a: MontageAsset) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState(asset.label);
  const [description, setDescription] = useState(asset.description);
  const [tags, setTags] = useState(asset.tags);

  const save = useMutation({
    mutationFn: (patch: Partial<Pick<MontageAsset, "label" | "description" | "tags">>) =>
      montageAssetsApi.update(asset.id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.montageAssets }),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });
  const commit = (field: "label" | "description" | "tags", value: string) => {
    if (value.trim() !== (asset[field] ?? "").trim()) save.mutate({ [field]: value });
  };

  return (
    <div className="asset-card">
      <div className={`asset-media asset-media--${asset.kind}`}>
        {asset.kind === "image" || asset.kind === "gif" ? (
          <img src={asset.url} alt="" loading="lazy" />
        ) : asset.kind === "video" ? (
          <video src={asset.url} controls preload="metadata" playsInline />
        ) : (
          <div className="asset-audio">
            <span className="asset-audio-ico">♪</span>
            <audio src={asset.url} controls preload="none" />
          </div>
        )}
        <span className="asset-kind">{KIND_LABEL[asset.kind]}</span>
        {asset.duration_sec ? <span className="asset-dur mono">{formatDuration(asset.duration_sec)}</span> : null}
        <button className="card-del" title="Удалить файл" onClick={() => onDelete(asset)}>
          🗑
        </button>
      </div>
      <input
        className="input asset-label"
        value={label}
        maxLength={120}
        onChange={(e) => setLabel(e.target.value)}
        onBlur={() => commit("label", label)}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      />
      <textarea
        className="input asset-desc"
        rows={2}
        placeholder="Когда уместно? Напр.: «на провал, неловкую паузу», «когда чат орёт»"
        value={description}
        maxLength={500}
        onChange={(e) => setDescription(e.target.value)}
        onBlur={() => commit("description", description)}
      />
      <input
        className="input asset-tags"
        placeholder="теги: фейл, смех, шок…"
        value={tags}
        maxLength={200}
        onChange={(e) => setTags(e.target.value)}
        onBlur={() => commit("tags", tags)}
      />
    </div>
  );
}

/**
 * «Файлы для монтажа» — the owner's meme library. 🤖 ИИ-монтаж reads each file's
 * label + «когда уместно» and drops fitting ones into clips (full frame, window
 * or sound only); the editor's «Вставки» section places them by hand.
 */
export function MontageAssetsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: qk.montageAssets, queryFn: montageAssetsApi.list });
  const [pending, setPending] = useState<MontageAsset | null>(null);
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadAll = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(list.length);
    let ok = 0;
    for (const f of list) {
      try {
        await montageAssetsApi.upload(f);
        ok += 1;
      } catch (e) {
        toast.error(`${f.name}: ${e instanceof ApiError ? e.message : "не загрузился"}`);
      }
      setUploading((n) => n - 1);
    }
    qc.invalidateQueries({ queryKey: qk.montageAssets });
    if (ok) toast.success(`Загружено: ${ok}. Допишите «когда уместно» — по этому ИИ выбирает файл.`);
  };

  const del = useMutation({
    mutationFn: (id: number) => montageAssetsApi.remove(id),
    onSuccess: () => {
      toast.success("Файл удалён");
      setPending(null);
      qc.invalidateQueries({ queryKey: qk.montageAssets });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось удалить"),
  });

  const assets = query.data ?? [];
  return (
    <>
      <PageHead
        title="Файлы для монтажа"
        sub="Мемы, реакции, стикеры и звуки — 🤖 ИИ-монтаж сам вставляет подходящие в клипы"
      />
      <div
        className={`asset-drop${drag ? " on" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          void uploadAll(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/*,video/*,audio/*,.gif"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) void uploadAll(e.target.files);
            e.target.value = "";
          }}
        />
        <strong>{uploading ? `Загружаю… осталось ${uploading}` : "Перетащите файлы сюда или нажмите"}</strong>
        <span className="muted">
          картинки, GIF, короткие видео (до 8 с в клипе), звуки · можно много сразу · подпись «когда уместно» — главное
          для ИИ
        </span>
      </div>

      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !assets.length ? (
        <EmptyState
          icon="🧩"
          title="Библиотека пуста"
          hint="Закиньте пару мемов и звуков — и 🤖 ИИ-монтаж начнёт вставлять их в подходящие моменты"
        />
      ) : (
        <div className="asset-grid">
          {assets.map((a) => (
            <AssetCard key={a.id} asset={a} onDelete={setPending} />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(pending)}
        title="Удалить файл?"
        body="Файл пропадёт из библиотеки. Уже отрендеренные клипы не изменятся; во вставках клипов он будет пропущен."
        busy={del.isPending}
        onCancel={() => setPending(null)}
        onConfirm={() => pending && del.mutate(pending.id)}
      />
    </>
  );
}
