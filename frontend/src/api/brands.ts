import { api } from "@/api/client";
import type { Background } from "@/motion";

/** Брендовый шаблон (app/brand_kit.py): ролики одного заказчика — в одном стиле. */
export interface BrandStroke {
  color: string;
  width: number;
}

export interface BrandNameplate {
  platform: "twitch" | "youtube" | "tiktok" | "telegram" | "kick" | "none";
  handle: string;
  style: "glass" | "solid";
  /** null — цвет площадки. */
  accent: string | null;
  position: "auto" | "top-left" | "top-right" | "under-split-left" | "under-split-center";
  /** Высота пилюли, px кадра 1080×1920. */
  size: number;
  /** Мягкое появление в начале (только без своей анимации, anim none). */
  fade: boolean;
  /** Своя анимация: вход от значка, редкий блик и тик значка (subtle — по умолчанию). */
  anim: "none" | "subtle" | "lively";
  /** Красная пульсирующая точка LIVE. */
  live: boolean;
  /** Свёртка в значок: auto — к призыву бренда, collapse — в конце ролика, none — нет. */
  outro: "auto" | "collapse" | "none";
}

/** Заставка или концовка логотипом кистью (app/brand_kit_logo.py, шаблоны logo-intro / logo-outro). */
export interface BrandLogoBumper {
  on: boolean;
  /** Длина, с (1.5–10; одобрено 3.5). */
  duration: number;
  color: string;
  position: "верх" | "центр" | "низ";
  /** Ширина логотипа, % кадра. */
  size: number;
  /** Взмах кисти, хлыст, удар (и вжух на уходе заставки) — записи пака, без треска. */
  sound: boolean;
  /** Только в роликах от стольких секунд (0 — во всех): заставка — на длинных версиях, не на частях TikTok. */
  min_clip_sec: number;
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
  /** style banner — концовкой-баннером (размытие + анимированный баннер, app/end_screen.py); нет — плашка. */
  cta: { text: string; url_text: string; duration_sec: number; style?: "plate" | "banner"; template?: "live" | "schedule" | "neon"; blur?: number };
  music_track_id: number | null;
  /** Фон за персонажем и под видео meme: анимированный, цвета [] — цвета бренда (app/brand_kit.py). */
  backdrop: Background;
  sfx_policy: "none" | "minimal";
  /** Плашка канала (значок площадки + ник) на весь ролик; пустой ник и platform none — нет плашки. */
  nameplate: BrandNameplate;
  /** Заставка логотипом в начало ролика (по умолчанию выключена). */
  logo_intro: BrandLogoBumper;
  /** Концовка логотипом до последнего кадра (по умолчанию выключена). */
  logo_outro: BrandLogoBumper;
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
  /** `logo` — заставка/концовка логотипом на этот ролик поверх бренда (нет поля — как в бренде). */
  apply: (clipPlanId: number, brandId: number, logo?: { logo_intro?: boolean; logo_outro?: boolean }) =>
    api.post<ApplyBrandResult>(`/api/clip-plans/${clipPlanId}/apply-brand`, { brand_id: brandId, ...(logo ?? {}) }),
};
