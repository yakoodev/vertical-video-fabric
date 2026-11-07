from __future__ import annotations

import json
import logging
import os
import re
import secrets
import string
import subprocess
import time
import uuid
import zlib
from pathlib import Path

import requests
from requests_auth_aws_sigv4 import AWSSigV4

from app.cookies import CookieRecord, tiktok_caption
from app.providers.base import Provider, ProviderResult, safe_provider_error
from app.proxy_config import masked
from app.publish_format import publish_plan
from app.settings import settings

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# Таймауты запросов (соединение, чтение): без них зависшее соединение держало воркер вечно.
_TIMEOUT = (30, 120)
_CHUNK_TIMEOUT = (30, 600)
# Подпись: браузер открывает www.tiktok.com, поэтому идёт через прокси аккаунта (node/tiktok-sign.cjs).
_SIGN_SCRIPT = Path(__file__).resolve().parents[2] / "node" / "tiktok-sign.cjs"
_SIGN_PROXY_ENV = "TIKTOK_SIGN_PROXY"
_SIGN_TIMEOUT = 60
# Ключи id поста в ответе публикации (числовой id ролика — для ссылки и статистики).
_ITEM_ID_KEYS = ("item_id", "itemId", "aweme_id", "awemeId", "item_id_str")
_ITEM_ID_RE = re.compile(r"\d{10,25}")
_CHUNK_SIZE = 5_242_880
MAYBE_POSTED = "Связь оборвалась на самом посте, ролик мог выйти: проверьте на TikTok, прежде чем публиковать снова"
NO_POST_LINK = "Пост вышел, но TikTok не вернул его id: ссылки и статистики у него не будет"


class _StepFailed(Exception):
    """Шаг загрузки не удался: готовый итог для очереди."""

    def __init__(self, result: ProviderResult) -> None:
        super().__init__(result.error)
        self.result = result


def _user_agent() -> str:
    """UA веб-загрузки: ``TIKTOK_USER_AGENT`` (браузер, из которого взяты cookie) или прежний."""
    return settings.tiktok_user_agent or _UA


class TikTokProvider(Provider):
    platform = "tiktok"

    def upload(
        self,
        *,
        cookies: list[CookieRecord],
        file_path: Path,
        title: str,
        description: str,
        privacy: str,
        allow_comments: bool,
        account_label: str,
        proxy_url: str,
        video_format: str = "9:16",
        thumbnail_path: str = "",
    ) -> ProviderResult:
        # TikTok ждёт вертикаль: 16:9/1:1/4:5 публикуем как есть (площадка покажет с полосами),
        # предупреждение — в ответе (app/publish_format.py). thumbnail_path TikTok не берёт.
        del thumbnail_path
        try:
            self._ensure_signature_helper()
            result = self._upload_and_post(
                cookies=cookies,
                file_path=file_path,
                title=title,
                description=description,
                privacy=privacy,
                allow_comments=allow_comments,
                account_label=account_label,
                proxy_url=proxy_url,
            )
            warning = publish_plan(self.platform, video_format)["warning"] if video_format != "9:16" else ""
            if warning:
                result.response = {**(result.response or {}), "format": video_format, "format_warning": warning}
            return result
        except _StepFailed as exc:
            return exc.result
        except requests.RequestException as exc:
            return ProviderResult(status="failed", error=_network_error(exc, proxy_url))
        except Exception as exc:  # noqa: BLE001 - provider must report failures to queue
            # Свой сбой (файл, подпись), а не ответ TikTok: в его тексте путь или адрес могут содержать «auth» и «session».
            return ProviderResult(status="failed", error=_safe_error(f"{type(exc).__name__}: {exc}"))

    def _ensure_signature_helper(self) -> None:
        for helper in (self._signature_dir() / "index.js", _SIGN_SCRIPT):
            if not helper.exists():
                raise RuntimeError(f"TikTok signature helper not found: {helper}")

    def _upload_and_post(
        self,
        *,
        cookies: list[CookieRecord],
        file_path: Path,
        title: str,
        description: str,
        privacy: str,
        allow_comments: bool,
        account_label: str,
        proxy_url: str,
    ) -> ProviderResult:
        session = requests.Session()
        session.verify = True
        # Иначе HTTP(S)_PROXY окружения перекрывает прокси аккаунта: в requests запрос сильнее сессии.
        session.trust_env = False
        user_agent = _user_agent()
        session.headers.update({"User-Agent": user_agent, "Accept": "application/json, text/plain, */*"})
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}
        for cookie in cookies:
            session.cookies.set(
                cookie.name,
                cookie.value,
                domain=cookie.domain or ".tiktok.com",
                path=cookie.path or "/",
            )

        if not _cookie_value(session, "sessionid"):
            return ProviderResult(status="needs_reauth", error="TikTok sessionid cookie is missing")

        creation_id = _generate_random_string(21, True)
        project_url = f"https://www.tiktok.com/api/v1/web/project/create/?creation_id={creation_id}&type=1&aid=1988"
        response = _retrying(session, "post", project_url)
        if not _assert_success(response):
            return _tiktok_response_failure(response, "TikTok project create failed")
        project_id = _answer_field(response, "TikTok project create failed", "project", "project_id", session_gated=True)

        video_id, session_key, upload_id, crcs, upload_host, store_uri, video_auth, aws_auth = _upload_to_tiktok(
            file_path, session
        )

        finish_url = f"https://{upload_host}/{store_uri}?uploadID={upload_id}&phase=finish&uploadmode=part"
        finish_headers = {"Authorization": video_auth, "Content-Type": "text/plain;charset=UTF-8"}
        finish_body = ",".join([f"{i + 1}:{crcs[i]}" for i in range(len(crcs))])
        response = _retrying(session, "post", finish_url, headers=finish_headers, data=finish_body)
        if not _assert_success(response):
            return _tiktok_response_failure(response, "TikTok upload finish failed", status="failed")

        commit_url = "https://www.tiktok.com/top/v1?Action=CommitUploadInner&Version=2020-11-19&SpaceName=tiktok"
        commit_body = '{"SessionKey":"' + session_key + '","Functions":[{"name":"GetMeta"}]}'
        response = _retrying(session, "post", commit_url, auth=aws_auth, data=commit_body)
        if not _assert_success(response):
            return _tiktok_response_failure(response, "TikTok upload commit failed")

        _safe_head(session, user_agent)
        caption = tiktok_caption(title, description)
        markup_text, text_extra = _convert_hashtags(caption)
        visibility_type = 1 if privacy == "private" else 0
        data = {
            "post_common_info": {
                "creation_id": creation_id,
                "enter_post_page_from": 1,
                "post_type": 3,
            },
            "feature_common_info_list": [
                {
                    "geofencing_regions": [],
                    "playlist_name": "",
                    "playlist_id": "",
                    "tcm_params": "{\"commerce_toggle_info\":{}}",
                    "sound_exemption": 0,
                    "anchors": [],
                    "vedit_common_info": {"draft": "", "video_id": video_id},
                    "privacy_setting_info": {
                        "visibility_type": visibility_type,
                        "allow_duet": 1,
                        "allow_stitch": 1,
                        "allow_comment": 1 if allow_comments else 0,
                    },
                }
            ],
            "single_post_req_list": [
                {
                    "batch_index": 0,
                    "video_id": video_id,
                    "is_long_video": 0,
                    "single_post_feature_info": {
                        "text": caption,
                        "text_extra": text_extra,
                        "markup_text": markup_text,
                        "music_info": {},
                        "poster_delay": 0,
                    },
                }
            ],
            "project_id": project_id,
        }

        ms_token = _cookie_value(session, "msToken") or ""
        sig_url = (
            "https://www.tiktok.com/api/v1/web/project/post/"
            f"?app_name=tiktok_web&channel=tiktok_web&device_platform=web&aid=1988&msToken={ms_token}"
        )
        signatures = _run_signature_helper(self._signature_dir(), user_agent, sig_url, proxy_url)
        if not signatures:
            return ProviderResult(status="failed", error="TikTok signature generation failed")
        signature_data = _signature_data(signatures)
        params = {
            "app_name": "tiktok_web",
            "channel": "tiktok_web",
            "device_platform": "web",
            "aid": 1988,
            "msToken": ms_token,
            "X-Bogus": signature_data["x-bogus"],
            "_signature": signature_data["signature"],
        }
        post_url = "https://www.tiktok.com/tiktok/web/project/post/v1/"
        try:
            response = session.post(
                post_url,
                params=params,
                data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json", "user-agent": user_agent},
                timeout=_TIMEOUT,
            )
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout):
            raise  # до TikTok запрос не дошёл, поста точно нет
        except requests.RequestException as exc:
            return _maybe_posted(str(exc))
        if response.status_code in (401, 403):
            return _tiktok_response_failure(response, "TikTok auth failed", status="needs_reauth")
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 500 or not isinstance(body, dict):
            return _maybe_posted(f"HTTP {response.status_code} {str(getattr(response, 'text', ''))[:300]}",
                                 response={"status_code": response.status_code})
        if body.get("status_code") != 0:
            error = body.get("status_msg") or body.get("message") or "TikTok publish failed"
            status = _classify_tiktok_error(error)
            return ProviderResult(status=status, error=_safe_error(error), response=body)
        # Числовой id поста — ссылка https://www.tiktok.com/@/video/<id> (её понимают TikTok и
        # yt-dlp): без ссылки статистика TikTok-публикаций не собиралась (app/platform_stats.py).
        item_id = _find_item_id(body)
        log.info("tiktok post ok: account=%s item_id=%s video_id=%s creation_id=%s project_id=%s",
                 account_label, item_id or "?", video_id, creation_id, project_id)
        return ProviderResult(
            status="succeeded",
            remote_id=item_id or str(video_id),
            remote_url=f"https://www.tiktok.com/@/video/{item_id}" if item_id else "",
            error="" if item_id else NO_POST_LINK,
            response={"tiktok": body, "video_id": video_id, "item_id": item_id, "creation_id": creation_id,
                      "project_id": project_id},
        )

    def _signature_dir(self) -> Path:
        return settings.tiktok_vendor_root / "tiktok_uploader" / "tiktok-signature"


def _upload_to_tiktok(file_path: Path, session: requests.Session):
    auth_url = "https://www.tiktok.com/api/v1/video/upload/auth/?aid=1988"
    response = _retrying(session, "get", auth_url)
    if not _assert_success(response):
        raise _StepFailed(_tiktok_response_failure(response, "TikTok upload auth failed"))
    token = _answer_field(response, "TikTok upload auth failed", "video_token_v5", session_gated=True)
    aws_auth = AWSSigV4(
        "vod",
        region="ap-singapore-1",
        aws_access_key_id=token["access_key_id"],
        aws_secret_access_key=token["secret_acess_key"],
        aws_session_token=token["session_token"],
    )
    file_size = file_path.stat().st_size
    apply_url = (
        "https://www.tiktok.com/top/v1?Action=ApplyUploadInner&Version=2020-11-19"
        f"&SpaceName=tiktok&FileType=video&IsInner=1&FileSize={file_size}&s=g158iqx8434"
    )
    response = _retrying(session, "get", apply_url, auth=aws_auth)
    if not _assert_success(response):
        raise _StepFailed(_tiktok_response_failure(response, "TikTok apply upload failed", status="failed"))
    upload_node = _answer_field(response, "TikTok apply upload failed", "Result", "InnerUploadAddress", "UploadNodes", 0)
    video_id = upload_node["Vid"]
    store_uri = upload_node["StoreInfos"][0]["StoreUri"]
    video_auth = upload_node["StoreInfos"][0]["Auth"]
    upload_host = upload_node["UploadHost"]
    session_key = upload_node["SessionKey"]
    crcs = []
    upload_id = str(uuid.uuid4())
    # С диска по куску: в памяти машины не лежит весь файл (длинная запись — гигабайты).
    with file_path.open("rb") as video:
        for idx, chunk in enumerate(iter(lambda: video.read(_CHUNK_SIZE), b""), start=1):
            crc = _crc32(chunk)
            crcs.append(crc)
            chunk_url = f"https://{upload_host}/{store_uri}?partNumber={idx}&uploadID={upload_id}&phase=transfer"
            headers = {
                "Authorization": video_auth,
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="undefined"',
                "Content-Crc32": crc,
            }
            response = _retrying(session, "post", chunk_url, headers=headers, data=chunk, timeout=_CHUNK_TIMEOUT)
            if not _assert_success(response):
                raise _StepFailed(_tiktok_response_failure(response, f"TikTok chunk upload failed at part {idx}",
                                                           status="failed"))
    return video_id, session_key, upload_id, crcs, upload_host, store_uri, video_auth, aws_auth


def _retrying(session: requests.Session, method: str, url: str, **kwargs):
    """Запрос шага, который безопасно повторить (до публикации поста): обрыв сети, 429 и 5xx
    повторяются ``PUBLISH_UPLOAD_RETRIES`` раз с растущей паузой; остальное — как есть.
    Сам пост (project/post) не повторяется никогда — повтор мог бы выложить ролик дважды."""
    kwargs.setdefault("timeout", _TIMEOUT)
    attempts = 1 + max(0, int(settings.publish_upload_retries))
    for attempt in range(1, attempts + 1):
        try:
            response = getattr(session, method)(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if attempt >= attempts:
                raise
        else:
            transient = response.status_code >= 500 or response.status_code == 429
            if not transient or attempt >= attempts:
                return response
        log.info("tiktok %s %s: retry %d/%d", method.upper(), url.split("?", 1)[0], attempt, attempts - 1)
        time.sleep(max(0.0, float(settings.publish_upload_retry_seconds)) * attempt)
    raise RuntimeError("unreachable")  # pragma: no cover


def _safe_head(session: requests.Session, user_agent: str) -> None:
    """Прогрев перед постом, как у веб-клиента; его сбой — не повод срывать публикацию."""
    try:
        session.head("https://www.tiktok.com", headers={"user-agent": user_agent}, timeout=_TIMEOUT)
    except requests.RequestException:
        pass


def _find_item_id(value, depth: int = 0) -> str:
    """Числовой id опубликованного ролика в ответе project/post (форма ответа не документирована)."""
    if depth > 8:
        return ""
    if isinstance(value, dict):
        for key in _ITEM_ID_KEYS:
            child = value.get(key)
            if isinstance(child, (str, int)) and not isinstance(child, bool) and _ITEM_ID_RE.fullmatch(str(child)):
                return str(child)
        children = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return ""
    for child in children:
        found = _find_item_id(child, depth + 1)
        if found:
            return found
    return ""


def _run_signature_helper(signature_dir: Path, user_agent: str, url: str, proxy_url: str = "") -> str:
    env = {key: value for key, value in os.environ.items() if key != _SIGN_PROXY_ENV}
    if proxy_url:
        env[_SIGN_PROXY_ENV] = proxy_url
    try:
        proc = subprocess.run(
            [settings.node_bin, str(_SIGN_SCRIPT), str(signature_dir), url, user_agent],
            text=True,
            capture_output=True,
            timeout=_SIGN_TIMEOUT,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        # from None: в тексте TimeoutExpired вся команда, а в ней адрес с msToken.
        raise RuntimeError(f"TikTok signature helper timed out after {_SIGN_TIMEOUT} s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"TikTok signature helper failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _signature_data(output: str) -> dict:
    try:
        data = json.loads(output)["data"]
        return {"x-bogus": data["x-bogus"], "signature": data["signature"]}
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("TikTok signature helper returned an unexpected answer") from None


def _generate_random_string(length: int, underline: bool) -> str:
    characters = string.ascii_letters + string.digits + ("_" if underline else "")
    return "".join(secrets.choice(characters) for _ in range(length))


def _crc32(content: bytes) -> str:
    return ("%X" % (zlib.crc32(content, 0) & 0xFFFFFFFF)).lower().zfill(8)


def _assert_success(response) -> bool:
    return response.status_code == 200


def _cookie_value(session: requests.Session, name: str) -> str:
    for cookie in session.cookies:
        if cookie.name == name and "tiktok.com" in (cookie.domain or ""):
            return cookie.value
    for cookie in session.cookies:
        if cookie.name == name:
            return cookie.value
    return ""


def _convert_hashtags(text: str) -> tuple[str, list[dict]]:
    text_extra = []
    idx = 0
    while idx < len(text):
        if text[idx] == "#":
            start = idx
            idx += 1
            name_start = idx
            while idx < len(text) and (text[idx].isalnum() or text[idx] == "_"):
                idx += 1
            if idx > name_start:
                text_extra.append(
                    {
                        "start": start,
                        "end": idx,
                        "type": 1,
                        "hashtag_name": text[name_start:idx],
                        "user_id": "",
                        "tag_id": str(len(text_extra)),
                    }
                )
                continue
        idx += 1
    return text, text_extra


def _answer_field(response, failure: str, *path, session_gated: bool = False):
    """Поле ответа 200. Нет его — итог с текстом TikTok, а не «KeyError: 'project'». ``session_gated`` — шаг, который
    TikTok отдаёт только живой сессии: без поля вероятнее всего протухли cookie."""
    try:
        value = response.json()
        for key in path:
            value = value[key]
        return value
    except (ValueError, KeyError, IndexError, TypeError):
        status = "needs_reauth" if session_gated else None
        raise _StepFailed(_tiktok_response_failure(response, f"{failure}: no {path[-1]} in the answer", status)) from None


def _tiktok_response_failure(response, fallback: str, status: str | None = None) -> ProviderResult:
    """Неудачный ответ TikTok. Куки виноваты при 401/403 или по тексту самого TikTok; свой ``fallback`` с «auth» в
    названии шага и страница ошибки прокси на это не влияют."""
    code = response.status_code
    body: dict = {}
    said = ""
    try:
        parsed = response.json()
    except ValueError:
        detail = str(getattr(response, "text", "") or "")[:300]
    else:
        body = parsed if isinstance(parsed, dict) else {}
        said = str(body.get("status_msg") or body.get("message") or "")
        detail = said or (json.dumps(parsed, ensure_ascii=False)[:300] if parsed else "")
    if status is None:
        status = "needs_reauth" if code in (401, 403) or _classify_tiktok_error(said) == "needs_reauth" else "failed"
    error = f"{fallback} (HTTP {code})" + (f": {detail}" if detail else "")
    return ProviderResult(status=status, error=_safe_error(error), response={"status_code": code, "body": body})


def _maybe_posted(detail: str, response: dict | None = None) -> ProviderResult:
    return ProviderResult(status="failed", maybe_posted=True, error=_safe_error(f"{MAYBE_POSTED}. {detail}".strip()),
                          response=response or {})


def _network_error(exc: Exception, proxy_url: str) -> str:
    """Сеть подвела до поста: ролика на площадке нет. Прокси назван (без логина и пароля), напрямую не пробуем."""
    head = (f"Нет связи через прокси {masked(proxy_url)}: ролик не отправлен, напрямую не пробовали"
            if proxy_url else "Нет связи с TikTok: ролик не отправлен")
    return _safe_error(f"{head}. {type(exc).__name__}: {str(exc)[:600]}")


def _classify_tiktok_error(message: str) -> str:
    """По тексту самого TikTok: про вход или проверку — нужны свежие cookie."""
    lowered = message.lower()
    auth_markers = ("login", "session", "cookie", "verify", "captcha", "401", "403", "auth")
    if any(marker in lowered for marker in auth_markers):
        return "needs_reauth"
    return "failed"


def _safe_error(message: str) -> str:
    return safe_provider_error(message, "tiktok upload failed")
