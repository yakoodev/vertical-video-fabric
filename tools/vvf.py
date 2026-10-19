#!/usr/bin/env python3
"""vvf — work with Vertical Video Fabric clips from outside the service.

Made for an agent (Claude / Codex) or a person in a terminal: pull a clip
file, edit it, push it back, render, read the quality report — without touching
the service's code or database. Standard library only.

    export VVF_URL=http://localhost:8088      # default
    export VVF_TOKEN=...                       # or VVF_TOKEN_FILE=/path/to/api_token.txt

    vvf.py sources                             # projects
    vvf.py clips 50 --favorites                # clips of a project
    vvf.py pull 812 -o clip-812.json           # the clip file
    vvf.py check clip-812.json                 # validate without applying
    vvf.py push clip-812.json                  # apply (all-or-nothing)
    vvf.py render 812                          # render with the file's settings, prints QC
    vvf.py qc 431                              # QC report of a finished render
    vvf.py download 431 -o out.mp4             # the rendered video
    vvf.py frame 50 --at 812.4 -o f.jpg        # a still of the source (to look at)
    vvf.py frame 50 --at 812.4 --x 0.62 -o c.jpg  # exactly the 9:16 window at x
    vvf.py schema                              # JSON Schema of the clip file
    vvf.py layers 812                          # motion layers + camera of a clip
    vvf.py layers 812 --json > layers.json     # the same as an editable file
    vvf.py layers-set 812 layers.json          # validate, then replace layers (+camera)
    vvf.py frame-motion 812 --at 3.2 -o f.png  # a frame of the finished look (layers, camera)
    vvf.py contact 812 -n 8 -o sheet.jpg       # contact sheet: the whole edit at a glance
    vvf.py agent 812 "сделай динамичнее"       # the built-in AI editor; waits, prints steps

See docs/AGENTS.md for the workflow and the rules of the file.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import List, Optional

BASE = os.environ.get("VVF_URL", "http://localhost:8088").rstrip("/")

# Russian output on a Windows console (cp866/cp1251 can't print "—" or "×"):
# UTF-8 with replacement, on any Python 3 — reconfigure() only exists from 3.7.
def _utf8(stream):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            return stream
        except (ValueError, OSError):
            return stream
    if hasattr(stream, "buffer"):
        import io

        return io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    return stream


sys.stdout = _utf8(sys.stdout)
sys.stderr = _utf8(sys.stderr)


def _token() -> str:
    token = os.environ.get("VVF_TOKEN", "").strip()
    path = os.environ.get("VVF_TOKEN_FILE", "").strip()
    if not token and path:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    return token


def call(method: str, path: str, body: Optional[object] = None, *, raw: bool = False, timeout: int = 3600):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method)
    token = _token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail).get("detail", detail)
        except ValueError:
            parsed = detail
        if isinstance(parsed, dict) and "problems" in parsed:
            print("Файл не принят:", file=sys.stderr)
            for p in parsed["problems"]:
                print(f"  - {p}", file=sys.stderr)
        else:
            print(f"HTTP {err.code}: {parsed}", file=sys.stderr)
        sys.exit(2)
    except urllib.error.URLError as err:
        print(f"Нет связи с {BASE}: {err.reason}", file=sys.stderr)
        sys.exit(2)
    return payload if raw else json.loads(payload or b"null")


def _print_qc(qc: Optional[dict]) -> None:
    if not qc:
        print("  QC: отчёта нет (рендер до появления проверки или ffmpeg не смог)")
        return
    lufs = qc.get("loudness_lufs")
    print(f"  QC: {'OK' if qc.get('ok') else 'есть замечания'}"
          f" · {qc.get('duration_sec')} с · {qc.get('width')}×{qc.get('height')}"
          f" · громкость {lufs if lufs is not None else '—'} LUFS · пик {qc.get('true_peak_dbfs')}")
    for issue in qc.get("issues") or []:
        print(f"    [{issue['level']}] {issue['text']}")


def cmd_sources(_args) -> None:
    for s in call("GET", "/api/sources"):
        print(f"{s['id']:>5}  {s.get('status', ''):10} {s.get('duration_sec') or 0:>8.0f} с  {s.get('original_filename', '')}")


def cmd_clips(args) -> None:
    plans = call("GET", f"/api/sources/{args.source}/clip-plans")
    shown = 0
    for p in plans:
        if args.favorites and not p.get("favorite"):
            continue
        if p.get("hidden") and not args.all:
            continue
        pieces = p.get("segments") or []
        total = sum(float(s["end_sec"]) - float(s["start_sec"]) for s in pieces)
        mark = "★" if p.get("favorite") else " "
        own = "⚙" if p.get("render_settings") else " "
        print(f"{p['id']:>5} {mark}{own} {p.get('status', ''):10} {len(pieces)} кус. {total:>5.0f} с  {p.get('title', '')}")
        shown += 1
    if not shown:
        print("нет клипов" + (" в избранном (★ ставит владелец в сервисе)" if args.favorites else ""), file=sys.stderr)


def cmd_pull(args) -> None:
    spec = call("GET", f"/api/clip-plans/{args.clip}/spec")
    text = json.dumps(spec, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"сохранено: {args.output}")
    else:
        print(text)


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except FileNotFoundError:
        print(f"нет файла: {path}", file=sys.stderr)
        sys.exit(2)
    except ValueError as err:
        print(f"{path}: это не JSON ({err})", file=sys.stderr)
        sys.exit(2)
    if not isinstance(spec, dict) or not isinstance(spec.get("clip_id"), int):
        print(f"{path}: нет clip_id — это не файл клипа (возьми его через `vvf.py pull`)", file=sys.stderr)
        sys.exit(2)
    return spec


def cmd_check(args) -> None:
    spec = _load(args.file)
    result = call("POST", f"/api/clip-plans/{spec['clip_id']}/spec/validate", spec)
    if result["ok"]:
        print("файл в порядке")
    else:
        print("проблемы:")
        for p in result["problems"]:
            print(f"  - {p}")
        sys.exit(1)


def cmd_push(args) -> None:
    spec = _load(args.file)
    result = call("PUT", f"/api/clip-plans/{spec['clip_id']}/spec", spec)
    changes = result.get("changes") or []
    print("применено: " + (", ".join(changes) if changes else "ничего не поменялось"))
    if args.write_back:
        with open(args.file, "w", encoding="utf-8") as f:
            f.write(json.dumps(result["spec"], ensure_ascii=False, indent=2) + "\n")


def cmd_render(args) -> None:
    print(f"рендер клипа {args.clip}… (ждём, это может занять минуты)")
    clip = call("POST", f"/api/clip-plans/{args.clip}/render", {})
    print(f"рендер {clip['id']}: {clip['status']}"
          + (f" · {clip.get('duration_sec'):.1f} с" if clip.get("duration_sec") else "")
          + (f" · ошибка: {clip.get('error')}" if clip.get("error") else ""))
    _print_qc(clip.get("qc"))
    if clip["status"] != "succeeded":
        sys.exit(1)


def cmd_qc(args) -> None:
    clip = call("GET", f"/api/clips/{args.render}")
    print(f"рендер {clip['id']}: {clip['status']}")
    _print_qc(clip.get("qc"))


def cmd_download(args) -> None:
    data = call("GET", f"/media/clips/{args.render}", raw=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"сохранено: {args.output} ({len(data) / 1e6:.1f} МБ)")


def cmd_frame(args) -> None:
    query = f"t={args.at}" + (f"&x={args.x}" if args.x is not None else f"&w={args.width}")
    data = call("GET", f"/api/sources/{args.source}/frame?{query}", raw=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"сохранено: {args.output}")


def _layer_line(layer: dict) -> str:
    props = layer.get("props") or {}
    what = props.get("text") or props.get("title") or props.get("char") or props.get("kind") or         props.get("asset_id") or props.get("src") or ""
    what = " ".join(str(what).split())[:40]
    flags = (" скрыт" if layer.get("hidden") else "") + (" 🔒" if layer.get("locked") else "")
    return (f"  {layer.get('id', '?'):<10} {layer.get('type', ''):8} {float(layer.get('start', 0)):6.2f}–"
            f"{float(layer.get('end', 0)):6.2f} с  x={layer.get('x')} y={layer.get('y')}  {what}{flags}")


def cmd_layers(args) -> None:
    render = call("GET", f"/api/clip-plans/{args.clip}/spec").get("render") or {}
    layers, camera = render.get("layers") or [], render.get("camera") or []
    if args.json:
        print(json.dumps({"layers": layers, "camera": camera}, ensure_ascii=False, indent=2))
        return
    print(f"слоёв: {len(layers)}")
    for layer in sorted(layers, key=lambda l: (float(l.get("start", 0)), l.get("track", 0))):
        print(_layer_line(layer))
    if camera:
        print("камера: " + ", ".join(f"{k.get('t')} с ×{k.get('zoom')}" for k in camera))


def cmd_layers_set(args) -> None:
    try:
        with open(args.file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as err:
        print(f"{args.file}: {err}", file=sys.stderr)
        sys.exit(2)
    if isinstance(data, list):
        data = {"layers": data}
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list):
        print("нужен список слоёв или объект {\"layers\": [...], \"camera\": [...]}", file=sys.stderr)
        sys.exit(2)
    body = {"layers": data["layers"]}
    if isinstance(data.get("camera"), list):
        body["camera"] = data["camera"]
    check = call("POST", f"/api/clip-plans/{args.clip}/layers/validate", body)
    if check.get("problems"):
        print("слои не приняты:")
        for p in check["problems"]:
            print(f"  - {p}")
        sys.exit(1)
    spec = call("GET", f"/api/clip-plans/{args.clip}/spec")
    spec["render"] = {**(spec.get("render") or {}), **body}
    result = call("PUT", f"/api/clip-plans/{args.clip}/spec", spec)
    render = result["spec"].get("render") or {}
    print(f"применено: слоёв {len(render.get('layers') or [])}, ключей камеры {len(render.get('camera') or [])}"
          " (Ctrl+Z в редакторе откатит)")


def cmd_frame_motion(args) -> None:
    body = {"layers": [], "camera": []} if args.no_layers else None
    data = call("POST", f"/api/clip-plans/{args.clip}/motion/frame?t={args.at}", body, raw=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"сохранено: {args.output}")


def cmd_contact(args) -> None:
    data = call("GET", f"/api/clip-plans/{args.clip}/contact-sheet?n={args.n}", None, raw=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"сохранено: {args.output}")


def cmd_agent(args) -> None:
    path = f"/api/clip-plans/{args.clip}/agent"
    started = call("POST", path, {"message": args.message})
    print(f"ИИ-монтажёр ({started.get('model')}) работает…", file=sys.stderr)
    shown = 0
    deadline = time.time() + args.timeout
    while True:
        chat = call("GET", path)
        reply = next((m for m in reversed(chat["messages"]) if m["id"] == started["message_id"]), None)
        steps = (reply or {}).get("steps") or []
        for step in steps[shown:]:
            if step.get("ok") is None:
                break
            mark = "✓" if step["ok"] else "✗"
            print(f"  {mark} {step.get('label')}")
            for problem in (step.get("problems") or [])[:5]:
                print(f"      - {problem}")
            shown += 1
        if not chat.get("running"):
            break
        if time.time() > deadline:
            call("POST", path + "/cancel", {})
            print("время вышло — ход остановлен", file=sys.stderr)
            sys.exit(1)
        time.sleep(1.5)
    reply = next((m for m in reversed(chat["messages"]) if m["id"] == started["message_id"]), None) or {}
    print()
    print(reply.get("text") or "(без ответа)")
    if reply.get("status") not in ("done", None):
        sys.exit(1)


def cmd_schema(_args) -> None:
    print(json.dumps(call("GET", "/api/clip-spec/schema"), ensure_ascii=False, indent=2))


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="vvf", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd")
    sub.required = True  # keyword form needs Python 3.7+; this works on 3.6 too
    sub.add_parser("sources", help="проекты").set_defaults(fn=cmd_sources)
    p = sub.add_parser("clips", help="клипы проекта")
    p.add_argument("source", type=int)
    p.add_argument("--favorites", action="store_true", help="только избранные")
    p.add_argument("--all", action="store_true", help="включая скрытые")
    p.set_defaults(fn=cmd_clips)
    p = sub.add_parser("pull", help="скачать файл клипа")
    p.add_argument("clip", type=int)
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("check", help="проверить файл, не применяя")
    p.add_argument("file")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("push", help="применить файл клипа")
    p.add_argument("file")
    p.add_argument("--write-back", action="store_true", help="перезаписать файл свежей версией с сервера")
    p.set_defaults(fn=cmd_push)
    p = sub.add_parser("render", help="отрендерить клип по его файлу")
    p.add_argument("clip", type=int)
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("qc", help="отчёт о качестве рендера")
    p.add_argument("render", type=int)
    p.set_defaults(fn=cmd_qc)
    p = sub.add_parser("download", help="скачать готовое видео")
    p.add_argument("render", type=int)
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_download)
    p = sub.add_parser("frame", help="кадр исходника: целиком или окно 9:16 при --x")
    p.add_argument("source", type=int)
    p.add_argument("--at", type=float, required=True, help="секунда исходника")
    p.add_argument("--x", type=float, help="центр кадра 9:16 (0..1) — покажет ровно то, что останется")
    p.add_argument("--width", type=int, default=960)
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_frame)
    sub.add_parser("schema", help="JSON Schema файла клипа").set_defaults(fn=cmd_schema)
    p = sub.add_parser("layers", help="моушн-слои и камера клипа")
    p.add_argument("clip", type=int)
    p.add_argument("--json", action="store_true", help="вывести JSON {layers, camera} для правки")
    p.set_defaults(fn=cmd_layers)
    p = sub.add_parser("layers-set", help="заменить слои (и камеру) клипа из файла JSON")
    p.add_argument("clip", type=int)
    p.add_argument("file", help="[слои] или {\"layers\": [...], \"camera\": [...]}")
    p.set_defaults(fn=cmd_layers_set)
    p = sub.add_parser("frame-motion", help="кадр готового вида клипа (слои + камера) в PNG")
    p.add_argument("clip", type=int)
    p.add_argument("--at", type=float, required=True, help="секунда готового клипа")
    p.add_argument("--no-layers", action="store_true", help="без слоёв и камеры — только основа")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_frame_motion)
    p = sub.add_parser("contact", help="лист кадров клипа (JPEG-сетка) — весь монтаж одним взглядом")
    p.add_argument("clip", type=int)
    p.add_argument("-n", type=int, default=8, help="сколько кадров (2..16)")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_contact)
    p = sub.add_parser("agent", help="попросить встроенного ИИ-монтажёра и дождаться ответа")
    p.add_argument("clip", type=int)
    p.add_argument("message")
    p.add_argument("--timeout", type=float, default=900, help="секунд ждать (по умолчанию 900)")
    p.set_defaults(fn=cmd_agent)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Output piped into `head` & co. and closed early — not an error.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
