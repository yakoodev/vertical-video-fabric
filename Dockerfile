# --- Stage 1: build the React SPA (only dist/ is copied into the final image) ---
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend ./
RUN npm run build

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

RUN python3 -m venv /opt/venv

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# YuNet DNN face-detection model for the deterministic autofocus (vendored so the
# build needs no network; falls back to Haar cascades at runtime if absent).
ENV YUNET_MODEL=/opt/models/yunet.onnx
COPY vendor/yunet.onnx /opt/models/yunet.onnx

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

RUN mkdir -p /data && chown -R node:node /data /app

EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
