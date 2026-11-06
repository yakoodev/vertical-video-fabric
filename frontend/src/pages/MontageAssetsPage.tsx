import { useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { montageAssetsApi, type MontageAsset } from "@/api/assets";
import { qk } from "@/api/keys";
import { projectsApi, type ChannelProject } from "@/api/projects";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { VideoThumb } from "@/components/VideoThumb";
import { ShowMore, useShowMore } from "@/components/ShowMore";
import { EmptyState, ErrorState, Loading, PageHead, formatDuration } from "@/components/ui";
import { AssetSearchPanel } from "@/pages/AssetSearchPanel";
import { AssetGeneratePanel } from "@/pages/AssetGeneratePanel";
import { useModuleOn, useModules } from "@/hooks/useModules";
import { SECTIONS } from "@/lib/tabs";

type AssetTab = "shared" | "project" | "all";
const PROJECT_ASSET_TABS: [AssetTab, string][] = [
  ["project", "Файлы проекта"],
  ["shared", "Общие"],
];
const ASSET_TABS = SECTIONS.files.tabs.map((t): [AssetTab, string] => [t.key as AssetTab, t.label]);

const KIND_LABEL: Record<MontageAsset["kind"], string> = {
  image: "Картинка",
  gif: "GIF",
  video: "Видео",
  audio: "Звук",
};

/** Уменьшенная картинка; GIF оживает под курсором, чтобы сотни файлов не качались разом. */
function AssetPicture({ asset }: { asset: MontageAsset }) {
  const [src, setSrc] = useState(`${asset.url}/thumb`);
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      onMouseEnter={asset.kind === "gif" ? () => setSrc(asset.url) : undefined}
      onError={() => setSrc(asset.url)}
    />
  );
}

function AssetCard({
  asset,
  onDelete,
  projects,
}: {
  asset: MontageAsset;
  onDelete: (a: MontageAsset) => void;
  projects: ChannelProject[];
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState(asset.label);
  const [description, setDescription] = useState(asset.description);
  const [tags, setTags] = useState(asset.tags);
  const assetAiOn = useModuleOn("asset_ai");

  const save = useMutation({
    mutationFn: (patch: Partial<Pick<MontageAsset, "label" | "description" | "tags">>) =>
      montageAssetsApi.update(asset.id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.montageAssets }),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });
  const move = useMutation({
    mutationFn: (projectId: number | null) => montageAssetsApi.update(asset.id, { project_id: projectId }),
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: qk.montageAssets });
      toast.success(a.project_id == null ? "Файл теперь общий" : "Файл перенесён в проект");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось перенести"),
  });
  const describe = useMutation({
    mutationFn: () => montageAssetsApi.describe(asset.id),
    onSuccess: (a) => {
      setLabel(a.label);
      setDescription(a.description);
      setTags(a.tags);
      qc.invalidateQueries({ queryKey: qk.montageAssets });
      toast.success("ИИ описал файл");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось описать"),
  });

  const commit = (field: "label" | "description" | "tags", value: string) => {
    if (value.trim() !== (asset[field] ?? "").trim()) save.mutate({ [field]: value });
  };

  return (
    <div className="asset-card">
      <div className={`asset-media asset-media--${asset.kind}`}>
        {asset.kind === "image" || asset.kind === "gif" ? (
          <AssetPicture asset={asset} />
        ) : asset.kind === "video" ? (
          <VideoThumb src={asset.url} thumb={`${asset.url}/thumb`} />
        ) : (
          <div className="asset-audio">
            <span className="asset-audio-ico">♪</span>
            <audio src={asset.url} controls preload="none" />
          </div>
        )}
        <span className="asset-kind">{KIND_LABEL[asset.kind]}</span>
        {asset.duration_sec ? <span className="asset-dur mono">{formatDuration(asset.duration_sec)}</span> : null}
        {asset.project_id != null ? (
          <span className="asset-proj">
            {projects.find((p) => p.id === asset.project_id)?.name ?? `проект #${asset.project_id}`}
          </span>
        ) : null}
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
      {assetAiOn ? (
        <button
          className="btn sm asset-ai"
          disabled={describe.isPending}
          onClick={() => describe.mutate()}
          title="ИИ посмотрит файл и напишет, что на нём и когда уместно"
        >
          {describe.isPending ? "✨ Смотрю…" : description.trim() ? "✨ Переписать" : "✨ Описать через ИИ"}
        </button>
      ) : null}
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
      <select
        className="input asset-owner"
        title="Чей файл: общий видят все клипы, файл проекта — только клипы этого проекта"
        value={asset.project_id ?? ""}
        disabled={move.isPending}
        onChange={(e) => move.mutate(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">Общий файл</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            Проект: {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * «Файлы для монтажа» — the owner's meme library. 🤖 ИИ-монтаж reads each file's
 * label + «когда уместно» and drops fitting ones into clips (full frame, window
 * or sound only); the editor's «Вставки» section places them by hand.
 */
/** «Общие файлы» (/files) или вкладка «Файлы» проекта (fixedProject: файлы проекта и общие). */
export function MontageAssetsPage({ projectId: fixedProject }: { projectId?: number } = {}) {
  const qc = useQueryClient();
  const toast = useToast();
  // Вкладки в адресе. Внутри проекта ?tab= занят вкладкой проекта — там свой параметр sub.
  const [params, setParams] = useSearchParams();
  const tab: AssetTab = fixedProject
    ? params.get("sub") === "shared" ? "shared" : "project"
    : params.get("tab") === "project" ? "project" : params.get("tab") === "all" ? "all" : "shared";
  const projects = useQuery({ queryKey: qk.channelProjects, queryFn: projectsApi.list, staleTime: 60_000 });
  const projectList = useMemo(() => projects.data ?? [], [projects.data]);
  const asked = fixedProject ?? (Number(params.get("project")) || null);
  const projectId = tab === "project" ? (asked ?? projectList[0]?.id ?? null) : null;
  const filter = useMemo(
    () => (tab === "project" ? { projectId } : tab === "all" ? {} : { shared: true }),
    [tab, projectId],
  );
  const showTab = (next: AssetTab, project?: number | null) =>
    fixedProject
      ? setParams((prev) => {
          const p = new URLSearchParams(prev);
          if (next === "shared") p.set("sub", "shared");
          else p.delete("sub");
          return p;
        }, { replace: true })
      : setParams(
      next === "shared"
        ? {}
        : next === "all"
          ? { tab: "all" }
          : { tab: "project", ...(project ? { project: String(project) } : {}) },
    );
  const query = useQuery({
    queryKey: qk.montageAssetsScoped(filter),
    queryFn: () => montageAssetsApi.listScoped(filter),
    enabled: tab !== "project" || projectId != null,
  });
  // Поиск картинок и ИИ-описание/генерация — модули (app/modules.py).
  const { on: moduleOn } = useModules();
  const [pending, setPending] = useState<MontageAsset | null>(null);
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [describing, setDescribing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadAll = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(list.length);
    let ok = 0;
    for (const f of list) {
      try {
        // На вкладке проекта файл становится файлом этого проекта, на остальных — общим.
        await montageAssetsApi.upload(f, "", "", "", { projectId });
        ok += 1;
      } catch (e) {
        toast.error(`${f.name}: ${e instanceof ApiError ? e.message : "не загрузился"}`);
      }
      setUploading((n) => n - 1);
    }
    qc.invalidateQueries({ queryKey: qk.montageAssets });
    if (ok) toast.success(`Загружено: ${ok} — ИИ сейчас сам опишет, что на файлах (обновится через пару секунд)`);
    // The server describes each upload in the background; pick the texts up.
    setTimeout(() => qc.invalidateQueries({ queryKey: qk.montageAssets }), 6000);
    setTimeout(() => qc.invalidateQueries({ queryKey: qk.montageAssets }), 15000);
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
  const shown = useShowMore(assets, `${tab}:${projectId}`);
  const undescribed = assets.filter((a) => !a.description.trim());
  return (
    <>
      {fixedProject ? null : (
        <PageHead
          title="Общие файлы"
          sub="Мемы, реакции, стикеры и звуки: 🤖 ИИ-монтаж сам вставляет подходящие в клипы. Клипу доступны общие файлы и файлы его проекта"
        />
      )}
      <div className="asset-scope">
        <nav className={fixedProject ? "pills" : "pipeline"}>
          {(fixedProject ? PROJECT_ASSET_TABS : ASSET_TABS).map(([key, label]) => (
            <button
              key={key}
              className={fixedProject ? `pill${tab === key ? " on" : ""}` : `tab${tab === key ? " active" : ""}`}
              onClick={() => showTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        {tab === "project" && !fixedProject ? (
          <select
            className="input"
            aria-label="Проект"
            value={projectId ?? ""}
            onChange={(e) => showTab("project", Number(e.target.value) || null)}
          >
            {projectList.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      {undescribed.length && moduleOn("asset_ai") ? (
        <div className="asset-hint">
          <span>
            <b>{undescribed.length}</b> файл(ов) без описания — ИИ-монтаж их почти не использует.
          </span>
          <button
            className="btn primary sm"
            disabled={describing}
            onClick={async () => {
              setDescribing(true);
              try {
                const r = await montageAssetsApi.describeMissing(undescribed.map((a) => a.id));
                toast.success(`Описано: ${r.described}${r.failed ? `, не вышло: ${r.failed}` : ""}`);
                qc.invalidateQueries({ queryKey: qk.montageAssets });
              } catch (e) {
                toast.error(e instanceof ApiError ? e.message : "Не удалось описать");
              } finally {
                setDescribing(false);
              }
            }}
          >
            {describing ? "✨ Описываю…" : `✨ Описать все (${undescribed.length})`}
          </button>
        </div>
      ) : null}
      {moduleOn("web_images") || moduleOn("asset_ai") ? (
        <div className="asset-ai-row">
          {moduleOn("web_images") ? <AssetSearchPanel projectId={projectId} /> : null}
          {moduleOn("asset_ai") ? <AssetGeneratePanel projectId={projectId} /> : null}
        </div>
      ) : null}
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

      {query.isLoading || (tab === "project" && projects.isLoading) ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : tab === "project" && projects.isSuccess && !projectList.length ? (
        <EmptyState icon="📁" title="Проектов пока нет" hint="Файлы проекта появятся, когда будет создан проект" />
      ) : !assets.length ? (
        <EmptyState
          icon="🧩"
          title={tab === "project" ? "В этом проекте файлов нет" : tab === "shared" ? "Общих файлов нет" : "Библиотека пуста"}
          hint="Закиньте пару мемов и звуков — и 🤖 ИИ-монтаж начнёт вставлять их в подходящие моменты"
        />
      ) : (
        <>
          <div className="asset-grid">
            {shown.visible.map((a) => (
              <AssetCard key={a.id} asset={a} onDelete={setPending} projects={projectList} />
            ))}
          </div>
          <ShowMore rest={shown.rest} onMore={shown.more} />
        </>
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
