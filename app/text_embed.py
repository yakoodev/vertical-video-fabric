"""Смысловые векторы коротких фраз — локально, на CPU (onnxruntime + tokenizers, без torch и платных API).

Зачем: мем к реплике игрока подбирается по смыслу, а не по совпавшим словам («Альцгеймер у тебя» → Малышева
«…ужасная болезнь»: общих слов нет, смысл — «болезнь»). Gemini для этого недоступен, платные шлюзы запрещены.

Модель — многоязычный кодировщик предложений в ONNX int8 (≈ 118 МБ + токенизатор 17 МБ), 384 числа на фразу,
≈ 5–15 мс на короткую фразу на CPU в два потока. Качается с Hugging Face при первом вызове в данные сервиса
(``DATA_DIR/embed-models`` — рядом с ``whisper-models``; свой каталог — ``EMBED_MODEL_DIR``), дальше без сети.
Модель живёт в памяти, пока нужна: через ``IDLE_UNLOAD_SEC`` простоя выгружается (память машины небольшая).

Не загрузилась (нет сети при первом запуске, нет onnxruntime/tokenizers, ``EMBED_MODEL=off``) — ``embed`` отдаёт
None, и вызывающий работает по словам, как раньше (детерминированный запасной путь). Повторная попытка — не
раньше ``RETRY_SEC``.

    embed(["фраза", …], kind="query") -> np.ndarray (n × dim, нормированы) | None
    similarity(query_vec, matrix) -> косинусы

В тестах — ``EMBED`` (подмена кодировщика: (texts, kind) -> векторы).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger(__name__)

# Обе — XLM-R-токенизатор, 384 измерения, int8 ≈ 118 МБ. e5 — с приставками «query: »/«passage: »
# (без них качество ниже); выбор и замеры — tools/meme_semantic_eval.py.
MODELS: dict[str, dict[str, Any]] = {
    "e5-small": {"repo": "Xenova/multilingual-e5-small", "revision": "761b726dd34fb83930e26aab4e9ac3899aa1fa78",
                 "onnx": "onnx/model_quantized.onnx", "query": "query: ", "passage": "query: "},
    "minilm": {"repo": "Xenova/paraphrase-multilingual-MiniLM-L12-v2",
               "revision": "2c4055b12046f11709e9df2c122e59ffbdc2f900",
               "onnx": "onnx/model_quantized.onnx", "query": "", "passage": ""},
}
DEFAULT_MODEL = "e5-small"
MAX_TOKENS = 128
BATCH = 32
THREADS = 2
IDLE_UNLOAD_SEC = 300.0
RETRY_SEC = 600.0

# Подмена в тестах: (texts, kind) -> матрица n × dim (нормировать не обязательно). None — настоящая модель.
EMBED: Callable[[list[str], str], Any] | None = None

_lock = threading.Lock()
_model: "_Encoder | None" = None
_failed_at: float = 0.0
_failed_name: str = ""
_timer: threading.Timer | None = None


def model_name() -> str:
    """Имя модели (``EMBED_MODEL``: e5-small | minilm | off)."""
    return (os.environ.get("EMBED_MODEL") or DEFAULT_MODEL).strip().lower()


def model_dir() -> Path:
    """Каталог моделей: ``EMBED_MODEL_DIR`` или ``DATA_DIR/embed-models`` (кэш Hugging Face внутри)."""
    custom = os.environ.get("EMBED_MODEL_DIR")
    if custom:
        return Path(custom)
    from app.settings import settings

    return settings.data_dir / "embed-models"


class _Encoder:
    def __init__(self, name: str):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        spec = MODELS[name]
        self.name = name
        self.spec = spec
        onnx_path, tok_path = _files(spec)
        self.tokenizer = Tokenizer.from_file(str(tok_path))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("<pad>") or 1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(onnx_path), options, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def encode(self, texts: list[str], kind: str) -> Any:
        import numpy as np

        prefix = self.spec.get(kind, "")
        out = []
        for i in range(0, len(texts), BATCH):
            chunk = [prefix + t for t in texts[i:i + BATCH]]
            enc = self.tokenizer.encode_batch(chunk)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            out.append((hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-6))
        return np.concatenate(out, axis=0) if out else np.zeros((0, 384), dtype=np.float32)


def _files(spec: dict) -> tuple[Path, Path]:
    """Файлы модели из кэша; нет — скачать (один раз, ≈ 135 МБ)."""
    from huggingface_hub import hf_hub_download

    folder = model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    got = []
    for name in (spec["onnx"], "tokenizer.json"):
        kwargs = {"repo_id": spec["repo"], "filename": name, "revision": spec["revision"], "cache_dir": str(folder)}
        try:
            got.append(Path(hf_hub_download(local_files_only=True, **kwargs)))
        except Exception:  # noqa: BLE001 - нет в кэше — качаем
            _log.info("text_embed: качаю %s/%s в %s", spec["repo"], name, folder)
            got.append(Path(hf_hub_download(**kwargs)))
    return got[0], got[1]


def _schedule_unload() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(IDLE_UNLOAD_SEC, unload)
    _timer.daemon = True
    _timer.start()


def unload() -> None:
    """Выгрузить модель (простой, тесты)."""
    global _model
    with _lock:
        _model = None


def _encoder() -> "_Encoder | None":
    global _model, _failed_at, _failed_name
    name = model_name()
    if name in ("off", "none", "0", "") or name not in MODELS:
        return None
    with _lock:
        if _model is not None and _model.name == name:
            return _model
        if _failed_name == name and time.monotonic() - _failed_at < RETRY_SEC:
            return None
        try:
            _model = _Encoder(name)
        except Exception as exc:  # noqa: BLE001 - нет модели — работаем по словам
            _failed_at, _failed_name = time.monotonic(), name
            _log.warning("text_embed: модель %s не загрузилась (%s) — подбор по словам", name, str(exc)[:200])
            return None
        return _model


def available() -> bool:
    """Модель есть (подмена в тестах или загрузилась)."""
    return EMBED is not None or _encoder() is not None


def _normalize(matrix: Any) -> Any:
    import numpy as np

    arr = np.asarray(matrix, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[None, :]
    return arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-9)


def embed(texts: list[str], kind: str = "query") -> Any:
    """Нормированные векторы фраз (n × dim) или None — модели нет. ``kind`` — query (реплика, запрос) | passage
    (описание мема)."""
    texts = [str(t or "").strip() or "." for t in texts]
    if not texts:
        return None
    if EMBED is not None:
        return _normalize(EMBED(texts, kind))
    enc = _encoder()
    if enc is None:
        return None
    try:
        vectors = enc.encode(texts, kind)
    except Exception as exc:  # noqa: BLE001 - сбой кодировщика не валит монтаж
        _log.warning("text_embed: кодирование не вышло: %s", str(exc)[:200])
        return None
    finally:
        _schedule_unload()
    return _normalize(vectors)


def similarity(query: Any, matrix: Any) -> Any:
    """Косинусы вектора ``query`` (dim) со строками ``matrix`` (n × dim); оба нормированы."""
    import numpy as np

    return np.asarray(matrix, dtype=np.float32) @ np.asarray(query, dtype=np.float32).reshape(-1)


def tag() -> str:
    """Метка модели для ключей кэша векторов (смена модели — векторы заново)."""
    return "test" if EMBED is not None else model_name()
