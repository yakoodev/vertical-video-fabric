/**
 * Библиотека 3D-моделей вещей (docs/3d/models.md): GLB в DATA_DIR/runtime/models3d/<категория>/<slug>.glb и
 * manifest.json рядом. Страница получает их по http://scene3d.local/models3d/… (render.mjs, --models).
 *
 * Ссылка на модель в сцене — ``model: "keyboard"`` (вариант категории выбирается засеянно: одна и та же сцена —
 * одна и та же вещь, разные сцены — разные) или ``model: "keyboard/mech_tkl_dark"`` (точно эта). Ссылки паков
 * (``пак/файл.glb``) — с расширением, библиотечные — без. Нет манифеста, модели или файла — объект строится из
 * запасного: ``primitive``/``params`` самого объекта или список ``fallback`` (процедурная вещь из деталей).
 *
 * Здесь — только чистые функции (без three): разбор ссылки, выбор варианта, проверка манифеста. Загрузка — engine.ts.
 */

/** Якорь: точка pos (м), size [w, h] — размер (экран), rot — поворот (°, наклон экрана назад — отрицательный x). */
export interface LibAnchor { pos: number[]; size?: number[]; rot?: number[] }

export interface LibModel {
  id: string;
  category: string;
  slug: string;
  file: string;
  title?: string;
  /** Габарит после нормализации [ширина x, высота y, глубина z], м; основание — центр низа в (0,0,0), перёд — +z. */
  size: number[];
  tris?: number;
  bytes?: number;
  outline?: number;
  tags?: string[];
  weight?: number;
  /** Точки модели (м, в её осях): screen — центр экрана {pos, size:[w,h]}, bulb — лампочка, top — столешница. */
  anchors?: Record<string, LibAnchor>;
  license?: string;
  license_url?: string;
  author?: string;
  source?: Record<string, any>;
  attribution?: string;
  normalize?: Record<string, any>;
}

export interface Library {
  version: number;
  categories: Record<string, string[]>;
  models: Record<string, LibModel>;
}

export const LIB_ORIGIN = "http://scene3d.local/models3d/";

/** Ссылка на библиотеку: «категория» или «категория/slug» без расширения файла (у паков расширение есть). */
export function isLibraryRef(ref: unknown): ref is string {
  return typeof ref === "string" && /^[a-z0-9_]+(\/[a-z0-9_]+)?$/.test(ref);
}

/** FNV-1a 32 бита — засев выбора варианта по строке. */
export function hashSeed(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** Число в [0, 1) из засева (mulberry32, один шаг). */
function unit(seed: number): number {
  let t = (seed + 0x6d2b79f5) >>> 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

export interface PickOptions {
  /** Засев сцены (поле models.seed сцены или seed окружения). */
  seed?: number | string;
  /** Ключ объекта (обычно id): у разных вещей одной категории в сцене — разные варианты. */
  key?: string;
  /** Нужны все эти теги (например ["desk"] у растений на стол). */
  tags?: string[];
  /** Не брать эти id (уже стоят рядом). */
  exclude?: string[];
}

/**
 * Модель по ссылке: точный id — она (или null); категория — вариант по весам, засеянно от (seed, key, категория).
 * Тот же засев — тот же вариант в любом браузере и куске рендера.
 */
export function pickModel(lib: Library | null, ref: string, opt: PickOptions = {}): LibModel | null {
  if (!lib || !isLibraryRef(ref)) return null;
  if (ref.includes("/")) return lib.models[ref] || null;
  const ids = (lib.categories[ref] || []).filter((id) => {
    const m = lib.models[id];
    if (!m || (m.weight ?? 1) <= 0) return false;
    if (opt.exclude?.includes(id)) return false;
    return !opt.tags?.length || opt.tags.every((t) => (m.tags || []).includes(t));
  });
  if (!ids.length) return null;
  const total = ids.reduce((s, id) => s + (lib.models[id].weight ?? 1), 0);
  let x = unit(hashSeed(`${opt.seed ?? 1}|${opt.key ?? ""}|${ref}`)) * total;
  for (const id of ids) {
    x -= lib.models[id].weight ?? 1;
    if (x < 0) return lib.models[id];
  }
  return lib.models[ids[ids.length - 1]];
}

/** Проверка манифеста: список проблем по-русски (пусто — годен). */
export function validateManifest(m: any): string[] {
  const out: string[] = [];
  if (!m || typeof m !== "object") return ["манифест не объект"];
  if (m.version !== 1) out.push(`version: ждём 1, а не ${m.version}`);
  if (!m.models || typeof m.models !== "object") return [...out, "нет models"];
  if (!m.categories || typeof m.categories !== "object") out.push("нет categories");
  for (const [id, x] of Object.entries<any>(m.models)) {
    const where = `модель ${id}`;
    if (!isLibraryRef(id) || !id.includes("/")) out.push(`${where}: id — «категория/slug» латиницей`);
    if (x.id !== id) out.push(`${where}: поле id не совпадает с ключом`);
    if (`${x.category}/${x.slug}` !== id) out.push(`${where}: category/slug не совпадают с id`);
    if (x.file !== `${x.category}/${x.slug}.glb`) out.push(`${where}: file — «категория/slug.glb»`);
    if (!Array.isArray(x.size) || x.size.length !== 3 || x.size.some((v: any) => !(typeof v === "number" && v > 0 && v < 20))) out.push(`${where}: size — [w, h, d] в метрах`);
    if (!x.license || !x.license_url) out.push(`${where}: нет лицензии или ссылки на неё`);
    if (x.license && !/^CC0-1\.0$|^CC-BY-[34]\.0$/.test(x.license)) out.push(`${where}: лицензия ${x.license} не из разрешённых (CC0, CC BY)`);
    if (/^CC-BY/.test(x.license || "") && !(x.author && x.attribution)) out.push(`${where}: CC BY без автора и строки атрибуции`);
    if (x.source?.kind !== "generated" && !x.source?.url) out.push(`${where}: нет ссылки на источник`);
    if (typeof x.bytes === "number" && x.bytes > 2.5 * 1024 * 1024) out.push(`${where}: файл больше бюджета (${x.bytes} байт)`);
    for (const [name, a] of Object.entries<any>(x.anchors || {})) {
      if (!Array.isArray(a?.pos) || a.pos.length !== 3) out.push(`${where}: якорь ${name} без pos [x,y,z]`);
    }
    if (m.categories && !(m.categories[x.category] || []).includes(id)) out.push(`${where}: нет в categories.${x.category}`);
  }
  for (const [cat, ids] of Object.entries<any>(m.categories || {})) {
    for (const id of ids || []) if (!m.models[id]) out.push(`categories.${cat}: нет модели ${id}`);
  }
  return out;
}

/** Каталог для агента и справки: категории → варианты (id, название, габарит, теги, лицензия). */
export function libraryCatalog(lib: Library | null) {
  if (!lib) return [];
  return Object.entries(lib.categories).map(([category, ids]) => ({
    category,
    variants: ids.map((id) => {
      const m = lib.models[id];
      return { id, title: m.title, size: m.size, tags: m.tags || [], license: m.license, anchors: Object.keys(m.anchors || {}) };
    }),
  }));
}
