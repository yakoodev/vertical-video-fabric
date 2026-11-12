# --- Stage 1: build the React SPA (only dist/ is copied into the final image) ---
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend ./
RUN npm run build
# Motion compositor: the same engine as the editor preview (frontend/src/motion),
# bundled into one .mjs for Node; @napi-rs/canvas stays external (runtime npm install).
COPY frontend/src/motion /motion/frontend/src/motion
COPY node/motion-render.ts /motion/node/motion-render.ts
COPY node/motion-render /motion/node/motion-render
RUN npx --yes esbuild@0.28.2 /motion/node/motion-render.ts --bundle --platform=node --format=esm \
    --target=node20 --external:@napi-rs/canvas --outfile=/motion/motion-render.mjs --log-level=warning
# 3D scenes (node/scene3d): the three.js engine is bundled into one browser script that the
# headless Chromium renderer loads (node/scene3d/render.mjs); three is a build-time dependency only.
WORKDIR /scene3d
RUN npm init -y >/dev/null && npm install --no-audit --no-fund three@0.185.0 @pixiv/three-vrm@3.5.5 esbuild@0.28.2
COPY node/scene3d/src /scene3d/src
RUN npx esbuild /scene3d/src/engine.ts --bundle --format=iife --platform=browser --target=chrome110 --minify     --outfile=/scene3d/dist/scene3d.js --log-level=warning
# VRM-персонажи 3D-серий (node/vrm, app/s3d_render.py): свой бандл; src импортирует ../../scene3d/src, поэтому
# лежит рядом (/vrm/src), а three и three-vrm берутся из /scene3d/node_modules (NODE_PATH).
COPY node/vrm/src /vrm/src
RUN NODE_PATH=/scene3d/node_modules npx esbuild /vrm/src/main.ts --bundle --format=iife --platform=browser --target=chrome110 \
    --minify --outfile=/vrm/dist/vrm.js --log-level=warning
# PO-токены YouTube (bgutil, режим «скрипт»): yt-dlp сам запускает `node build/generate_once.js`
# на каждый токен (app/ytdlp_net.py, BGUTIL_POT_HOME). Версия совпадает с pip-плагином
# bgutil-ytdlp-pot-provider в requirements.txt; tsc — только при сборке, в образ — build/ и
# рабочие node_modules (canvas — готовая сборка под linux-x64, та же база bookworm, что у образа).
# Архив релиза закреплён sha256.
ARG BGUTIL_POT_VERSION=2.0.2
ARG BGUTIL_POT_SHA256=1f2ae9ec39405b8e80397dfc2551f58deed09d8dff4b2b8fbc314df8e091c0a1
ADD --checksum=sha256:${BGUTIL_POT_SHA256} \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/${BGUTIL_POT_VERSION}.tar.gz /tmp/bgutil.tar.gz
WORKDIR /bgutil
RUN tar xzf /tmp/bgutil.tar.gz --strip-components=1 && rm /tmp/bgutil.tar.gz \
    && cd server && npm ci --include=dev --no-audit --no-fund && npx tsc \
    && npm prune --omit=dev --no-audit --no-fund
WORKDIR /build

# --- Stage 2: runtime ---
FROM node:24-bookworm-slim

ARG TIKTOK_UPLOADER_REF=73475dbb67be5d8e5e7181af665fbf7f0db7fff4

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    DATA_DIR=/data \
    NODE_ENV=production \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    PATH="/opt/venv/bin:${PATH}" \
    TIKTOK_VENDOR_ROOT=/opt/TiktokAutoUploader \
    BGUTIL_POT_HOME=/opt/bgutil-pot/server

WORKDIR /app

# chromium — и TikTok-подпись, и SVG → webm (app/svg_webm.py, node/svg-to-webm.mjs, headless покадрово).
# fonts-dejavu-core — запасной шрифт Chromium (~1.5 МБ): шрифты сервиса конвертер подключает сам, а глифы
# своих SVG вне них без системного шрифта рисовались бы квадратами.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        fonts-dejavu-core \
        ffmpeg \
        git \
        libglib2.0-0 \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Content packs for motion layers (Twemoji, stickers, SFX, meme templates) and CC0 3D models for
# node/scene3d (Kenney furniture, Quaternius characters + animations, ~55 MB):
# fetched at build time with pinned versions + sha256 (stdlib-only script, data
# only — nothing downloaded is executed). Placed early and copied alone so the
# layer stays cached until the script itself changes. PACKS_SKIP=imgflip skips
# the meme templates if i.imgflip.com is unreachable from the build network;
# PACKS_SKIP=kenney-furniture,quaternius-characters,quaternius-animations skips the 3D models.
ARG PACKS_SKIP=""
COPY tools/fetch_packs.py /app/tools/fetch_packs.py
RUN python3 /app/tools/fetch_packs.py /app/vendor/packs --skip "${PACKS_SKIP}"
# Background music (CC0, pinned sha256) — own layer: editing the track list
# does not re-download every pack above.
COPY tools/fetch_music.py /app/tools/fetch_music.py
RUN python3 /app/tools/fetch_music.py /app/vendor/packs
# Montage SFX (CC0, pinned sha256): rebuilds the "freesound" pack from the curated
# list in fetch_sfx.py on top of the one above and swaps its packs.json entry —
# editing the sound list does not re-download every pack.
COPY tools/fetch_sfx.py /app/tools/fetch_sfx.py
RUN python3 /app/tools/fetch_sfx.py /app/vendor/packs

RUN python3 -m venv /opt/venv

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# DNN models for the deterministic autofocus, vendored so the build needs no
# network (both degrade gracefully at runtime if absent):
#  - YuNet: face detection (Haar fallback).
#  - YOLOX (OpenCV Zoo, Apache-2.0): person/object detection — the main subject signal.
ENV YUNET_MODEL=/opt/models/yunet.onnx \
    YOLOX_MODEL=/opt/models/yolox_nano.onnx
COPY vendor/yunet.onnx /opt/models/yunet.onnx
COPY vendor/yolox_nano.onnx /opt/models/yolox_nano.onnx
# Fonts for motion layers (OFL, committed to the repo — see vendor/fonts/LICENSES.md).
COPY vendor/fonts /app/vendor/fonts

RUN git clone https://github.com/makiisthenes/TiktokAutoUploader.git /opt/TiktokAutoUploader \
    && cd /opt/TiktokAutoUploader \
    && git checkout ${TIKTOK_UPLOADER_REF} \
    && python3 -c "from pathlib import Path; p=Path('/opt/TiktokAutoUploader/tiktok_uploader/tiktok-signature/index.js'); s=p.read_text(); s=s.replace('\"--start-maximized\",', '\"--start-maximized\",\\n    \"--no-sandbox\",\\n    \"--disable-setuid-sandbox\",'); s=s.replace('headless: true,\\n      args:', 'headless: true,\\n      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,\\n      args:'); p.write_text(s)" \
    && cd /opt/TiktokAutoUploader/tiktok_uploader/tiktok-signature \
    && npm install

COPY package.json package-lock.json* /app/
RUN npm install --omit=dev

COPY app /app/app
# Библиотека движений VRM: перенос клипов паков и папки anim_inbox на скелет VRM (app/animlib.sync).
COPY tools/anim_retarget.py tools/anim_library.py /app/tools/
# Бренд-ассеты (значок Витубики для концовки историй, app/vtubika_endcard.py).
COPY assets /app/assets
# Плейбуки агентов (get_playbook, MCP prompts) читаются с диска — app/playbooks.py.
COPY docs/playbooks /app/docs/playbooks
# Миниатюры 3D-пресетов для пикеров редактора 3D-серии (≈1 МБ, app/s3d_api.py).
COPY docs/3d/img /app/docs/3d/img
COPY node /app/node
COPY tests /app/tests
COPY pytest.ini /app/pytest.ini
COPY --from=frontend-build /build/dist /app/frontend/dist
COPY --from=frontend-build /motion/motion-render.mjs /app/node/motion-render.mjs
COPY --from=frontend-build /scene3d/dist/scene3d.js /app/node/scene3d/dist/scene3d.js
COPY --from=frontend-build /vrm/dist/vrm.js /app/node/vrm/dist/vrm.js
COPY --from=frontend-build /bgutil/server /opt/bgutil-pot/server

RUN mkdir -p /data && chown -R node:node /data /app

EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
