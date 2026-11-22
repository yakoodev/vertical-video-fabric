"""Лёгкая копия 3D-модели для просмотра в браузере (app/assets3d.py): GLB/VRM/glTF → preview.glb.

Владелец смотрит сервис издалека (~150 КБ/с), поэтому превью — несколько мегабайт, а не 15–20:

* текстуры — WebP (EXT_texture_webp), длинная сторона ≤ 1024 у цветовых карт и ≤ 512 у служебных (нормали,
  затенение, блики, маски MToon);
* геометрия — кодек meshoptimizer (EXT_meshopt_compression, node/assets3d/meshopt.mjs) по видам буфера.

Документ glTF не перестраивается: номера узлов, мешей, материалов и текстур остаются прежними, поэтому расширения
VRM (VRM 0.x, VRMC_vrm, пружинки, MToon, выражения) по-прежнему указывают куда надо. Меняются только байты картинок и
раскладка буфера. Здесь же — справка о модели (треугольники, материалы, текстуры, выражения, пружинки) и обложка
из метаданных VRM.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import struct
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
MESHOPT_SCRIPT = ROOT / "node" / "assets3d" / "meshopt.mjs"
MAIN_MAX = 1024
SMALL_MAX = 512
WEBP_QUALITY = 84

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

# Служебные карты: им хватает 512 px.
SMALL_GLTF = ("normalTexture", "occlusionTexture", "metallicRoughnessTexture")
SMALL_VRM0 = ("_BumpMap", "_SphereAdd", "_RimTexture", "_OutlineWidthTexture", "_UvAnimMaskTexture", "_EmissionMap")
SMALL_MTOON1 = ("shadingShiftTexture", "matcapTexture", "rimMultiplyTexture", "outlineWidthMultiplyTexture",
                "uvAnimationMaskTexture")


class ModelError(ValueError):
    """Файл — не модель или в нём то, что превью не умеет (внешние файлы у .gltf)."""


# --------------------------------------------------------------------------- чтение и запись


def read_model(path: Path) -> tuple[dict, bytes]:
    """GLB/VRM или .gltf со встроенными данными → (документ, единый двоичный буфер)."""
    data = Path(path).read_bytes()
    if len(data) >= 12 and struct.unpack_from("<I", data, 0)[0] == GLB_MAGIC:
        off, doc, bin_ = 12, None, b""
        while off + 8 <= len(data):
            ln, typ = struct.unpack_from("<II", data, off)
            chunk = data[off + 8: off + 8 + ln]
            if typ == CHUNK_JSON:
                doc = json.loads(chunk)
            elif typ == CHUNK_BIN and not bin_:
                bin_ = bytes(chunk)
            off += 8 + ln
        if not isinstance(doc, dict) or "asset" not in doc:
            raise ModelError("в файле нет описания модели")
        if any(b.get("uri") for b in doc.get("buffers", [])):
            raise ModelError("модель ссылается на внешние файлы — загрузите один файл .glb или .vrm")
        return doc, bin_
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ModelError("это не 3D-модель (.glb, .gltf или .vrm)") from exc
    if not isinstance(doc, dict) or "asset" not in doc:
        raise ModelError("это не 3D-модель (.glb, .gltf или .vrm)")
    return _embed_gltf(doc)


def _data_uri(uri: str) -> bytes:
    if not uri.startswith("data:"):
        raise ModelError("модель ссылается на внешние файлы — загрузите один файл .glb или .vrm")
    head, _, payload = uri.partition(",")
    return base64.b64decode(payload) if head.endswith(";base64") else payload.encode()


def _embed_gltf(doc: dict) -> tuple[dict, bytes]:
    """.gltf с данными в data:-ссылках → один буфер, картинки — виды буфера (как в GLB)."""
    buffers = [_data_uri(b.get("uri", "")) for b in doc.get("buffers", [])]
    out = bytearray()
    offsets = []
    for b in buffers:
        offsets.append(len(out))
        out += b
        out += b"\0" * (-len(out) % 4)
    for bv in doc.get("bufferViews", []):
        bv["byteOffset"] = offsets[bv.get("buffer", 0)] + bv.get("byteOffset", 0)
        bv["buffer"] = 0
    for im in doc.get("images", []):
        if "uri" in im:
            raw = _data_uri(im.pop("uri"))
            doc.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": len(out), "byteLength": len(raw)})
            im["bufferView"] = len(doc["bufferViews"]) - 1
            im.setdefault("mimeType", "image/png" if raw[:4] == b"\x89PNG" else "image/jpeg")
            out += raw
            out += b"\0" * (-len(out) % 4)
    doc["buffers"] = [{"byteLength": len(out)}]
    return doc, bytes(out)


def write_glb(doc: dict, bin_: bytes) -> bytes:
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * (-len(js) % 4)
    bn = bin_ + b"\0" * (-len(bin_) % 4)
    total = 12 + 8 + len(js) + (8 + len(bn) if bn else 0)
    out = struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(js), CHUNK_JSON) + js
    if bn:
        out += struct.pack("<II", len(bn), CHUNK_BIN) + bn
    return out


def view_bytes(doc: dict, bin_: bytes, index: int) -> bytes:
    bv = doc["bufferViews"][index]
    start = bv.get("byteOffset", 0)
    return bin_[start: start + bv["byteLength"]]


# --------------------------------------------------------------------------- справка о модели


def vrm_version(doc: dict) -> str | None:
    ext = doc.get("extensions") or {}
    if "VRMC_vrm" in ext:
        return "1.0"
    if "VRM" in ext:
        return "0.x"
    return None


def expressions(doc: dict) -> list[str]:
    """Имена выражений VRM: пресеты и свои (как их называет three-vrm: happy, aa, blink…)."""
    ext = doc.get("extensions") or {}
    if "VRMC_vrm" in ext:
        e = ext["VRMC_vrm"].get("expressions") or {}
        return [*(e.get("preset") or {}).keys(), *(e.get("custom") or {}).keys()]
    if "VRM" in ext:
        v0 = {"joy": "happy", "angry": "angry", "sorrow": "sad", "fun": "relaxed", "a": "aa", "i": "ih", "u": "ou",
              "e": "ee", "o": "oh", "blink": "blink", "blink_l": "blinkLeft", "blink_r": "blinkRight",
              "lookup": "lookUp", "lookdown": "lookDown", "lookleft": "lookLeft", "lookright": "lookRight",
              "neutral": "neutral"}
        out = []
        for g in (ext["VRM"].get("blendShapeMaster") or {}).get("blendShapeGroups") or []:
            preset = str(g.get("presetName") or "").lower()
            name = v0.get(preset) if preset and preset != "unknown" else str(g.get("name") or "")
            if name and name not in out:
                out.append(name)
        return out
    return []


def spring_count(doc: dict) -> int:
    ext = doc.get("extensions") or {}
    if "VRMC_springBone" in ext:
        return sum(len(s.get("joints") or []) for s in ext["VRMC_springBone"].get("springs") or [])
    if "VRM" in ext:
        groups = (ext["VRM"].get("secondaryAnimation") or {}).get("boneGroups") or []
        return sum(len(g.get("bones") or []) for g in groups)
    return 0


def _image_size(raw: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            return im.size
    except Exception:  # noqa: BLE001 — битая картинка: размер просто неизвестен
        return None


def inspect(doc: dict, bin_: bytes | None = None) -> dict:
    """Справка: треугольники, вершины, меши, материалы, текстуры (размеры), морфы, кости, VRM-сведения."""
    acc = doc.get("accessors") or []
    tris = verts = morphs = 0
    for mesh in doc.get("meshes") or []:
        for p in mesh.get("primitives") or []:
            pos = (p.get("attributes") or {}).get("POSITION")
            n = acc[pos]["count"] if pos is not None and pos < len(acc) else 0
            verts += n
            mode = p.get("mode", 4)
            count = acc[p["indices"]]["count"] if "indices" in p and p["indices"] < len(acc) else n
            if mode == 4:
                tris += count // 3
            elif mode in (5, 6):
                tris += max(0, count - 2)
            morphs = max(morphs, len(p.get("targets") or []))
    images = []
    for i, im in enumerate(doc.get("images") or []):
        size = None
        if bin_ is not None and "bufferView" in im:
            size = _image_size(view_bytes(doc, bin_, im["bufferView"]))
        images.append({"index": i, "name": im.get("name") or f"image{i}", "mime": im.get("mimeType") or "",
                       "width": size[0] if size else None, "height": size[1] if size else None})
    ext = doc.get("extensions") or {}
    meta: dict[str, Any] = {}
    if "VRMC_vrm" in ext:
        m = ext["VRMC_vrm"].get("meta") or {}
        meta = {"title": m.get("name") or "", "author": ", ".join(m.get("authors") or [])}
    elif "VRM" in ext:
        m = ext["VRM"].get("meta") or {}
        meta = {"title": m.get("title") or "", "author": m.get("author") or ""}
    return {
        "triangles": tris,
        "vertices": verts,
        "meshes": len(doc.get("meshes") or []),
        "materials": len(doc.get("materials") or []),
        "textures": len(doc.get("textures") or []),
        "images": images,
        "morph_targets": morphs,
        "skins": len(doc.get("skins") or []),
        "joints": sum(len(s.get("joints") or []) for s in doc.get("skins") or []),
        "animations": len(doc.get("animations") or []),
        "vrm": vrm_version(doc),
        "expressions": expressions(doc),
        "spring_bones": spring_count(doc),
        "meta": meta,
    }


# --------------------------------------------------------------------------- текстуры


def _tex_image(doc: dict, tex: Any) -> int | None:
    textures = doc.get("textures") or []
    if not isinstance(tex, int) or not 0 <= tex < len(textures):
        return None
    t = textures[tex]
    src = t.get("source")
    if src is None:
        src = ((t.get("extensions") or {}).get("EXT_texture_webp") or {}).get("source")
    return src


def image_roles(doc: dict) -> dict[int, str]:
    """Картинка → «main» (цвет, видна крупно) или «small» (служебная карта). Неизвестно где — «main»."""
    roles: dict[int, set[str]] = {}

    def mark(tex: Any, role: str) -> None:
        img = _tex_image(doc, tex)
        if img is not None:
            roles.setdefault(img, set()).add(role)

    for m in doc.get("materials") or []:
        pbr = m.get("pbrMetallicRoughness") or {}
        mark((pbr.get("baseColorTexture") or {}).get("index"), "main")
        mark((m.get("emissiveTexture") or {}).get("index"), "main")
        for key in SMALL_GLTF:
            mark((pbr.get(key) or m.get(key) or {}).get("index"), "small")
        mt = (m.get("extensions") or {}).get("VRMC_materials_mtoon") or {}
        mark((mt.get("shadeMultiplyTexture") or {}).get("index"), "main")
        for key in SMALL_MTOON1:
            mark((mt.get(key) or {}).get("index"), "small")
    vrm0 = (doc.get("extensions") or {}).get("VRM") or {}
    for mp in vrm0.get("materialProperties") or []:
        for key, tex in (mp.get("textureProperties") or {}).items():
            mark(tex, "small" if key in SMALL_VRM0 else "main")
    meta_tex = (vrm0.get("meta") or {}).get("texture")
    if isinstance(meta_tex, int) and meta_tex >= 0:
        mark(meta_tex, "small")
    thumb = ((doc.get("extensions") or {}).get("VRMC_vrm") or {}).get("meta", {}).get("thumbnailImage")
    if isinstance(thumb, int):
        roles.setdefault(thumb, set()).add("small")
    return {i: ("main" if "main" in r else "small") for i, r in roles.items()}


def shrink_image(raw: bytes, max_side: int, quality: int = WEBP_QUALITY) -> bytes | None:
    """Картинка → WebP с длинной стороной ≤ max_side; None — не вышло (битая картинка)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if has_alpha else "RGB")
            if has_alpha and im.getextrema()[3][0] == 255:
                im = im.convert("RGB")  # прозрачность заявлена, но её нет — без альфы легче
            w, h = im.size
            k = min(1.0, max_side / float(max(w, h)))
            if k < 1.0:
                im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "WEBP", quality=quality, method=5)
            return out.getvalue()
    except Exception:  # noqa: BLE001
        log.warning("assets3d: картинка не сжалась", exc_info=True)
        return None


def shrink_textures(doc: dict, bin_: bytes, main_max: int = MAIN_MAX, small_max: int = SMALL_MAX) -> tuple[dict, bytes, dict]:
    """Все картинки — в WebP поменьше; буфер собирается заново, остальные виды — байт в байт."""
    roles = image_roles(doc)
    views = doc.get("bufferViews") or []
    replace: dict[int, bytes] = {}
    converted: list[int] = []
    for i, im in enumerate(doc.get("images") or []):
        if "bufferView" not in im:
            continue
        raw = view_bytes(doc, bin_, im["bufferView"])
        small = shrink_image(raw, small_max if roles.get(i) == "small" else main_max)
        if small is None:
            continue
        if len(small) >= len(raw) and (im.get("mimeType") in ("image/png", "image/jpeg")):
            continue  # уже маленькая: оставить как есть
        replace[im["bufferView"]] = small
        im["mimeType"] = "image/webp"
        converted.append(i)
    if not replace:
        return doc, bin_, {"converted": 0}
    out = bytearray()
    for idx, bv in enumerate(views):
        data = replace.get(idx)
        if data is None:
            data = view_bytes(doc, bin_, idx)
        bv["byteOffset"] = len(out)
        bv["byteLength"] = len(data)
        bv["buffer"] = 0
        out += data
        out += b"\0" * (-len(out) % 4)
    doc["buffers"] = [{"byteLength": len(out)}]
    conv = set(converted)
    for t in doc.get("textures") or []:
        if t.get("source") in conv:
            t.setdefault("extensions", {})["EXT_texture_webp"] = {"source": t.pop("source")}
    if conv:
        for key in ("extensionsUsed", "extensionsRequired"):
            items = list(doc.get(key) or [])
            if "EXT_texture_webp" not in items:
                items.append("EXT_texture_webp")
            doc[key] = items
    return doc, bytes(out), {"converted": len(converted)}


# --------------------------------------------------------------------------- превью целиком


def node_bin() -> str:
    try:
        from app.scene3d import node_bin as nb

        return nb()
    except Exception:  # noqa: BLE001
        return "node"


def meshopt(src: Path, dst: Path, timeout: int = 180) -> dict | None:
    """Сжать геометрию (node/assets3d/meshopt.mjs); None — не вышло, тогда превью без этого шага."""
    try:
        proc = subprocess.run([node_bin(), str(MESHOPT_SCRIPT), str(src), str(dst)], capture_output=True, text=True,
                              timeout=timeout, cwd=str(ROOT))
    except (OSError, subprocess.TimeoutExpired):
        log.warning("assets3d: meshopt не запустился", exc_info=True)
        return None
    if proc.returncode != 0 or not dst.is_file():
        log.warning("assets3d: meshopt упал: %s", (proc.stderr or proc.stdout)[-800:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def vrm_thumbnail(doc: dict, bin_: bytes) -> bytes | None:
    """Обложка из метаданных VRM (VRoid кладёт портрет) — исходные байты картинки или None."""
    ext = doc.get("extensions") or {}
    img = None
    if "VRMC_vrm" in ext:
        img = (ext["VRMC_vrm"].get("meta") or {}).get("thumbnailImage")
    elif "VRM" in ext:
        img = _tex_image(doc, (ext["VRM"].get("meta") or {}).get("texture"))
    images = doc.get("images") or []
    if not isinstance(img, int) or not 0 <= img < len(images) or "bufferView" not in images[img]:
        return None
    return view_bytes(doc, bin_, images[img]["bufferView"])


def build_preview(src: Path, dst: Path, *, thumb: Path | None = None, geometry: bool = True) -> dict:
    """Сделать preview.glb (и обложку из VRM, если есть). Возвращает справку о модели и размеры."""
    doc, bin_ = read_model(src)
    info = inspect(doc, bin_)
    if thumb is not None:
        raw = vrm_thumbnail(doc, bin_)
        small = shrink_image(raw, 384, 80) if raw else None
        if small:
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(small)
    doc, bin_, tex = shrink_textures(doc, bin_)
    dst.parent.mkdir(parents=True, exist_ok=True)
    stage = dst.with_name(dst.stem + ".tex.glb")
    stage.write_bytes(write_glb(doc, bin_))
    geo = meshopt(stage, dst) if geometry else None
    if geo is None:
        stage.replace(dst)
    else:
        stage.unlink(missing_ok=True)
    prev_doc, prev_bin = read_model_lenient(dst)
    info["preview_images"] = inspect(prev_doc, prev_bin)["images"] if prev_doc else []
    info["textures_converted"] = tex.get("converted", 0)
    info["geometry_compressed"] = bool(geo and geo.get("compressed"))
    return info


def read_model_lenient(path: Path) -> tuple[dict | None, bytes | None]:
    try:
        return read_model(path)
    except (ModelError, OSError, ValueError):
        return None, None
