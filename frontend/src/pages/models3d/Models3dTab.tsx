import { useRef, useState, type DragEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { assets3dApi, assets3dKeys, uploadAsset3d, type Asset3d } from "@/api/assets3d";
import { Icon } from "@/components/Icon";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";
import { mb, num } from "./labels";
import "@/styles/models3d.css";

/**
 * Проект → «3D-модели»: персонажи, реквизит и окружение карточками. Нажал — окно просмотра (вращать, выражения,
 * анимации). Перетащить .vrm/.glb/.gltf на страницу — новая модель; лёгкую копию для просмотра делает сервер.
 */

const OK_EXT = [".vrm", ".glb", ".gltf"];

interface UploadState {
  name: string;
  sent: number;
  stage: "send" | "build";
}

function Card({ a, projectId }: { a: Asset3d; projectId: number }) {
  return (
    <Link className="m3d-card" to={`/p/${projectId}/3d/${a.id}`} title={`Открыть «${a.name}» в 3D`}>
      <div className="m3d-thumb">
        {a.urls.thumb ? <img src={a.urls.thumb} alt="" loading="lazy" /> : <span className="m3d-thumb-ph">3D</span>}
        {a.status === "failed" ? <span className="m3d-badge is-bad">не открылась</span> : null}
        {a.status === "building" ? <span className="m3d-badge">готовлю…</span> : null}
      </div>
      <div className="m3d-card-body">
        <strong>{a.name}</strong>
        <span className="muted">
          {a.is_vrm ? "персонаж" : num(a.info?.triangles) + " треуг."} · {mb(a.preview_gz_bytes || a.preview_bytes)}
        </span>
      </div>
    </Link>
  );
}

export function Models3dTab({ projectId }: { projectId: number }) {
  const list = useQuery({ queryKey: assets3dKeys.list(projectId), queryFn: () => assets3dApi.list(projectId) });
  const qc = useQueryClient();
  const toast = useToast();
  const [drag, setDrag] = useState(false);
  const [uploads, setUploads] = useState<UploadState[]>([]);
  const input = useRef<HTMLInputElement | null>(null);
  const depth = useRef(0);

  const upload = async (files: File[]) => {
    const maxMb = list.data?.max_upload_mb ?? 200;
    for (const file of files) {
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!OK_EXT.includes(ext)) {
        toast.error(`«${file.name}» — не 3D-модель: нужен файл .vrm, .glb или .gltf`);
        continue;
      }
      if (file.size > maxMb * 1024 * 1024) {
        toast.error(`«${file.name}» больше ${maxMb} МБ`);
        continue;
      }
      setUploads((u) => [...u, { name: file.name, sent: 0, stage: "send" }]);
      const mark = (patch: Partial<UploadState>) =>
        setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, ...patch } : x)));
      try {
        const a = await uploadAsset3d(projectId, file, (sent) => mark({ sent, stage: sent >= 1 ? "build" : "send" }));
        if (a.duplicate) toast.success(`«${a.name}» уже есть в проекте`);
        else if (a.status === "failed") toast.error(`«${a.name}»: ${a.error || "не открылась"}`);
        else toast.success(`«${a.name}» добавлена · для просмотра ${mb(a.preview_gz_bytes || a.preview_bytes)}`);
      } catch (e) {
        toast.error(`«${file.name}»: ${e instanceof ApiError ? e.message : "не загрузилась"}`);
      } finally {
        setUploads((u) => u.filter((x) => x.name !== file.name));
        qc.invalidateQueries({ queryKey: assets3dKeys.list(projectId) });
      }
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    depth.current = 0;
    setDrag(false);
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) void upload(files);
  };

  if (list.isLoading) return <Loading />;
  if (list.isError || !list.data) return <ErrorState error={list.error} onRetry={() => list.refetch()} />;
  const groups = list.data.groups;

  return (
    <div
      className={`m3d-tab${drag ? " is-drag" : ""}`}
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        depth.current += 1;
        setDrag(true);
      }}
      onDragOver={(e) => e.dataTransfer.types.includes("Files") && e.preventDefault()}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (!depth.current) setDrag(false);
      }}
      onDrop={onDrop}
    >
      <div className="m3d-intro">
        <p>
          Модели персонажей и вещей проекта. Нажми на карточку — откроется 3D: можно крутить, приближать, менять лицо
          и включать движения. <b>Добавить</b> — перетащи сюда файл .vrm, .glb или .gltf (до {list.data.max_upload_mb} МБ).
        </p>
        <button className="btn primary" onClick={() => input.current?.click()}>
          <Icon name="plus" size={16} /> Добавить модель
        </button>
        <input ref={input} type="file" accept=".vrm,.glb,.gltf" multiple hidden
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            e.target.value = "";
            if (files.length) void upload(files);
          }} />
      </div>
      {uploads.map((u) => (
        <div key={u.name} className="m3d-uploading">
          <span className="spinner" />
          <span>
            {u.stage === "send" ? `Загружаю «${u.name}» · ${Math.round(u.sent * 100)} %` : `«${u.name}»: готовлю к просмотру…`}
          </span>
          <div className="m3d-bar"><span style={{ width: `${Math.round(u.sent * 100)}%` }} /></div>
        </div>
      ))}
      {groups.map((g) => (
        <section key={g.kind} className="m3d-group">
          <h2>
            {g.label} <span className="muted">{g.assets.length ? g.assets.length : ""}</span>
          </h2>
          {g.assets.length ? (
            <div className="m3d-grid">
              {g.assets.map((a) => <Card key={a.id} a={a} projectId={projectId} />)}
            </div>
          ) : (
            <p className="m3d-empty muted">
              {g.kind === "environment" ? "Пока пусто: сюда — комнаты, улицы, лестницы." : "Пока пусто."}
            </p>
          )}
        </section>
      ))}
      {!list.data.count ? (
        <p className="muted">Моделей пока нет — перетащи первый файл сюда.</p>
      ) : null}
    </div>
  );
}
