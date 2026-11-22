"""Ч.5: экран переписки списком (``chat|{json}``, ``app/series3d_chase.chat_spec``) — для вставки во весь кадр.

Строка состояния, имя чата, сообщения снизу вверх (последнее — внизу): исходящие — синие справа со временем под ними,
входящие — серые слева, фото — картинка в пузыре (``photos[ключ]`` — PNG, снятый заранее, например комната героя).
Все размеры — в системе телефона (×PHONE_SCALE в мире), как у ``series_scene.chat_screen``; буквы — шрифтом с честной
кириллицей (Segoe), без сильного свечения (под bloom расплывается). ``scroll`` — содержимое за план чуть уезжает вверх:
он листает переписку.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import bmesh
import bpy

import hero_shot as kit

LINE, TEXT, WRAP = 0.0056, 0.0045, 12
TOP, BOTTOM = 0.0222, -0.0290          # поле сообщений (z), ниже шапки; снизу запас — кулак держит телефон за низ
BUB_W = 0.0275
Y_TEXT, Y_PLATE = -0.0063, -0.0058


def _wrap(text: str, width: int, max_lines: int = 3) -> str:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:max_lines])


def _quad(name, w, h, loc, mat):
    """Плоскость с UV (под картинку) лицом в −Y телефона."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    vs = [bm.verts.new((x * w / 2, 0.0, z * h / 2)) for x, z in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    f = bm.faces.new(vs)
    for loop, (u, v) in zip(f.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop[uv].uv = (u, v)
    f.normal_update()
    if f.normal.y > 0:
        f.normal_flip()
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = kit.link(bpy.data.objects.new(name, me))
    ob.location = loc
    return ob


def photo_mat(name: str, path: str | None):
    """Свечение картинкой (three.js берёт карту из GLB — ``series_export.material_info``: etex)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for nd in list(nt.nodes):
        nt.nodes.remove(nd)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    if path and Path(path).is_file():
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(path))
        tex.image.pack()
        nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    else:
        em.inputs["Color"].default_value = (0.2, 0.22, 0.26, 1.0)
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def chat_list(j: int, value: str, phone, font, photos: dict | None = None) -> tuple[list, object]:
    """Объекты экрана (дети пустышки-группы на телефоне) и сама группа: ``(части, группа)``. Группу показывают и
    прячут ключами масштаба, ``scroll`` — её сдвиг вверх."""
    spec = json.loads(value[5:])
    group = kit.link(bpy.data.objects.new(f"chat_group{j}", None))
    group.parent = phone
    content = kit.link(bpy.data.objects.new(f"chat_content{j}", None))
    content.parent = group
    ink = kit.emission_mat(f"chat_ink{j}", (1.0, 1.0, 1.0), 1.4)
    dim = kit.emission_mat(f"chat_dim{j}", (0.80, 0.85, 0.95), 1.0)
    red = kit.emission_mat(f"chat_red{j}", (1.0, 0.24, 0.18), 1.8)
    blue = kit.emission_mat(f"chat_blue{j}", (0.06, 0.30, 0.95), 0.55)
    grey = kit.emission_mat(f"chat_grey{j}", (0.24, 0.26, 0.32), 0.55)
    bar = kit.emission_mat(f"chat_bar{j}", (0.10, 0.12, 0.18), 0.5)
    parts = []

    def tx(name, body, size, x, z, mat, parent, align="CENTER"):
        ob = kit.text(f"chat_{name}{j}", body, size, (x, Y_TEXT, z), mat, font=font, align=align)
        ob.data.resolution_u = 24
        ob.parent = parent
        parts.append(ob)
        return ob

    def plate(name, w, h, x, z, mat, parent):
        ob = kit.box(f"chat_{name}{j}", (w, 0.0004, h), (x, Y_PLATE, z), mat, bevel=0.0)
        ob.parent = parent
        parts.append(ob)
        return ob
    lock = spec.get("lock")
    if isinstance(lock, dict):
        # экран блокировки: строка состояния, крупные часы и заряд (телефон загорелся в темноте)
        status = str(spec.get("status") or "")
        if status:
            tx("status", status, 0.0040, 0.0, 0.0352, red if status.startswith("нет") else dim, group)
        tx("clock", str(lock.get("time") or ""), 0.0105, 0.0, 0.0190, ink, group)
        batt = str(lock.get("battery") or "")
        if batt:
            low = kit.emission_mat(f"chat_low{j}", (1.0, 0.28, 0.18), 1.6)
            plate("batt", 0.0150, 0.0070, 0.0, -0.0010, grey, group)
            tx("batt", batt, 0.0068, 0.0, -0.0010, low, group)
        if lock.get("note"):
            tx("lnote", str(lock["note"]), 0.0036, 0.0, -0.0090, dim, group)
        return parts, (group, content, 0.0)
    plate("head", 0.036, 0.0115, 0.0, 0.0300, bar, group)
    status = str(spec.get("status") or "")
    if status:
        tx("status", status, 0.0040, 0.0, 0.0352, red if status.startswith("нет") else dim, group)
    tx("to", str(spec.get("to") or "Чат"), 0.0058, 0.0, 0.0296, ink, group)
    # снизу вверх: последнее сообщение — у нижнего края (в начале плана — на scroll ниже, потом уезжает вверх)
    z = BOTTOM + float(spec.get("scroll") or 0.0)
    for k, it in reversed(list(enumerate(spec.get("items") or []))):
        note = str(it.get("note") or "")
        if it.get("photo"):
            ph_w, ph_h = 0.0215, 0.0262
            need = ph_h + 0.0030 + (0.0042 if note else 0.0)
            if z + need > TOP:
                break
            if note:
                tx(f"pnote{k}", note, 0.0033, 0.0035, z + 0.0016, dim, content)
                z += 0.0040
            frame = plate(f"pframe{k}", ph_w + 0.0016, ph_h + 0.0016, 0.0047, z + ph_h / 2, blue, content)
            _ = frame
            q = _quad(f"chat_photo{k}{j}", ph_w, ph_h, (0.0047, Y_PLATE - 0.0003, z + ph_h / 2),
                      photo_mat(f"chat_photo_mat{k}{j}", (photos or {}).get(str(it["photo"]))))
            q.parent = content
            parts.append(q)
            z += ph_h + 0.0034
            continue
        out = "out" in it
        body = str(it.get("out") if out else it.get("in") or "")
        lines = _wrap(body[:1].upper() + body[1:], WRAP, 4)
        n_ = lines.count("\n") + 1
        bh = LINE * n_ + 0.0034
        need = bh + 0.0030 + (0.0040 if note else 0.0)
        if z + need > TOP:
            break
        if note:
            tx(f"note{k}", note, 0.0033, 0.0060 if out else -0.0060, z + 0.0016, dim, content)
            z += 0.0040
        x = 0.0042 if out else -0.0042
        plate(f"bub{k}", BUB_W, bh, x, z + bh / 2, blue if out else grey, content)
        tx(f"txt{k}", lines, TEXT, x, z + bh / 2, ink, content)
        z += bh + 0.0030
    return parts, (group, content, float(spec.get("scroll") or 0.0))


def key_chat(group_info, vis: list[int], frames: list[int], shot_span: tuple[int, int] | None):
    """Показать группу в её кадрах (масштаб), содержимое уезжает вверх на ``scroll`` за план («листает»)."""
    group, content, scroll = group_info
    kit.key_vec(group, "scale", frames, [((1, 1, 1) if v else (0, 0, 0)) for v in vis], interp="CONSTANT")
    if scroll and shot_span:
        a, b = shot_span
        zs = []
        for i in range(len(frames)):
            x = min(1.0, max(0.0, (i - a) / max(1, b - a)))
            x = x * x * (3 - 2 * x)
            zs.append(-scroll * (1.0 - x))
        kit.fcurve(content, "location", 2, frames, zs)
    _ = math
