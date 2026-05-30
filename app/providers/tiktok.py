from __future__ import annotations

import json
import pickle
import secrets
import string
import subprocess
import uuid
import zlib
from pathlib import Path

import requests
from requests_auth_aws_sigv4 import AWSSigV4

from app.cookies import CookieRecord, tiktok_caption
from app.providers.base import Provider, ProviderResult
from app.settings import settings

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


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
    ) -> ProviderResult:
        try:
            self._ensure_signature_helper()
            result = self._upload_with_vendor_requests(
                cookies=cookies,
                file_path=file_path,
                title=title,
                description=description,
                privacy=privacy,
                allow_comments=allow_comments,
                account_label=account_label,
                proxy_url=proxy_url,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - provider must report failures to queue
            message = str(exc)
            return ProviderResult(status=_classify_tiktok_error(message), error=_safe_error(message))

    def _ensure_signature_helper(self) -> None:
        helper = self._signature_helper_path()
        if not helper.exists():
            raise RuntimeError(f"TikTok signature helper not found: {helper}")

    def _upload_with_vendor_requests(
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
        self._write_vendor_cookie_file(account_label, cookies)

        session = requests.Session()
        session.verify = True
        session.headers.update({"User-Agent": _UA, "Accept": "application/json, text/plain, */*"})
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
        response = session.post(project_url)
        if not _assert_success(response):
            return _tiktok_response_failure(response, "TikTok project create failed")
        project_id = response.json()["project"]["project_id"]

        video_id, session_key, upload_id, crcs, upload_host, store_uri, video_auth, aws_auth = _upload_to_tiktok(
            file_path, session
        )

        finish_url = f"https://{upload_host}/{store_uri}?uploadID={upload_id}&phase=finish&uploadmode=part"
        finish_headers = {"Authorization": video_auth, "Content-Type": "text/plain;charset=UTF-8"}
        finish_body = ",".join([f"{i + 1}:{crcs[i]}" for i in range(len(crcs))])
        response = session.post(finish_url, headers=finish_headers, data=finish_body)
        if not _assert_success(response):
            return _tiktok_response_failure(response, "TikTok upload finish failed")

        commit_url = "https://www.tiktok.com/top/v1?Action=CommitUploadInner&Version=2020-11-19&SpaceName=tiktok"
        commit_body = '{"SessionKey":"' + session_key + '","Functions":[{"name":"GetMeta"}]}'
        response = session.post(commit_url, auth=aws_auth, data=commit_body)
        if not _assert_success(response):
            return _tiktok_response_failure(response, "TikTok upload commit failed")

        session.head("https://www.tiktok.com", headers={"user-agent": _UA})
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
        signatures = _run_signature_helper(self._signature_helper_path(), _UA, sig_url)
        if not signatures:
            return ProviderResult(status="failed", error="TikTok signature generation failed")
        signature_data = json.loads(signatures)["data"]
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
        response = session.post(
            post_url,
            params=params,
            data=json.dumps(data, ensure_ascii=False),
            headers={"content-type": "application/json", "user-agent": _UA},
        )
        if response.status_code in (401, 403):
            return _tiktok_response_failure(response, "TikTok auth failed", status="needs_reauth")
        try:
            body = response.json()
        except ValueError:
            return _tiktok_response_failure(response, "TikTok publish returned non-JSON response")
        if body.get("status_code") != 0:
            error = body.get("status_msg") or body.get("message") or "TikTok publish failed"
            status = _classify_tiktok_error(error)
            return ProviderResult(status=status, error=_safe_error(error), response=body)
        return ProviderResult(
            status="succeeded",
            remote_id=str(video_id),
            response={"tiktok": body, "video_id": video_id, "creation_id": creation_id},
        )

    def _write_vendor_cookie_file(self, account_label: str, cookies: list[CookieRecord]) -> None:
        runtime_dir = settings.runtime_dir / "tiktok-cookies"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        safe_label = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in account_label)[:80]
        path = runtime_dir / f"tiktok_session-{safe_label or 'account'}.cookie"
        data = [cookie.to_vendor_dict(".tiktok.com") for cookie in cookies]
        with path.open("wb") as f:
            pickle.dump(data, f)

    def _signature_helper_path(self) -> Path:
        return settings.tiktok_vendor_root / "tiktok_uploader" / "tiktok-signature" / "browser.js"


def _upload_to_tiktok(file_path: Path, session: requests.Session):
    auth_url = "https://www.tiktok.com/api/v1/video/upload/auth/?aid=1988"
    response = session.get(auth_url)
    if not _assert_success(response):
        raise RuntimeError("TikTok upload auth failed")
    token = response.json()["video_token_v5"]
    aws_auth = AWSSigV4(
        "vod",
        region="ap-singapore-1",
        aws_access_key_id=token["access_key_id"],
        aws_secret_access_key=token["secret_acess_key"],
        aws_session_token=token["session_token"],
    )
    video_content = file_path.read_bytes()
    file_size = len(video_content)
    apply_url = (
        "https://www.tiktok.com/top/v1?Action=ApplyUploadInner&Version=2020-11-19"
        f"&SpaceName=tiktok&FileType=video&IsInner=1&FileSize={file_size}&s=g158iqx8434"
    )
    response = session.get(apply_url, auth=aws_auth)
    if not _assert_success(response):
        raise RuntimeError("TikTok apply upload failed")
    upload_node = response.json()["Result"]["InnerUploadAddress"]["UploadNodes"][0]
    video_id = upload_node["Vid"]
    store_uri = upload_node["StoreInfos"][0]["StoreUri"]
    video_auth = upload_node["StoreInfos"][0]["Auth"]
    upload_host = upload_node["UploadHost"]
    session_key = upload_node["SessionKey"]
    chunk_size = 5_242_880
    chunks = [video_content[i : i + chunk_size] for i in range(0, file_size, chunk_size)]
    crcs = []
    upload_id = str(uuid.uuid4())
    for idx, chunk in enumerate(chunks, start=1):
        crc = _crc32(chunk)
        crcs.append(crc)
        chunk_url = f"https://{upload_host}/{store_uri}?partNumber={idx}&uploadID={upload_id}&phase=transfer"
        headers = {
            "Authorization": video_auth,
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="undefined"',
            "Content-Crc32": crc,
        }
        response = session.post(chunk_url, headers=headers, data=chunk)
        if not _assert_success(response):
            raise RuntimeError(f"TikTok chunk upload failed at part {idx}")
    return video_id, session_key, upload_id, crcs, upload_host, store_uri, video_auth, aws_auth


def _run_signature_helper(js_path: Path, user_agent: str, url: str) -> str:
    proc = subprocess.run(
        [settings.node_bin, str(js_path), url, user_agent],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"TikTok signature helper failed: {proc.stderr.strip()}")
    stdout = proc.stdout.strip()
    if not stdout and proc.stderr.strip():
        raise RuntimeError(f"TikTok signature helper failed: {proc.stderr.strip()}")
    return stdout


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


def _tiktok_response_failure(response, fallback: str, status: str | None = None) -> ProviderResult:
    message = fallback
    body = {}
    try:
        body = response.json()
        message = body.get("status_msg") or body.get("message") or fallback
    except ValueError:
        message = response.text[:1000] if getattr(response, "text", "") else fallback
    return ProviderResult(
        status=status or _classify_tiktok_error(f"{response.status_code} {message}"),
        error=_safe_error(message),
        response={"status_code": response.status_code, "body": body},
    )


def _classify_tiktok_error(message: str) -> str:
    lowered = message.lower()
    auth_markers = ("login", "session", "cookie", "verify", "captcha", "401", "403", "auth")
    if any(marker in lowered for marker in auth_markers):
        return "needs_reauth"
    return "failed"


def _safe_error(message: str) -> str:
    text = (message or "tiktok upload failed").strip()
    if len(text) > 2000:
        text = text[:2000] + "..."
    return text
