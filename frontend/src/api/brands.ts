import { api } from "@/api/client";
import type { Background } from "@/motion";

/** Брендовый шаблон (app/brand_kit.py): ролики одного заказчика — в одном стиле. */
export interface BrandStroke {
  color: string;
  width: number;
}

export interface Brand {
  id: number;
  name: string;
  /** Семейство из пака шрифтов; "" — шрифт по умолчанию. */
  font: string;
  /** highlight — второй цвет бренда: выделенные слова субтитров (нет — сервер берёт контрастный к звучащему слову). */
  colors: { text: string; accent: string; plate: string; highlight?: string };
  logo_asset_id: number | null;
  logo_corner: "top-right" | "top-left";
  caption_style: {
    style: string;
    font: string;
    color: string | null;
    activeColor: string | null;
    stroke: BrandStroke | null;
    bg: { color: string; opacity: number } | null;
  };
  hook_style: {
    font: string;
    color: string | null;
    size: number | null;
    plate: boolean;
    stroke: BrandStroke | null;
    uppercase: boolean;
  };
  cta: { text: string; url_text: string; duration_sec: number };
  music_track_id: number | null;
  /** Фон за персонажем и под видео meme: анимированный, цвета [] — цвета бренда (app/brand_kit.py). */
  backdrop: Background;
  sfx_policy: "none" | "minimal";
  created_at?: string;
  updated_at?: string;
}

export type BrandInput = Omit<Brand, "id" | "created_at" | "updated_at">;

export interface ApplyBrandResult {
  ok: boolean;
  brand: { id: number; name: string };
  changes: string[];
  warnings: string[];
  check?: string;
}

export const brandsApi = {
  list: () => api.get<Brand[]>("/api/brands"),
  create: (brand: BrandInput) => api.post<Brand>("/api/brands", brand),
  update: (id: number, patch: Partial<BrandInput>) => api.patch<Brand>(`/api/brands/${id}`, patch),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/brands/${id}`),
  apply: (clipPlanId: number, brandId: number) =>
    api.post<ApplyBrandResult>(`/api/clip-plans/${clipPlanId}/apply-brand`, { brand_id: brandId }),
};
