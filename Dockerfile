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
RUN npx --yes esbuild@0.28.2 /motion/node/motion-render.ts --bundle --platform=node --format=esm \
    --target=node20 --external:@napi-rs/canvas --outfile=/motion/motion-render.mjs --log-level=warning

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
    TIKTOK_VENDOR_ROOT=/opt/TiktokAutoUploader

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        ffmpeg \
        git \
        libglib2.0-0 \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Content packs for motion layers (Twemoji, stickers, SFX, meme templates):
# fetched at build time with pinned versions + sha256 (stdlib-only script, data
# only — nothing downloaded is executed). Placed early and copied alone so the
# layer stays cached until the script itself changes. PACKS_SKIP=imgflip skips
# the meme templates if i.imgflip.com is unreachable from the build network.
ARG PACKS_SKIP=""
COPY tools/fetch_packs.py /app/tools/fetch_packs.py
RUN python3 /app/tools/fetch_packs.py /app/vendor/packs --skip "${PACKS_SKIP}"
# Background music (CC0, pinned sha256) — own layer: editing the track list
# does not re-download every pack above.
COPY tools/fetch_music.py /app/tools/fetch_music.py
RUN python3 /app/tools/fetch_music.py /app/vendor/packs

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
COPY node /app/node
COPY tests /app/tests
COPY pytest.ini /app/pytest.ini
COPY --from=frontend-build /build/dist /app/frontend/dist
COPY --from=frontend-build /motion/motion-render.mjs /app/node/motion-render.mjs

RUN mkdir -p /data && chown -R node:node /data /app

EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
