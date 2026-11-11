/**
 * Код ВНУТРИ страницы (Runtime.evaluate) для разбора копии страницы на слои (node/webpage/layers.mjs):
 * выбор блоков, рамки, простой текст, шрифты страницы и «показать только этот блок» для кадра блока.
 * Каждая функция — обычная JS-функция; в страницу уходит её текст `(${fn})(arg)`.
 */

/**
 * Верх страницы как на кадре снимка: прибитые элементы (fixed — шапка, кнопка чата; sticky) встают на своё
 * место при прокрутке 0 обычным абсолютным/статичным блоком. Иначе при проверке «не закрыт ли блок»
 * (прокрутка к нему) шапка ехала бы следом и закрывала всё подряд.
 */
export function freezeFixed() {
  let n = 0;
  window.scrollTo(0, 0);
  for (const el of document.querySelectorAll("body *")) {
    const pos = getComputedStyle(el).position;
    if (pos === "sticky") {
      el.style.setProperty("position", "relative", "important");
      el.style.setProperty("top", "auto", "important");
      el.style.setProperty("bottom", "auto", "important");
      n++;
    } else if (pos === "fixed") {
      const r = el.getBoundingClientRect();
      el.style.setProperty("position", "absolute", "important");
      el.style.setProperty("top", `${r.top + scrollY}px`, "important");
      el.style.setProperty("left", `${r.left + scrollX}px`, "important");
      el.style.setProperty("width", `${r.width}px`, "important");
      el.style.setProperty("bottom", "auto", "important");
      el.style.setProperty("right", "auto", "important");
      n++;
    }
  }
  return n;
}

/**
 * Найти осмысленные блоки страницы внутри области и пометить их атрибутом data-vvf-b="<i>".
 *
 * opts: {region: {x,y,w,h} | null, selector: string | null, maxLayers, maxHeight, focusCards: bool}.
 * Вернуть {region, page_width, page_height, blocks: [{i, parent, kind, tag, text, title, alt, sel, box, pad,
 * text_style?}], font_faces: [{family, weight, style, range, src}], problem?}.
 *
 * Виды: header (шапка/меню), heading, text, button, price, badge, image — «листья» (внутри них блоков нет);
 * card (карточка товара), section (фон секции во всю ширину) — контейнеры, у них бывают дети.
 */
export function pickBlocks(opts) {
  const PRICE = /(\d[\d\s .,]*\s?(₽|руб|р\.|\$|€|£|usd|eur))|((₽|\$|€)\s?\d)/i;
  const LEAF = new Set(["header", "heading", "text", "button", "price", "badge", "image"]);
  const SKIP = new Set(["script", "style", "link", "meta", "br", "template", "noscript", "head", "title", "option",
    "source", "track", "wbr", "path", "g", "defs", "use", "symbol", "clippath", "lineargradient", "radialgradient", "stop", "mask"]);
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();
  const docW = Math.max(document.documentElement.clientWidth || 0, innerWidth || 0);
  const docH = Math.min(Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0), opts.maxHeight || 20000);
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    return [r.x + scrollX, r.y + scrollY, r.width, r.height];
  };
  const out = { page_width: docW, page_height: docH, blocks: [], font_faces: [] };

  // Область: селектор → рамка элемента; иначе заданная; иначе вся страница (до maxHeight).
  let region = opts.region ? { ...opts.region } : { x: 0, y: 0, w: docW, h: docH };
  if (opts.selector) {
    let el = null;
    try {
      el = document.querySelector(opts.selector);
    } catch {
      el = null;
    }
    if (!el) return { ...out, problem: `нет элемента ${opts.selector}` };
    const [x, y, w, h] = boxOf(el);
    region = { x, y, w, h };
  }
  region.x = Math.max(0, Math.min(docW - 1, region.x));
  region.y = Math.max(0, Math.min(docH - 1, region.y));
  region.w = Math.max(1, Math.min(docW - region.x, region.w));
  region.h = Math.max(1, Math.min(docH - region.y, region.h));
  for (const k of ["x", "y", "w", "h"]) region[k] = Math.round(region[k]);
  out.region = region;

  const parse = (c) => {
    const m = String(c || "").match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const hasBg = (cs) => {
    const c = parse(cs.backgroundColor);
    return (c && c.a > 0.05) || (cs.backgroundImage && cs.backgroundImage !== "none");
  };
  const bgImageUrl = (cs) => /url\(/.test(cs.backgroundImage || "");
  const hasBorder = (cs) => ["Top", "Right", "Bottom", "Left"].some((s) => parseFloat(cs[`border${s}Width`]) > 0 && cs[`border${s}Style`] !== "none" && (parse(cs[`border${s}Color`])?.a ?? 0) > 0.05);
  const hasShadow = (cs) => cs.boxShadow && cs.boxShadow !== "none";
  const radius = (cs) => parseFloat(cs.borderTopLeftRadius) || 0;
  const visible = (el) => {
    if (typeof el.checkVisibility === "function" && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: false })) return false;
    return true;
  };
  const ownText = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join(" ").trim();
  const words = (el) => clean(el.innerText || el.getAttribute("aria-label") || el.getAttribute("alt") || el.value || "");
  const blockChild = (el) => [...el.children].some((c) => {
    const d = getComputedStyle(c).display;
    return d === "block" || d === "flex" || d === "grid" || d === "table" || d === "list-item";
  });
  const inRegion = (b) => {
    const ix = Math.max(0, Math.min(b[0] + b[2], region.x + region.w) - Math.max(b[0], region.x));
    const iy = Math.max(0, Math.min(b[1] + b[3], region.y + region.h) - Math.max(b[1], region.y));
    return (ix * iy) / Math.max(1, b[2] * b[3]);
  };

  /** Вид блока или null. */
  const classify = (el, cs, b) => {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const text = words(el);
    if (/^h[1-6]$/.test(tag) || role === "heading") return text ? "heading" : null;
    if ((tag === "header" || tag === "nav" || role === "banner" || role === "navigation") && b[2] >= docW * 0.6) return "header";
    if (tag === "button" || role === "button" || (tag === "input" && /^(submit|button)$/i.test(el.type))) return text && text.length <= 60 ? "button" : null;
    if (tag === "img" || tag === "svg" || tag === "picture" || tag === "video" || tag === "canvas" || el.hasAttribute("data-vvf-iframe")) {
      return b[2] >= 24 && b[3] >= 24 ? "image" : null;
    }
    if (tag === "a" && text && text.length <= 40 && b[3] <= 120 && (hasBg(cs) || hasBorder(cs)) && (parseFloat(cs.paddingLeft) >= 4 || parseFloat(cs.paddingTop) >= 4)) return "button";
    if (text && text.length <= 40 && PRICE.test(text) && el.children.length <= 3 && ![...el.children].some((c) => clean(c.innerText) === text)) return "price";
    if (text && text.length <= 24 && b[2] <= 220 && b[3] <= 56 && hasBg(cs) && !el.querySelector("img,svg")) return "badge";
    const surface = (hasBg(cs) && !bgImageUrl(cs)) || hasShadow(cs) || hasBorder(cs);
    if (surface && b[2] >= 100 && b[3] >= 80 && b[2] <= docW * 0.98 && (radius(cs) > 0 || hasShadow(cs) || hasBorder(cs))) {
      const media = el.querySelector("img, picture, svg, video, [data-vvf-iframe]");
      if (media && text.length >= 2) return "card";
    }
    if (bgImageUrl(cs) && b[2] >= 40 && b[3] >= 40 && !blockChild(el) && (!text || text.length < 3)) return "image";
    if (hasBg(cs) && b[2] >= Math.min(docW, region.w) * 0.9 && b[3] >= 100) return "section";
    if (["p", "li", "blockquote", "figcaption", "dd", "dt", "td", "label", "small"].includes(tag)) return text.length >= 2 ? "text" : null;
    if ((tag === "span" || tag === "div" || tag === "a" || tag === "strong" || tag === "b" || tag === "em") && clean(ownText(el)).length >= 8 && !blockChild(el)) return "text";
    return null;
  };

  // Кандидаты в порядке документа; родитель — ближайший выбранный предок.
  const chosen = new Map(); // el -> node
  const nodes = [];
  const parentOf = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const n = chosen.get(p);
      if (n && !n.dropped) return n;
    }
    return null;
  };
  const iou = (a, b) => {
    const ix = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]));
    const iy = Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]));
    const inter = ix * iy;
    return inter / Math.max(1, a[2] * a[3] + b[2] * b[3] - inter);
  };
  for (const el of document.body ? document.body.querySelectorAll("*") : []) {
    const tag = el.tagName.toLowerCase();
    if (SKIP.has(tag) || el.closest("svg") !== el && el.closest("svg")) continue;
    const b = boxOf(el);
    if (b[2] < 8 || b[3] < 8) continue;
    if (b[0] + b[2] <= 2 || b[0] >= docW - 2) continue;
    if (inRegion(b) < 0.5) continue;
    if (b[2] * b[3] >= region.w * region.h * 0.92 && !(tag === "img" || tag === "picture")) continue; // обёртка всей области
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || !visible(el)) continue;
    const kind = classify(el, cs, b);
    if (!kind) continue;
    const parent = parentOf(el);
    if (parent && LEAF.has(parent.kind)) continue;
    const node = { el, kind, box: b, parent, dropped: false, cs };
    if (parent && iou(parent.box, b) >= 0.9) {
      // Тот же прямоугольник: контейнер с единственным содержимым — берём более «говорящий» вид.
      if (LEAF.has(kind) || parent.kind === "section") {
        parent.dropped = true;
        node.parent = parent.parent;
      } else continue;
    }
    chosen.set(el, node);
    nodes.push(node);
  }
  let live = nodes.filter((n) => !n.dropped);
  const liveParent = (n) => {
    let p = n.parent;
    while (p && p.dropped) p = p.parent;
    return p;
  };
  for (const n of live) n.parent = liveParent(n);

  // Не закрыт ли блок целиком: 5 точек, сверху должен оказаться сам блок или его содержимое.
  const pe = document.createElement("style");
  pe.textContent = "*{pointer-events:auto!important}";
  document.head.appendChild(pe);
  const covered = (n) => {
    const [x, y, w, h] = n.box;
    window.scrollTo(0, Math.max(0, y + h / 2 - innerHeight / 2));
    const pts = [[0.5, 0.5], [0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]];
    for (const [fx, fy] of pts) {
      const px = x + w * fx - scrollX;
      const py = y + h * fy - scrollY;
      if (px < 0 || py < 0 || px >= innerWidth || py >= innerHeight) return false; // вне экрана — не проверить, считаем видимым
      const top = document.elementFromPoint(px, py);
      if (!top || n.el === top || n.el.contains(top)) return false;
      // Сверху — прозрачная обёртка (предок блока)? тогда тоже видно.
      if (top.contains(n.el)) return false;
    }
    return true;
  };
  live = live.filter((n) => {
    if (n.box[3] > innerHeight * 0.9) return true;
    const hidden = covered(n);
    if (hidden) n.dropped = true;
    return !hidden;
  });
  pe.remove();
  window.scrollTo(0, 0);
  for (const n of live) n.parent = liveParent(n);

  // Много карточек (каталог) и область не задана — карточка одним слоем, её дети остаются в её кадре.
  const cards = live.filter((n) => n.kind === "card");
  if (cards.length > 4 && !opts.focusCards) {
    for (const n of live) {
      for (let p = n.parent; p; p = p.parent) {
        if (p.kind === "card") {
          n.dropped = true;
          break;
        }
      }
    }
    live = live.filter((n) => !n.dropped);
  }
  // Предел слоёв: сначала важное (кнопки, цены, заголовки, карточки), при равенстве — выше на странице.
  const RANK = { button: 10, price: 10, heading: 9, card: 8, badge: 8, image: 7, header: 6, text: 5, section: 4 };
  const limit = Math.max(1, opts.maxLayers || 40);
  if (live.length > limit) {
    const keep = new Set([...live].sort((a, b) => RANK[b.kind] - RANK[a.kind] || a.box[1] - b.box[1]).slice(0, limit));
    for (const n of live) if (!keep.has(n)) n.dropped = true;
    live = live.filter((n) => !n.dropped);
    for (const n of live) n.parent = liveParent(n);
  }

  // Простой текст — можно живым текстом: один стиль, без картинок, без своей подложки.
  const loadedFamilies = new Set();
  try {
    for (const f of document.fonts) if (f.status === "loaded") loadedFamilies.add(f.family.replace(/["']/g, "").trim().toLowerCase());
  } catch {
    /* нет FontFaceSet */
  }
  const firstFamily = (stack) => String(stack || "").split(",")[0].replace(/["']/g, "").trim();
  const sameFont = (a, b) => a.fontFamily === b.fontFamily && a.fontSize === b.fontSize && a.fontWeight === b.fontWeight && a.fontStyle === b.fontStyle && a.color === b.color && a.letterSpacing === b.letterSpacing;
  const textStyle = (n) => {
    if (!["heading", "text", "price", "button", "badge"].includes(n.kind)) return null;
    const el = n.el;
    const cs = n.cs;
    if (el.querySelector("img,svg,picture,video,canvas,input,select,textarea,iframe,[data-vvf-iframe]")) return null;
    if (["input", "select", "textarea"].includes(el.tagName.toLowerCase())) return null;
    for (const d of el.querySelectorAll("*")) {
      if (d.tagName.toLowerCase() === "br") continue;
      const dc = getComputedStyle(d);
      if (!sameFont(dc, cs) || hasBg(dc) || hasBorder(dc) || (dc.textShadow && dc.textShadow !== "none")) return null;
    }
    if (hasBg(cs) || hasBorder(cs) || hasShadow(cs) || (cs.textShadow && cs.textShadow !== "none")) return null;
    if (cs.transform && cs.transform !== "none") return null;
    if ((cs.webkitBackgroundClip || cs.backgroundClip) === "text") return null;
    if (cs.writingMode && !cs.writingMode.startsWith("horizontal")) return null;
    for (let p = el; p && p !== document.documentElement; p = p.parentElement) {
      if (Number(getComputedStyle(p).opacity) < 0.99) return null;
    }
    const color = parse(cs.color);
    if (!color || color.a < 0.5) return null;
    const text = String(el.innerText || "").replace(/[ \t]+/g, " ").replace(/\n{2,}/g, "\n").trim();
    if (!text || text.length > 380) return null;
    const size = parseFloat(cs.fontSize);
    const lh = cs.lineHeight === "normal" ? size * 1.2 : parseFloat(cs.lineHeight);
    const padL = parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth);
    const padT = parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth);
    const padR = parseFloat(cs.paddingRight) + parseFloat(cs.borderRightWidth);
    const padB = parseFloat(cs.paddingBottom) + parseFloat(cs.borderBottomWidth);
    const b = n.box;
    // Рамка самих строк (Range): у блока во всю ширину текст бывает короче.
    let tb = [b[0] + padL, b[1] + padT, Math.max(1, b[2] - padL - padR), Math.max(1, b[3] - padT - padB)];
    try {
      const range = document.createRange();
      range.selectNodeContents(el);
      const r = range.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) tb = [r.x + scrollX, r.y + scrollY, r.width, r.height];
    } catch {
      /* как есть */
    }
    const family = firstFamily(cs.fontFamily);
    const align = { start: "left", left: "left", end: "right", right: "right", center: "center", justify: "left", "-webkit-center": "center" }[cs.textAlign] || "left";
    return {
      text, family, stack: cs.fontFamily, web_font: loadedFamilies.has(family.toLowerCase()),
      size, weight: Number(cs.fontWeight) || 400, italic: cs.fontStyle === "italic",
      color: [color.r, color.g, color.b, color.a], line_height: lh, letter_spacing: parseFloat(cs.letterSpacing) || 0,
      align, lines: Math.max(1, Math.round(tb[3] / Math.max(1, lh))), box: tb.map((v) => Math.round(v * 100) / 100),
      content: [b[0] + padL, b[1] + padT, Math.max(1, b[2] - padL - padR), Math.max(1, b[3] - padT - padB)].map((v) => Math.round(v * 100) / 100),
    };
  };
  const shadowPad = (cs) => {
    let pad = 0;
    if (hasShadow(cs)) pad += (String(cs.boxShadow).match(/-?\d+(\.\d+)?px/g) || []).reduce((s, v) => s + Math.abs(parseFloat(v)), 0);
    if (cs.outlineStyle !== "none") pad += (parseFloat(cs.outlineWidth) || 0) + Math.max(0, parseFloat(cs.outlineOffset) || 0);
    if (cs.filter && cs.filter.includes("drop-shadow")) pad += (String(cs.filter).match(/-?\d+(\.\d+)?px/g) || []).reduce((s, v) => s + Math.abs(parseFloat(v)), 0);
    return Math.min(90, Math.ceil(pad) + 3);
  };
  const sel = (el) => {
    const parts = [];
    for (let cur = el; cur && cur.nodeType === 1 && cur !== document.documentElement; cur = cur.parentElement) {
      if (cur.id && /^[A-Za-z][\w-]{0,60}$/.test(cur.id) && document.querySelectorAll(`#${cur.id}`).length === 1) {
        parts.unshift(`#${cur.id}`);
        break;
      }
      const t = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (!parent) {
        parts.unshift(t);
        break;
      }
      const same = [...parent.children].filter((c) => c.tagName === cur.tagName);
      parts.unshift(same.length > 1 ? `${t}:nth-of-type(${same.indexOf(cur) + 1})` : t);
    }
    return parts.join(" > ");
  };
  const titleOf = (el) => {
    const h = el.querySelector("h1,h2,h3,h4,h5,h6,[class*=title],[class*=name],strong,b");
    const t = h ? clean(h.innerText) : "";
    if (t) return t.slice(0, 80);
    const img = el.querySelector("img[alt]");
    return img ? clean(img.getAttribute("alt")).slice(0, 80) : "";
  };
  // Порядок рисования среди соседей одного родителя: позиционированные (absolute/relative/…) — поверх обычных,
  // по z-index; отрицательный z-index — под ними. Смотрим сам блок и его предков до родительского блока.
  const paintKey = (n) => {
    let positioned = 0;
    let z = 0;
    const stop = n.parent ? n.parent.el : null;
    for (let p = n.el; p && p !== stop && p !== document.body; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.position !== "static") {
        positioned = 1;
        const zi = parseInt(cs.zIndex, 10);
        if (Number.isFinite(zi)) z = zi;
      }
    }
    return z < 0 ? [-1, z] : [positioned, z];
  };
  for (const old of document.querySelectorAll("[data-vvf-b]")) old.removeAttribute("data-vvf-b");
  live.forEach((n, i) => {
    n.i = i;
  });
  out.blocks = live.map((n) => {
    n.el.setAttribute("data-vvf-b", String(n.i));
    const leafText = LEAF.has(n.kind) ? clean(n.el.innerText || n.el.value || "").slice(0, 160) : "";
    const rec = {
      i: n.i, parent: n.parent ? n.parent.i : -1, kind: n.kind, tag: n.el.tagName.toLowerCase(), text: leafText,
      title: n.kind === "card" || n.kind === "section" ? titleOf(n.el) : "", alt: clean(n.el.getAttribute("alt") || n.el.getAttribute("aria-label") || "").slice(0, 80),
      sel: sel(n.el), box: n.box.map((v) => Math.round(v * 100) / 100), paint: paintKey(n),
      pad: LEAF.has(n.kind) && n.kind !== "image" ? Math.max(4, shadowPad(n.cs)) : shadowPad(n.cs),
    };
    const ts = textStyle(n);
    if (ts) rec.text_style = ts;
    return rec;
  });

  // Шрифты страницы (@font-face): семейство, толщина, unicode-range и адрес файла.
  const faces = [];
  const walk = (rules, base) => {
    for (const r of rules) {
      if (r.type === 5 /* FONT_FACE_RULE */) {
        const src = r.style.getPropertyValue("src") || "";
        const urls = [...src.matchAll(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g)].map((m) => {
          try {
            return new URL(m[2], base).href;
          } catch {
            return "";
          }
        }).filter(Boolean);
        faces.push({
          family: (r.style.getPropertyValue("font-family") || "").replace(/["']/g, "").trim(),
          weight: r.style.getPropertyValue("font-weight") || "400", style: r.style.getPropertyValue("font-style") || "normal",
          range: r.style.getPropertyValue("unicode-range") || "", src: urls,
        });
      } else if (r.cssRules) {
        try {
          walk(r.cssRules, base);
        } catch {
          /* чужой лист */
        }
      }
      if (r.styleSheet) {
        try {
          walk(r.styleSheet.cssRules, r.styleSheet.href || base);
        } catch {
          /* чужой лист */
        }
      }
    }
  };
  for (const sheet of document.styleSheets) {
    try {
      walk(sheet.cssRules, sheet.href || document.baseURI);
    } catch {
      /* чужой лист */
    }
  }
  out.font_faces = faces.slice(0, 200);
  return out;
}

/** Стиль «показать только отмеченное»: всё скрыто, data-vvf-on и его содержимое видно, data-vvf-off внутри — нет. */
export function isolateStyle() {
  const st = document.createElement("style");
  st.id = "vvf-isolate";
  st.textContent = "html,body{background:transparent!important}" +
    "*{visibility:hidden!important}" +
    "[data-vvf-on],[data-vvf-on] *{visibility:visible!important}" +
    "[data-vvf-on] [data-vvf-off],[data-vvf-on] [data-vvf-off] *{visibility:hidden!important}";
  document.head.appendChild(st);
  return true;
}

/** Отметить блок i (и спрятать его дочерние блоки children) для кадра блока. */
export function focusBlock(arg) {
  for (const el of document.querySelectorAll("[data-vvf-on],[data-vvf-off]")) {
    el.removeAttribute("data-vvf-on");
    el.removeAttribute("data-vvf-off");
  }
  const el = document.querySelector(`[data-vvf-b="${arg.i}"]`);
  if (!el) return false;
  el.setAttribute("data-vvf-on", "1");
  for (const c of arg.children) document.querySelector(`[data-vvf-b="${c}"]`)?.setAttribute("data-vvf-off", "1");
  return true;
}

/** Фон страницы без блоков: верхние блоки (и всё в них) спрятаны. */
export function plateStyle(top) {
  const st = document.createElement("style");
  st.id = "vvf-plate";
  st.textContent = top.map((i) => `[data-vvf-b="${i}"],[data-vvf-b="${i}"] *`).join(",") + "{visibility:hidden!important}";
  if (top.length) document.head.appendChild(st);
  return true;
}

/** Убрать стиль по id. */
export function dropStyle(id) {
  document.getElementById(id)?.remove();
  return true;
}
