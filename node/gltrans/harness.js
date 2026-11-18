/*
 * Страница рендера gl-transitions (node/gltrans/render.mjs): WebGL 1 в headless Chromium рисует кадр перехода
 * между двумя кадрами A и B по спецификации GL Transition v1 (README gl-transitions): шейдер даёт
 * `vec4 transition(vec2 uv)`, мы — `progress`, `ratio`, `getFromColor`, `getToColor` и `main()`.
 *
 * Кадры приходят по одному сырыми RGB (строки сверху вниз) по /frames/a|b/<j>, ответ — сырые RGB кадра (те же строки сверху вниз)
 * в base64. uv спецификации — начало слева снизу: строки картинки не переворачиваем, а берём текстуру по
 * (uv.x, 1 − uv.y) и рисуем так, что нижняя строка буфера (первая в readPixels) — верх картинки.
 * Прозрачное в ответе шейдера — поверх чёрного (у видео альфы нет).
 */
(function () {
  "use strict";

  const VERT = "attribute vec2 _p; varying vec2 _uv; void main(){ _uv = _p * 0.5 + 0.5; gl_Position = vec4(_p, 0.0, 1.0); }";

  function fragSource(glsl) {
    return [
      "precision highp float;",
      "precision highp int;",
      "varying vec2 _uv;",
      "uniform sampler2D from, to;",
      "uniform float progress, ratio;",
      "vec4 getFromColor(vec2 uv) { return texture2D(from, vec2(uv.x, 1.0 - uv.y)); }",
      "vec4 getToColor(vec2 uv) { return texture2D(to, vec2(uv.x, 1.0 - uv.y)); }",
      "#line 1",
      glsl,
      "void main() {",
      "  vec4 c = transition(vec2(_uv.x, 1.0 - _uv.y));",
      "  gl_FragColor = vec4(clamp(c.rgb, 0.0, 1.0) * clamp(c.a, 0.0, 1.0), 1.0);",
      "}",
    ].join("\n");
  }

  /** Параметры шейдера: `uniform T a, b; // = value` и `/* = value *\/` (спецификация v1) → [{name, type, value}]. */
  function parseParams(glsl) {
    const out = [];
    const re = /uniform\s+(bool|int|float|vec[234]|ivec[234])\s+([^;]+);([^\n]*)/g;
    let m;
    while ((m = re.exec(glsl))) {
      const type = m[1];
      const tail = m[3];
      const names = [];
      let shared = null;
      for (const part of m[2].split(",")) {
        const inline = /\/\*\s*=\s*([^*]+)\*\//.exec(part);
        const name = part.replace(/\/\*[\s\S]*?\*\//g, "").trim();
        if (!/^[A-Za-z_]\w*$/.test(name)) continue;
        names.push({ name, value: inline ? inline[1].trim() : null });
      }
      const after = /^\s*\/\/\s*=\s*([^;]+?)\s*;?\s*(\/\/.*)?$/.exec(tail);
      if (after) shared = after[1].trim();
      for (const n of names) {
        const raw = n.value ?? shared;
        if (raw == null) continue;
        out.push({ name: n.name, type, value: parseValue(type, raw) });
      }
    }
    return out;
  }

  function parseValue(type, raw) {
    const text = String(raw).trim();
    if (type === "bool") return text === "true" ? 1 : 0;
    const size = type === "float" || type === "int" ? 1 : Number(type.slice(-1));
    const inner = /\(([^)]*)\)/.exec(text);
    const nums = (inner ? inner[1] : text).split(",").map((s) => Number(s.trim())).filter((v) => Number.isFinite(v));
    if (!nums.length) nums.push(0);
    const vals = nums.length === 1 ? new Array(size).fill(nums[0]) : nums.slice(0, size);
    while (vals.length < size) vals.push(0);
    return type.startsWith("i") || type === "int" ? vals.map((v) => Math.round(v)) : vals;
  }

  function setUniform(gl, prog, type, name, value) {
    const loc = gl.getUniformLocation(prog, name);
    if (!loc) return;
    const v = Array.isArray(value) ? value : [value];
    if (type === "bool" || type === "int") gl.uniform1i(loc, Math.round(Number(v[0]) || 0));
    else if (type === "float") gl.uniform1f(loc, Number(v[0]) || 0);
    else if (type === "ivec2") gl.uniform2i(loc, v[0] | 0, v[1] | 0);
    else if (type === "ivec3") gl.uniform3i(loc, v[0] | 0, v[1] | 0, v[2] | 0);
    else if (type === "ivec4") gl.uniform4i(loc, v[0] | 0, v[1] | 0, v[2] | 0, v[3] | 0);
    else if (type === "vec2") gl.uniform2f(loc, +v[0], +v[1]);
    else if (type === "vec3") gl.uniform3f(loc, +v[0], +v[1], +v[2]);
    else if (type === "vec4") gl.uniform4f(loc, +v[0], +v[1], +v[2], +v[3]);
  }

  const canvas = document.getElementById("c");
  const gl = canvas.getContext("webgl", { preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false, alpha: false });
  let job = null;

  function compile(type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(sh) || "без сообщения";
      gl.deleteShader(sh);
      throw new Error(`шейдер не собрался: ${log.slice(0, 400)}`);
    }
    return sh;
  }

  function texture(unit) {
    const tex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
  }

  async function fetchBytes(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  }

  function toBase64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }

  window.__gltrans = {
    ok: !!gl,
    /** Задание: шейдер, размер, кадры A/B (URL сырых RGB n кадров), свои параметры поверх умолчаний шейдера. */
    async load(spec) {
      if (!gl) throw new Error("WebGL недоступен");
      const { width, height } = spec;
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
      const glsl = new TextDecoder().decode(await fetchBytes(spec.shader));
      if (job && job.prog) gl.deleteProgram(job.prog);
      const prog = gl.createProgram();
      gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fragSource(glsl)));
      gl.bindAttribLocation(prog, 0, "_p");
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(`программа не связалась: ${gl.getProgramInfoLog(prog)}`);
      gl.useProgram(prog);
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.pixelStorei(gl.PACK_ALIGNMENT, 1);
      const params = parseParams(glsl);
      const own = spec.params || {};
      for (const p of params) setUniform(gl, prog, p.type, p.name, own[p.name] !== undefined ? own[p.name] : p.value);
      gl.uniform1i(gl.getUniformLocation(prog, "from"), 0);
      gl.uniform1i(gl.getUniformLocation(prog, "to"), 1);
      gl.uniform1f(gl.getUniformLocation(prog, "ratio"), width / height);
      const size = width * height * 3;
      job = { prog, width, height, size, a: spec.a, b: spec.b, texA: texture(0), texB: texture(1),
              progressLoc: gl.getUniformLocation(prog, "progress"), params: params.map((p) => p.name) };
      return { params: job.params };
    },
    /** Кадр j окна с прогрессом p → base64 сырых RGB (строки сверху вниз). Кадры A/B — по одному (память). */
    async frame(j, p) {
      const { width, height, size } = job;
      const a = await fetchBytes(`${job.a}/${j}`);
      const b = await fetchBytes(`${job.b}/${j}`);
      if (a.length !== size || b.length !== size) throw new Error(`кадр ${j}: не тот размер`);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, job.texA);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, width, height, 0, gl.RGB, gl.UNSIGNED_BYTE, a);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, job.texB);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, width, height, 0, gl.RGB, gl.UNSIGNED_BYTE, b);
      gl.uniform1f(job.progressLoc, p);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      const px = new Uint8Array(width * height * 4);
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, px);
      const err = gl.getError();
      if (err) throw new Error(`ошибка WebGL ${err}`);
      const rgb = new Uint8Array(size);
      for (let i = 0, k = 0; i < px.length; i += 4, k += 3) {
        rgb[k] = px[i];
        rgb[k + 1] = px[i + 1];
        rgb[k + 2] = px[i + 2];
      }
      return toBase64(rgb);
    },
    /** Разбор параметров шейдера без отрисовки (тесты). */
    parseParams,
  };
})();
