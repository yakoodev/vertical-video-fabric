/**
 * Код, который выполняется ВНУТРИ страницы (Runtime.evaluate) для node/webpage/snapshot.mjs: копия DOM без
 * скриптов с локальными адресами файлов, карта заметных мест страницы (заголовки, кнопки, цены), признаки
 * капчи и правки DOM (ops). Каждая функция — обычная JS-функция; в страницу уходит её текст `(${fn})(arg)`.
 */

/** Есть ли на странице капча / проверка «вы не робот»: строка причины или "". */
export function detectBlock() {
  const text = `${document.title}\n${(document.body && document.body.innerText) || ""}`.slice(0, 4000).toLowerCase();
  const words = [
    "captcha", "are you a robot", "are you human", "verify you are human", "verifying you are human",
    "attention required", "checking your browser", "access denied", "unusual traffic", "press & hold",
    "подтвердите, что вы не робот", "вы не робот", "проверка браузера", "доступ запрещ",
  ];
  const hit = words.find((w) => text.includes(w));
  if (hit) return `на странице «${hit}»`;
  const frames = [...document.querySelectorAll("iframe[src]")].map((f) => f.src);
  const wall = frames.find((s) => /recaptcha|hcaptcha|challenges\.cloudflare|turnstile|captcha/i.test(s));
  if (wall) return "на странице окно капчи";
  if (document.querySelector("#challenge-form, #cf-challenge-running, .cf-browser-verification, #px-captcha")) {
    return "проверка «вы не робот»";
  }
  return "";
}

/**
 * Копия страницы без скриптов: адреса файлов (картинки, стили, шрифты, фоны) — на локальные из `map`
 * (абсолютный адрес → "../assets/имя"), чего нет в карте — пусто (копия не ходит в сеть). Возвращает
 * {html, stylesheets: [{href, local}]} — стили из ссылок node переписывает сам (url() внутри css).
 */
export function serializePage(map) {
  const abs = (u) => {
    try {
      return new URL(u, document.baseURI).href;
    } catch {
      return null;
    }
  };
  const local = (u) => {
    if (!u) return null;
    if (u.startsWith("data:")) return u;
    const a = abs(u);
    return a && map[a.split("#")[0]] ? map[a.split("#")[0]] + (a.includes("#") ? a.slice(a.indexOf("#")) : "") : null;
  };
  const cssUrls = (css, base) =>
    String(css || "").replace(/url\(\s*(['"]?)([^'")]*)\1\s*\)/g, (m, _q, u) => {
      if (!u || u.startsWith("data:") || u.startsWith("#")) return m;
      let a;
      try {
        a = new URL(u, base).href.split("#")[0];
      } catch {
        return 'url("data:,")';
      }
      return map[a] ? `url("${map[a]}")` : 'url("data:,")';
    });
  const origAll = [document.documentElement, ...document.documentElement.querySelectorAll("*")];
  const root = document.documentElement.cloneNode(true);
  const cloneAll = [root, ...root.querySelectorAll("*")];
  const drop = [];
  const sheets = [];
  for (let i = 0; i < origAll.length && i < cloneAll.length; i++) {
    const o = origAll[i];
    const c = cloneAll[i];
    const tag = o.tagName.toLowerCase();
    for (const attr of [...c.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on") || name === "integrity" || name === "nonce" || name === "crossorigin") c.removeAttribute(attr.name);
      else if ((name === "href" || name === "src" || name === "action" || name === "formaction" || name === "xlink:href") && /^\s*javascript:/i.test(attr.value)) {
        c.setAttribute(attr.name, "#");
      }
    }
    if (c.hasAttribute && c.hasAttribute("style")) c.setAttribute("style", cssUrls(c.getAttribute("style"), document.baseURI));
    if (["script", "noscript", "base", "object", "embed", "applet", "audio", "portal"].includes(tag)) {
      drop.push(c);
      continue;
    }
    if (tag === "meta" && /refresh/i.test(o.getAttribute("http-equiv") || "")) {
      drop.push(c);
      continue;
    }
    if (tag === "link") {
      const rel = (o.getAttribute("rel") || "").toLowerCase();
      if (rel.split(/\s+/).includes("stylesheet") && o.href) {
        const href = o.href.split("#")[0];
        if (map[href]) {
          c.setAttribute("href", map[href]);
          sheets.push({ href, local: map[href] });
        } else {
          // Стиль без тела ответа (кэш, чужой домен) — если он читается, вкладываем правила.
          let text = "";
          try {
            text = [...(o.sheet ? o.sheet.cssRules : [])].map((r) => r.cssText).join("\n");
          } catch {
            text = "";
          }
          if (text) {
            const st = document.createElement("style");
            if (o.media) st.setAttribute("media", o.media);
            st.textContent = cssUrls(text, href);
            c.replaceWith(st);
          } else drop.push(c);
        }
      } else drop.push(c); // preload, prefetch, preconnect, manifest, иконки — копии не нужны
      continue;
    }
    if (tag === "style") {
      let text = o.textContent || "";
      try {
        // CSS-in-JS (insertRule) — правила есть, а текста в теге нет.
        const rules = o.sheet ? [...o.sheet.cssRules] : [];
        if (rules.length && text.trim().length < 10) text = rules.map((r) => r.cssText).join("\n");
      } catch {
        /* чужой лист */
      }
      c.textContent = cssUrls(text, document.baseURI);
      continue;
    }
    if (tag === "img") {
      const src = local(o.currentSrc || o.src || o.getAttribute("data-src") || "");
      c.removeAttribute("srcset");
      c.removeAttribute("sizes");
      c.removeAttribute("loading");
      c.removeAttribute("decoding");
      if (src) c.setAttribute("src", src);
      else c.removeAttribute("src");
      continue;
    }
    if (tag === "source") {
      drop.push(c);
      continue;
    }
    if (tag === "video") {
      const poster = local(o.poster || "");
      c.removeAttribute("src");
      c.removeAttribute("autoplay");
      if (poster) c.setAttribute("poster", poster);
      else c.removeAttribute("poster");
      c.setAttribute("preload", "none");
      continue;
    }
    if (tag === "canvas") {
      try {
        const img = document.createElement("img");
        img.src = o.toDataURL("image/png");
        const r = o.getBoundingClientRect();
        img.setAttribute("style", `${c.getAttribute("style") || ""};width:${r.width}px;height:${r.height}px`);
        if (o.className) img.className = o.className;
        c.replaceWith(img);
      } catch {
        /* холст с чужой картинкой не читается */
      }
      continue;
    }
    if (tag === "iframe" || tag === "frame") {
      // Встроенный ролик или виджет — серая плашка того же размера (копия не грузит чужие страницы).
      const r = o.getBoundingClientRect();
      const ph = document.createElement("div");
      ph.setAttribute("data-vvf-iframe", "1");
      ph.setAttribute("style", `display:${getComputedStyle(o).display === "inline" ? "inline-block" : getComputedStyle(o).display};width:${Math.round(r.width)}px;height:${Math.round(r.height)}px;background:#1f2128;border-radius:8px`);
      c.replaceWith(ph);
      continue;
    }
    if (tag === "image" || tag === "use") {
      for (const name of ["href", "xlink:href"]) {
        const v = c.getAttribute(name);
        if (v && !v.startsWith("#") && !v.startsWith("data:")) {
          const l = local(v);
          if (l) c.setAttribute(name, l);
          else c.removeAttribute(name);
        }
      }
      continue;
    }
    if (tag === "a" && o.href) c.setAttribute("href", o.href);
    if (tag === "form") c.removeAttribute("action");
    if ((tag === "input" || tag === "textarea") && o.value && o.type !== "password") c.setAttribute("value", o.value);
    if (tag === "input" && o.type === "password") c.removeAttribute("value");
  }
  for (const d of drop) d.remove();
  // CSS-in-JS через adoptedStyleSheets (конструируемые листы) — тоже в копию.
  try {
    const adopted = [...(document.adoptedStyleSheets || [])].map((s) => [...s.cssRules].map((r) => r.cssText).join("\n")).join("\n");
    if (adopted.trim()) {
      const st = document.createElement("style");
      st.textContent = cssUrls(adopted, document.baseURI);
      root.querySelector("head")?.appendChild(st);
    }
  } catch {
    /* нет листов */
  }
  const head = root.querySelector("head");
  if (head) {
    for (const old of head.querySelectorAll("meta[charset]")) old.remove();
    const meta = document.createElement("meta");
    meta.setAttribute("charset", "utf-8");
    head.prepend(meta);
    const src = document.createElement("meta");
    src.setAttribute("name", "vvf-source");
    src.setAttribute("content", location.href);
    head.appendChild(src);
  }
  return { html: "<!doctype html>\n" + root.outerHTML, stylesheets: sheets };
}

/**
 * Заметные места страницы для агента и для правок: заголовки, кнопки и ссылки с текстом, цены, крупные
 * картинки, блоки текста. Для каждого — CSS-селектор (уникальный в этой версии), текст и рамка в CSS px
 * страницы. До `limit` штук сверху вниз.
 */
export function outlinePage(limit) {
  const PRICE = /(\d[\d\s .,]*\s?(₽|руб|р\.|\$|€|£|usd|eur))|((₽|\$|€)\s?\d)/i;
  const sel = (el) => {
    if (el.id && /^[A-Za-z][\w-]{0,60}$/.test(el.id) && document.querySelectorAll(`#${el.id}`).length === 1) return `#${el.id}`;
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      if (cur.id && /^[A-Za-z][\w-]{0,60}$/.test(cur.id) && document.querySelectorAll(`#${cur.id}`).length === 1) {
        parts.unshift(`#${cur.id}`);
        break;
      }
      const tag = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const same = [...parent.children].filter((c) => c.tagName === cur.tagName);
      parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(cur) + 1})` : tag);
      cur = parent;
    }
    return parts.join(" > ");
  };
  const pageW = document.documentElement.clientWidth || innerWidth;
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return null;
    // За краем страницы (карусель, спрятанное меню) — на кадре его нет.
    if (r.x + scrollX + r.width <= 2 || r.x + scrollX >= pageW - 2) return null;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return null;
    return [Math.round(r.x + scrollX), Math.round(r.y + scrollY), Math.round(r.width), Math.round(r.height)];
  };
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim().slice(0, 140);
  const out = [];
  const seen = new Set();
  const push = (el, kind) => {
    if (seen.has(el)) return;
    const box = vis(el);
    if (!box) return;
    const text = clean(el.innerText || el.getAttribute("aria-label") || el.getAttribute("alt") || "");
    if (kind !== "image" && !text) return;
    seen.add(el);
    out.push({ kind, sel: sel(el), tag: el.tagName.toLowerCase(), text, box });
  };
  for (const el of document.querySelectorAll("h1, h2, h3")) push(el, "heading");
  for (const el of document.querySelectorAll("button, a, [role=button], input[type=submit]")) {
    const t = clean(el.innerText || el.value || "");
    if (t && t.length <= 40) push(el, "button");
  }
  for (const el of document.querySelectorAll("body *")) {
    if (el.children.length > 2) continue;
    const t = clean(el.innerText);
    // Цена — самый вложенный элемент с этим текстом (div и его span с той же ценой — одна цена).
    if (t && t.length <= 40 && PRICE.test(t) && ![...el.children].some((c) => clean(c.innerText) === t)) push(el, "price");
  }
  for (const el of document.querySelectorAll("img")) {
    const r = el.getBoundingClientRect();
    if (r.width * r.height >= 120 * 120) push(el, "image");
  }
  for (const el of document.querySelectorAll("p, li, figcaption, blockquote, td")) {
    const t = clean(el.innerText);
    if (t.length >= 12) push(el, "text");
  }
  out.sort((a, b) => a.box[1] - b.box[1] || a.box[0] - b.box[0]);
  // Каталог из сотни цен не должен вытеснить заголовки и кнопки: у каждого вида своя доля, сверху вниз.
  const share = { price: 0.25, button: 0.25, heading: 0.2, image: 0.1, text: 0.2 };
  const taken = {};
  const kept = out.filter((o) => {
    taken[o.kind] = (taken[o.kind] || 0) + 1;
    return taken[o.kind] <= Math.max(3, Math.round(limit * share[o.kind]));
  });
  return kept.slice(0, limit);
}

/**
 * Правки DOM (ops уже проверены Python-ом, app/webpages_ops.py). Цель — `selector` (CSS) или `find` (текст
 * на странице: самый вложенный элемент с этим текстом). Возвращает {applied: [{i, op, count, problem?}],
 * region: {x, y, w, h} | null} — region от crop_to.
 */
export function applyOps(ops) {
  const STYLE_ID = "vvf-edit-style";
  const css = [];
  const results = [];
  let region = null;
  const byText = (needle, all) => {
    const want = String(needle || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!want) return [];
    const hits = [...document.querySelectorAll("body *")].filter((el) => {
      if (["script", "style"].includes(el.tagName.toLowerCase())) return false;
      return (el.textContent || "").replace(/\s+/g, " ").toLowerCase().includes(want);
    });
    // Самые вложенные: элемент, внутри которого есть другой с тем же текстом, не берём.
    const deepest = hits.filter((el) => !hits.some((o) => o !== el && el.contains(o)));
    return all ? deepest : deepest.slice(0, 1);
  };
  const targets = (op) => {
    if (op.selector) {
      try {
        const found = [...document.querySelectorAll(op.selector)];
        return op.all ? found : found.slice(0, 1);
      } catch {
        throw new Error(`селектор не разобрать: ${op.selector}`);
      }
    }
    if (op.find) return byText(op.find, op.all);
    return [];
  };
  const replaceText = (el, from, to) => {
    let n = 0;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const norm = (s) => s.replace(/[\s ]+/g, " ");
    for (const t of nodes) {
      if (t.nodeValue.includes(from)) {
        t.nodeValue = t.nodeValue.split(from).join(to);
        n++;
      } else if (norm(t.nodeValue).includes(norm(from))) {
        t.nodeValue = norm(t.nodeValue).split(norm(from)).join(to);
        n++;
      }
    }
    if (!n && norm(el.textContent || "").includes(norm(from))) {
      // Текст разбит на несколько узлов (цена «1 990» + «₽» в разных span) — заменяем весь текст элемента.
      el.textContent = norm(el.textContent).split(norm(from)).join(to);
      n = 1;
    }
    return n;
  };
  ops.forEach((op, i) => {
    const res = { i, op: op.op, count: 0 };
    try {
      if (op.op === "set_text" && !op.selector && !op.find && op.from) {
        res.count = replaceText(document.body, op.from, op.text);
        if (!res.count) res.problem = `текст «${op.from}» не найден`;
        results.push(res);
        return;
      }
      const els = targets(op);
      if (!els.length) {
        res.problem = op.selector ? `нет элемента ${op.selector}` : `не найден текст «${op.find}»`;
        results.push(res);
        return;
      }
      for (const el of els) {
        if (op.op === "set_text") {
          if (op.from) res.count += replaceText(el, op.from, op.text);
          else {
            el.textContent = op.text;
            res.count++;
          }
        } else if (op.op === "hide") {
          el.style.setProperty("visibility", "hidden", "important");
          res.count++;
        } else if (op.op === "remove") {
          el.remove();
          res.count++;
        } else if (op.op === "set_style") {
          for (const [k, v] of Object.entries(op.style || {})) el.style.setProperty(k, String(v), "important");
          res.count++;
        } else if (op.op === "add_highlight") {
          const cls = `vvf-hl-${i}`;
          el.classList.add(cls);
          const c = op.color || "#ffd400";
          const w = op.width || 6;
          if (op.kind === "outline") css.push(`.${cls}{outline:${w}px solid ${c} !important;outline-offset:${Math.round(w * 0.8)}px !important;border-radius:${op.radius ?? 10}px}`);
          else {
            const glow = `0 0 0 ${w}px ${c}, 0 0 ${w * 6}px ${w * 2}px ${c}`;
            css.push(`.${cls}{box-shadow:${glow} !important;border-radius:${op.radius ?? 10}px;position:relative;z-index:5}`);
            if (op.kind === "pulse") {
              css.push(`@keyframes vvf-pulse-${i}{0%,100%{box-shadow:${glow}}50%{box-shadow:0 0 0 ${w * 1.6}px ${c}, 0 0 ${w * 10}px ${w * 3}px ${c}}}`);
              css.push(`.${cls}{animation:vvf-pulse-${i} 1.2s ease-in-out infinite}`);
            }
          }
          res.count++;
        } else if (op.op === "crop_to") {
          const r = el.getBoundingClientRect();
          const pad = op.pad ?? 24;
          const x = Math.max(0, r.x + scrollX - pad);
          const y = Math.max(0, r.y + scrollY - pad);
          region = { x: Math.round(x), y: Math.round(y), w: Math.round(r.width + pad * 2), h: Math.round(r.height + pad * 2) };
          res.count++;
          break;
        } else if (op.op === "insert_badge") {
          const b = document.createElement("span");
          b.className = `vvf-badge vvf-badge-${i}`;
          b.textContent = op.text;
          const pos = op.position || "top-right";
          const base = `display:inline-block;padding:6px 14px;border-radius:999px;font:800 ${op.size || 18}px/1.2 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:${op.color || "#ffffff"};background:${op.bg || "#ff2d55"};box-shadow:0 4px 14px rgba(0,0,0,.25);white-space:nowrap;z-index:20`;
          if (pos === "before" || pos === "after") {
            b.setAttribute("style", `${base};margin:6px`);
            el[pos === "before" ? "before" : "after"](b);
          } else {
            if (getComputedStyle(el).position === "static") el.style.setProperty("position", "relative");
            const [v, h] = pos.split("-");
            b.setAttribute("style", `${base};position:absolute;${v}:-12px;${h}:-12px`);
            el.appendChild(b);
          }
          res.count++;
        }
      }
    } catch (e) {
      res.problem = String((e && e.message) || e).slice(0, 200);
    }
    results.push(res);
  });
  if (css.length) {
    let st = document.getElementById(STYLE_ID);
    if (!st) {
      st = document.createElement("style");
      st.id = STYLE_ID;
      document.head.appendChild(st);
    }
    st.textContent += "\n" + css.join("\n");
  }
  return { applied: results, region };
}
