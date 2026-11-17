import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { coverDocsApi, type CoverFormat } from "@/api/coverDocs";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/api/client";
import "@/styles/project-photos.css";

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/* «Фото и превью» проекта: документы фоторедактора без клипа (превью YouTube, обложки, картинки к постам). */

const FORMATS: [CoverFormat, string][] = [
  ["16:9", "Превью YouTube 1280×720"],
  ["9:16", "Вертикаль 1080×1920"],
  ["1:1", "Квадрат 1080×1080"],
  ["4:5", "Пост 1080×1350"],
  ["custom", "Свой размер"],
];

export function ProjectPhotos({ projectId }: { projectId: number }) {
  const toast = useToast();
  const navigate = useNavigate();
  const [format, setFormat] = useState<CoverFormat>("16:9");
  const [width, setWidth] = useState(1280);
  const [height, setHeight] = useState(720);
  const docs = useQuery({
    queryKey: ["cover-docs", "project", projectId],
    queryFn: () => coverDocsApi.list({ project_id: projectId }),
  });
  const create = useMutation({
    mutationFn: async () => (await coverDocsApi.create({
      project_id: projectId, format, title: "Фото", ...(format === "custom" ? { width, height } : {}),
    })).id,
    onSuccess: (id) => navigate(`/covers/${id}`),
    onError: (e) => toast.error(errorText(e, "Не удалось создать фото")),
  });
  const list = docs.data?.docs ?? [];
  return (
    <section className="pphotos" aria-label="Фото и превью проекта">
      <div className="pphotos-head">
        <h3>Фото и превью</h3>
        <form className="pphotos-new" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <select className="input" aria-label="Размер" value={format} onChange={(e) => setFormat(e.target.value as CoverFormat)}>
            {FORMATS.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          {format === "custom" ? (
            <>
              <input className="input" type="number" min={64} max={4096} aria-label="Ширина" value={width} onChange={(e) => setWidth(Number(e.target.value) || 1280)} />
              <span aria-hidden="true">×</span>
              <input className="input" type="number" min={64} max={4096} aria-label="Высота" value={height} onChange={(e) => setHeight(Number(e.target.value) || 720)} />
            </>
          ) : null}
          <button type="submit" className="btn primary sm" disabled={create.isPending}>{create.isPending ? "Создаю…" : "Новое фото"}</button>
        </form>
      </div>
      {list.length ? (
        <div className="pphotos-list">
          {list.map((d) => (
            <button key={d.id} type="button" className="pphotos-card" onClick={() => navigate(`/covers/${d.id}`)} title="Открыть в редакторе">
              <span className="pphotos-img" style={{ aspectRatio: `${d.width} / ${d.height}` }}>
                {d.preview ? <img src={`${d.preview}?rev=${d.rev}`} alt="" loading="lazy" /> : <span>{d.width}×{d.height}</span>}
              </span>
              <span className="pphotos-title">{d.title}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="muted">
          {docs.isLoading ? "Загружаю…" : "Здесь будут превью и картинки проекта. Превью из ролика — кнопка «Превью для YouTube» в «Ленте» или на экране клипа."}
        </p>
      )}
    </section>
  );
}
