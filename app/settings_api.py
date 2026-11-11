"""Настройки сервиса: прокси, значения по умолчанию, промпты, заголовок и описание после рендера, выгрузка и загрузка
конфигурации.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
import json
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app import download_account, proxy_config
from app.agent_settings_api import owner_only
from app.ai.schema import ANALYSIS_RESPONSE_SCHEMA
from app.api_common import _safe_next, _setting_int
from app.default_prompts import BASE_ANALYSIS_PROMPT, DEFAULT_PUBLISHING_PROMPT, DEFAULT_SUBTITLE_PROMPT
from app.settings import settings
from app.subtitles.gemini import gemini_subtitle_schema


PUBLISHING_METADATA_SCHEMA = {
    "type": "object",
    "required": ["title", "description"],
    "properties": {
        "title": {"type": "string", "maxLength": 100},
        "description": {"type": "string", "maxLength": 500},
    },
}


def _settings_panel_data(store: Any) -> dict:
    saved = store.list_app_settings(include_secrets=False)
    store.ensure_prompt_presets(
        {
            "analysis": ("Apex analysis", saved.get("analysis_prompt") or BASE_ANALYSIS_PROMPT),
            "publishing": ("Publishing metadata", saved.get("publishing_prompt") or DEFAULT_PUBLISHING_PROMPT),
            "subtitle": ("Apex subtitles", saved.get("subtitle_prompt") or DEFAULT_SUBTITLE_PROMPT),
        }
    )
    analysis_prompt_preset = store.get_default_prompt_preset("analysis")
    publishing_prompt_preset = store.get_default_prompt_preset("publishing")
    subtitle_prompt_preset = store.get_default_prompt_preset("subtitle")
    default_banner_id = _setting_int(saved.get("default_banner_id"))
    default_subtitle_profile_id = _setting_int(saved.get("default_subtitle_profile_id"))
    return {
        "accounts": store.list_accounts(),
        "presets": store.list_ffmpeg_presets(),
        "banners": store.list_banners(),
        "audio_tracks": store.list_audio_tracks(),
        "subtitle_profiles": store.list_subtitle_profiles(),
        "prompt_presets": store.list_prompt_presets(),
        "analysis_prompt_presets": store.list_prompt_presets("analysis"),
        "publishing_prompt_presets": store.list_prompt_presets("publishing"),
        "subtitle_prompt_presets": store.list_prompt_presets("subtitle"),
        "analysis_prompt": (analysis_prompt_preset or {}).get("prompt") or saved.get("analysis_prompt") or BASE_ANALYSIS_PROMPT,
        "publishing_prompt": (publishing_prompt_preset or {}).get("prompt") or saved.get("publishing_prompt") or DEFAULT_PUBLISHING_PROMPT,
        "subtitle_prompt": (subtitle_prompt_preset or {}).get("prompt") or saved.get("subtitle_prompt") or DEFAULT_SUBTITLE_PROMPT,
        "default_analysis_prompt_preset_id": (analysis_prompt_preset or {}).get("id") or 0,
        "default_ai_provider": saved.get("default_ai_provider") or settings.ai_video_provider,
        "default_ai_model": saved.get("default_ai_model") or "",
        "default_subtitle_provider": saved.get("default_subtitle_provider") or settings.subtitle_provider,
        "default_subtitle_model": saved.get("default_subtitle_model") or "",
        "default_banner_id": default_banner_id,
        "default_subtitle_profile_id": default_subtitle_profile_id,
        "global_proxy_configured": bool(store.get_global_proxy_url()),
        "global_proxy_display": "configured" if store.get_global_proxy_url() else "direct",
        "analysis_schema_json": json.dumps(ANALYSIS_RESPONSE_SCHEMA, ensure_ascii=False, indent=2),
        "publishing_schema_json": json.dumps(PUBLISHING_METADATA_SCHEMA, ensure_ascii=False, indent=2),
        "subtitle_schema_json": json.dumps(gemini_subtitle_schema(), ensure_ascii=False, indent=2),
    }


class ProxySettingsRequest(BaseModel):
    url: str = Field(default="", max_length=500, description="http://логин:пароль@хост:порт, socks5://… или логин:пароль:хост:порт")
    expires_on: str = Field(default="", max_length=10, description="YYYY-MM-DD — когда прокси протухнет")
    for_gemini: bool = True


class DownloadAccountRequest(BaseModel):
    account_id: int | None = Field(default=None, description="id аккаунта YouTube для скачивания; пусто — не брать никакой")


class ProxyTestRequest(BaseModel):
    url: str = Field(default="", max_length=500)


class AutoMetaPayload(BaseModel):
    enabled: bool


def build_settings_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings/proxy", tags=["Settings"], summary="Прокси сервиса (без секретов)")
    def api_get_proxy(_auth: auth_dep) -> dict:
        return proxy_config.status()

    @router.put("/api/settings/proxy", tags=["Settings"], summary="Задать прокси (загрузки, при желании — Gemini)")
    def api_put_proxy(payload: ProxySettingsRequest, _auth: auth_dep) -> dict:
        try:
            url = payload.url.strip() or proxy_config.load().get("url", "")
            if not url:
                raise ValueError("прокси: укажите адрес")
            return proxy_config.save(url, payload.expires_on or None, payload.for_gemini)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/settings/proxy", tags=["Settings"], summary="Убрать прокси сервиса")
    def api_delete_proxy(_auth: auth_dep) -> dict:
        return proxy_config.clear()

    @router.post("/api/settings/proxy/test", tags=["Settings"], summary="Проверить прокси: видео-CDN Twitch и Google")
    def api_test_proxy(_auth: auth_dep, payload: ProxyTestRequest | None = None) -> dict:
        try:
            return proxy_config.test((payload.url if payload else "") or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/settings/download-account", tags=["Settings"],
                summary="Аккаунт YouTube для скачивания (когда YouTube просит вход)")
    def api_get_download_account(_auth: auth_dep) -> dict:
        return download_account.status(get_store())

    @router.put("/api/settings/download-account", tags=["Settings"],
                summary="Выбрать аккаунт YouTube для скачивания (null — никакой)")
    def api_put_download_account(payload: DownloadAccountRequest, _auth: auth_dep) -> dict:
        try:
            return download_account.set_account(get_store(), payload.account_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/settings", tags=["Render"], summary="Default models, proxy and asset settings")
    def api_settings(_auth: auth_dep) -> dict:
        data = _settings_panel_data(get_store())
        return {
            "default_ai_provider": data["default_ai_provider"],
            "default_ai_model": data["default_ai_model"],
            "default_subtitle_provider": data["default_subtitle_provider"],
            "default_subtitle_model": data["default_subtitle_model"],
            "default_banner_id": data["default_banner_id"],
            "default_subtitle_profile_id": data["default_subtitle_profile_id"],
            "global_proxy_configured": data["global_proxy_configured"],
            "global_proxy_display": data["global_proxy_display"],
            "banners": [{"id": b["id"], "label": b["label"]} for b in data["banners"]],
            "subtitle_profiles": [{"id": p["id"], "label": p["label"]} for p in data["subtitle_profiles"]],
            **_auto_meta_settings(),
        }

    def _auto_meta_settings() -> dict:
        from app.ai.publish_meta import AUTO_META_KEY

        return {
            "auto_publish_meta": bool(get_store().get_app_setting_value(AUTO_META_KEY, True)),
            "gemini_configured": bool(settings.gemini_api_key),
            "auto_publish_meta_env": bool(settings.auto_publish_meta),
        }

    @router.put(
        "/api/settings/auto-publish-meta",
        tags=["Settings"],
        summary="Write title/description/hashtags automatically after each render (needs a Gemini key)",
    )
    def api_set_auto_publish_meta(payload: AutoMetaPayload, _auth: auth_dep) -> dict:
        from app.ai.publish_meta import AUTO_META_KEY

        get_store().set_app_setting(AUTO_META_KEY, bool(payload.enabled))
        return _auto_meta_settings()

    @router.post("/ui/settings/defaults", include_in_schema=False)
    def ui_save_settings_defaults(
        _auth: auth_dep,
        analysis_prompt: Annotated[str, Form()] = BASE_ANALYSIS_PROMPT,
        publishing_prompt: Annotated[str, Form()] = DEFAULT_PUBLISHING_PROMPT,
        subtitle_prompt: Annotated[str, Form()] = DEFAULT_SUBTITLE_PROMPT,
        default_ai_provider: Annotated[str, Form()] = "mock",
        default_ai_model: Annotated[str, Form()] = "",
        default_subtitle_provider: Annotated[str, Form()] = "mock",
        default_subtitle_model: Annotated[str, Form()] = "",
        global_proxy_url: Annotated[str | None, Form()] = None,
        default_banner_id: Annotated[int, Form()] = 0,
        default_subtitle_profile_id: Annotated[int, Form()] = 0,
        next: Annotated[str, Form()] = "/sources",
    ) -> RedirectResponse:
        try:
            provider = default_ai_provider.strip().lower() or "mock"
            subtitle_provider = default_subtitle_provider.strip().lower() or "mock"
            if provider not in {"mock", "action", "polza", "gemini", "artemox"}:
                raise ValueError(f"unsupported provider: {default_ai_provider}")
            if subtitle_provider not in {"mock", "whisper", "polza", "gemini", "artemox"}:
                raise ValueError(f"unsupported subtitle provider: {default_subtitle_provider}")
            if global_proxy_url is not None and global_proxy_url.strip() and not global_proxy_url.strip().startswith(("http://", "https://")):
                raise ValueError("global proxy must start with http:// or https://")
            if default_banner_id:
                get_store().get_banner(default_banner_id)
            if default_subtitle_profile_id:
                get_store().get_subtitle_profile(default_subtitle_profile_id)
            get_store().set_app_setting("default_ai_provider", provider)
            get_store().set_app_setting("default_ai_model", default_ai_model.strip())
            get_store().set_app_setting("default_subtitle_provider", subtitle_provider)
            get_store().set_app_setting("default_subtitle_model", default_subtitle_model.strip())
            get_store().set_app_setting("default_banner_id", int(default_banner_id or 0))
            get_store().set_app_setting("default_subtitle_profile_id", int(default_subtitle_profile_id or 0))
            if global_proxy_url is not None:
                get_store().set_app_setting("global_proxy_url", global_proxy_url.strip(), encrypted=True)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=_safe_next(next), status_code=303)

    @router.get("/api/prompt-presets", tags=["Render"], summary="List prompt presets")
    def api_prompt_presets(_auth: auth_dep, task: str | None = None) -> list[dict]:
        return get_store().list_prompt_presets(task)

    @router.post("/ui/prompt-presets", include_in_schema=False)
    def ui_create_prompt_preset(
        _auth: auth_dep,
        task: Annotated[str, Form()],
        label: Annotated[str, Form()],
        prompt: Annotated[str, Form()],
        make_default: Annotated[bool, Form()] = False,
        next: Annotated[str, Form()] = "/sources",
    ) -> RedirectResponse:
        try:
            get_store().create_prompt_preset(task, label, prompt, is_default=make_default)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=_safe_next(next), status_code=303)

    @router.post("/ui/prompt-presets/{preset_id}/delete", include_in_schema=False)
    def ui_delete_prompt_preset(
        preset_id: int,
        _auth: auth_dep,
        next: Annotated[str, Form()] = "/sources",
    ) -> RedirectResponse:
        try:
            get_store().delete_prompt_preset(preset_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=_safe_next(next), status_code=303)

    @router.get("/api/export", tags=["Settings"], summary="Export a shareable config bundle")
    def api_export_bundle(request: Request, _auth: auth_dep, accounts: bool = False) -> dict:
        if accounts:  # в пакете живые cookie и пароли прокси
            owner_only(request, "Аккаунты с cookie выгружает только владелец")
        return get_store().export_bundle(include_accounts=accounts)

    @router.post("/api/import", tags=["Settings"], summary="Import a config bundle")
    def api_import_bundle(payload: dict, request: Request, _auth: auth_dep) -> dict:
        if payload.get("accounts"):
            owner_only(request, "Аккаунты загружает только владелец")
        try:
            return get_store().import_bundle(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
