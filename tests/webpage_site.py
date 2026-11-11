"""Маленький сайт для проверки снимка страницы (app/webpages.py) без интернета: магазин с ценой, кнопкой «Купить»,
баннером cookie, ленивой картинкой, фоном из css и скриптом, который дописывает блок. Плюс страница-капча.

``build_site(dir)`` пишет файлы, ``serve(dir)`` — локальный HTTP-сервер в фоне (контекстный менеджер → базовый адрес).
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import threading
from pathlib import Path

import cv2
import numpy as np


def _png(path: Path, w: int, h: int, bgr: tuple[int, int, int]) -> None:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = bgr
    cv2.rectangle(img, (w // 6, h // 6), (w - w // 6, h - h // 6), (255, 255, 255), max(2, w // 40))
    path.write_bytes(cv2.imencode(".png", img)[1].tobytes())


INDEX = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Витрина — тестовый магазин</title>
<link rel="stylesheet" href="/css/site.css">
<link rel="preload" href="/img/hero.png" as="image">
</head><body>
<header class="top"><b>Витрина</b><a href="/cart" class="cart">Корзина</a></header>
<section class="hero"><h1 id="title">Модели для витубера</h1><p>Готовые аватары за один вечер</p></section>
<main>
  <article class="card" id="product">
    <img class="pic" src="/img/model.png" alt="Модель Мафую" width="300" height="200">
    <h2>Модель «Мафую»</h2>
    <p class="desc">Риг лица, 12 эмоций, готова к стриму. Подходит для OBS и VTube Studio.</p>
    <div class="price"><span class="amount">1 990</span> <span class="cur">₽</span></div>
    <button class="buy" onclick="alert('нельзя')">Купить</button>
  </article>
  <div class="bgblock">Фон из css</div>
  <img class="lazy" loading="lazy" src="/img/lazy.png" alt="Ленивая" width="300" height="200" style="margin-top:900px">
  <div id="from-js"></div>
</main>
<div class="cookie" id="cookie">Мы используем cookie <button>Принять</button></div>
<script>document.getElementById('from-js').textContent = 'Блок дописан скриптом';
window.addEventListener('load', () => { window.__ran = true; });</script>
</body></html>
"""

CSS = """
html, body { margin: 0; font-family: Arial, sans-serif; background: #f6f3ff; color: #1b1530; }
.top { display: flex; justify-content: space-between; padding: 14px 18px; background: #2a1f5c; color: #fff; }
.top a { color: #ffd400; }
.hero { padding: 40px 18px; background: linear-gradient(135deg, #7c3aed, #ec4899); color: #fff; }
.card { margin: 18px; padding: 16px; background: #fff; border-radius: 16px; box-shadow: 0 6px 20px rgba(0,0,0,.08); }
.pic { width: 100%; height: auto; border-radius: 12px; }
.price { font-size: 32px; font-weight: 800; margin: 10px 0; color: #7c3aed; }
.buy { width: 100%; padding: 14px; border: 0; border-radius: 12px; background: #ff2d55; color: #fff; font-size: 20px; }
.bgblock { margin: 18px; height: 160px; background: url("../img/bg.png") center/cover; color: #fff; padding: 12px; border-radius: 12px; }
.cookie { position: fixed; left: 0; right: 0; bottom: 0; padding: 14px; background: #111; color: #fff; }
"""

CAPTCHA = """<!doctype html><html><head><meta charset="utf-8"><title>Attention Required</title></head>
<body><h1>Подтвердите, что вы не робот</h1><p>Нажмите на все светофоры.</p></body></html>
"""


def build_site(root: Path) -> Path:
    (root / "css").mkdir(parents=True, exist_ok=True)
    (root / "img").mkdir(exist_ok=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "captcha.html").write_text(CAPTCHA, encoding="utf-8")
    (root / "css" / "site.css").write_text(CSS, encoding="utf-8")
    _png(root / "img" / "model.png", 300, 200, (60, 160, 40))
    _png(root / "img" / "hero.png", 120, 80, (200, 80, 40))
    _png(root / "img" / "lazy.png", 300, 200, (40, 40, 220))
    _png(root / "img" / "bg.png", 200, 100, (180, 40, 160))
    return root


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # noqa: D401 - без шума в выводе тестов
        pass


@contextlib.contextmanager
def serve(root: Path):
    handler = functools.partial(_Quiet, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
