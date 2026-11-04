import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { coverDocsApi } from "@/api/coverDocs";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

const STYLE_LABELS: Record<string, string> = {
  yellow: "жёлтый",
  white: "белый",
  plate: "плашка",
  accent: "цвет бренда",
};

/** Cover variants of a rendered clip: make 3, pick one (it becomes the clip's cover and the
 *  plan's cover, burned in as the first frame on the next render). */
export function CoverVariants({ clip }: { clip: Clip }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const covers = useQuery({ queryKey: qk.clipCovers(clip.id), queryFn: () => clipsApi.covers(clip.id) });
  const list = covers.data?.covers ?? [];

  const refresh = () => {
    qc.invalidateQueries({ queryKey: qk.clipCovers(clip.id) });
    qc.invalidateQueries({ queryKey: ["clips"] });
  };

  const generate = useMutation({
    mutationFn: () => clipsApi.generateCovers(clip.id, { count: 3 }),
    onSuccess: (r) => {
      const how: Record<string, string> = {
        meta: "тексты из метаданных клипа",
        gemini: "тексты придумал Gemini",
        title: "тексты из заголовка клипа",
        texts: "ваши тексты",
      };
      toast.success(`Готово: ${r.new.length} варианта, ${how[r.texts_source] ?? r.texts_source}`);
      refresh();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сделать обложки"),
  });

  const choose = useMutation({
    mutationFn: (coverId: number) => clipsApi.chooseCover(clip.id, coverId, true),
    onSuccess: (r) => {
      toast.success(r.note || "Обложка выбрана");
      refresh();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось выбрать"),
  });

  const compose = useMutation({
    mutationFn: () => coverDocsApi.create({ clip_id: clip.id, template: "avatar-bottom" }),
    onSuccess: (r) => navigate(`/covers/${r.id}`),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось открыть редактор превью"),
  });

  const ratio = clip.width > 0 && clip.height > 0 ? `${clip.width} / ${clip.height}` : "9 / 16";
  const landscape = clip.width > clip.height;

  return (
    <div className="field cv-wrap">
      <div className="cv-head">
        <span>Обложка</span>
        <button
          type="button"
          className="btn sm"
          disabled={compose.isPending || clip.status !== "succeeded"}
          onClick={() => compose.mutate()}
          title="Редактор слоёв: кадр из ролика, аватар, текст. Без ИИ, можно дать ссылку на правку"
        >
          {compose.isPending ? "Открываю…" : "Собрать превью"}
        </button>
        <button
          type="button"
          className="btn sm pub-gen-btn"
          disabled={generate.isPending || clip.status !== "succeeded"}
          onClick={() => generate.mutate()}
          title="Сильные кадры клипа (лица, резкость, цвет) + крупный текст 2–5 слов в стороне от лиц, в стиле бренда"
        >
          {generate.isPending ? "Делаю…" : list.length ? "Новые 3 варианта" : "Сделать 3 варианта"}
        </button>
      </div>
      {list.length ? (
        <div className={`cv-grid${landscape ? " wide" : ""}`}>
          {list.map((c) => (
            <button
              key={c.id}
              type="button"
              className={`cv-item${c.chosen ? " chosen" : ""}`}
              style={{ aspectRatio: ratio }}
              disabled={choose.isPending}
              onClick={() => choose.mutate(c.id)}
              title={`${c.text} · ${STYLE_LABELS[c.style] ?? c.style} · кадр ${c.t.toFixed(1)} с${c.faces ? " · лицо" : ""}`}
            >
              <img src={c.url} alt={c.text} loading="lazy" />
              {c.chosen ? <span className="cv-badge">выбрана</span> : null}
            </button>
          ))}
        </div>
      ) : (
        <span className="muted cv-empty">
          {covers.isLoading ? "…" : "Вариантов нет. Выбранная станет превью клипа и миниатюрой YouTube, а при следующем рендере вошьётся первым кадром."}
        </span>
      )}
    </div>
  );
}
