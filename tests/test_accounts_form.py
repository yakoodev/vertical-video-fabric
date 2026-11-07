"""Форма аккаунта: JSON Cookie-Editor, прокси как его дают продавцы, обновление cookie без потери прокси, секреты не
возвращаются, менять аккаунты может только владелец."""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from app.auth import is_agent_request
from app.cookies import parse_cookie_input, required_cookie_status
from app.settings import settings
from test_api_pipeline import _client

COOKIE_EDITOR = json.dumps([
    {"domain": ".tiktok.com", "expirationDate": 1793000000.5, "hostOnly": False, "httpOnly": True, "name": "sessionid",
     "path": "/", "sameSite": "no_restriction", "secure": True, "session": False, "storeId": "0", "value": "SESSIONVALUE"},
    {"domain": "www.tiktok.com", "hostOnly": True, "httpOnly": False, "name": "msToken", "path": "/", "secure": True,
     "session": True, "value": "abc=="},
    {"domain": ".tiktok.com", "name": "tt-target-idc", "value": "useast2a"},
], indent=2)


def test_cookie_editor_json_export_is_accepted():
    cookies = parse_cookie_input(COOKIE_EDITOR, "tiktok")
    by_name = {c.name: c for c in cookies}
    assert by_name["sessionid"].value == "SESSIONVALUE" and by_name["sessionid"].http_only
    assert by_name["sessionid"].expires == 1793000000
    assert by_name["msToken"].domain == "www.tiktok.com" and by_name["msToken"].value == "abc=="
    assert by_name["msToken"].expires is None
    assert required_cookie_status("tiktok", cookies) == (True, [])


def test_storage_state_and_plain_object_are_accepted():
    state = json.dumps({"cookies": [{"name": "sessionid", "value": "s", "domain": ".tiktok.com", "expires": -1}]})
    assert parse_cookie_input(state, "tiktok")[0].expires is None
    plain = parse_cookie_input('{"sessionid": "s", "sid_tt": "t"}', "tiktok")
    assert {c.name for c in plain} == {"sessionid", "sid_tt"} and plain[0].domain == ".tiktok.com"


def test_broken_json_is_refused_without_echoing_values():
    with pytest.raises(ValueError) as err:
        parse_cookie_input('[{"name": "sessionid", "value": "SECRET"', "tiktok")
    assert "SECRET" not in str(err.value)


@pytest.fixture
def api(tmp_path, monkeypatch):
    return _client(tmp_path, monkeypatch)


def _add(client, **body):
    return client.post("/api/accounts", json={"platform": "tiktok", "label": "geo", "cookie": "sessionid=SESSIONVALUE",
                                              **body})


@pytest.mark.parametrize("pasted, stored", [
    ("http://user:pass@1.2.3.4:8080", "http://user:pass@1.2.3.4:8080"),
    ("user:pass:1.2.3.4:8080", "http://user:pass@1.2.3.4:8080"),
    ("  https://user:p%40ss@geo.example:443  ", "https://user:p%40ss@geo.example:443"),
])
def test_pasted_proxy_is_stored_ready_to_use(api, pasted, stored):
    client, store = api
    account = _add(client, proxy_url=pasted).json()
    assert store.get_account_proxy_url(account["id"]) == stored
    assert account["proxy_display"].endswith(stored.rsplit("@", 1)[1]) and "pass" not in account["proxy_display"]


@pytest.mark.parametrize("bad", ["http://user:pass@1.2.3.4", "http://user:pa/ss@1.2.3.4:80", "socks5://u:p@h:1080",
                                 "ftp://h:21"])
def test_bad_proxy_is_refused_at_once_and_the_list_still_works(api, bad):
    client, _store = api
    response = _add(client, proxy_url=bad)
    assert response.status_code == 400 and "proxy_url" in response.json()["detail"]
    assert "pass" not in response.json()["detail"]
    assert client.get("/api/accounts").status_code == 200


def test_saved_invalid_proxy_does_not_break_the_account_list(api):
    client, store = api
    account = _add(client).json()
    store.db.execute("UPDATE accounts SET encrypted_proxy_url = ? WHERE id = ?",
                     (store.cipher.encrypt_json("http://u:p@h:notaport"), account["id"]))
    assert client.get("/api/accounts").status_code == 200


def test_fresh_cookies_keep_the_proxy_unless_asked_to_clear(api):
    client, store = api
    account = _add(client, proxy_url="http://user:pass@1.2.3.4:8080").json()
    _add(client)  # те же площадка и метка, новые cookie, поле прокси пустое
    assert store.get_account_proxy_url(account["id"]) == "http://user:pass@1.2.3.4:8080"
    _add(client, proxy_url="http://user:pass@5.6.7.8:3128")
    assert store.get_account_proxy_url(account["id"]) == "http://user:pass@5.6.7.8:3128"
    _add(client, clear_proxy=True)
    assert store.get_account_proxy_url(account["id"]) == ""


def test_proxy_display_tells_about_the_service_wide_proxy(api):
    client, store = api
    store.set_app_setting("global_proxy_url", "http://g:g@9.9.9.9:1", encrypted=True)
    assert _add(client).json()["proxy_display"] == "global proxy"


def test_secrets_never_come_back(api):
    client, _store = api
    created = _add(client, cookie=COOKIE_EDITOR, proxy_url="http://user:SECRETPASS@1.2.3.4:8080")
    listed = client.get("/api/accounts")
    for response in (created, listed):
        text = response.text
        assert "SESSIONVALUE" not in text and "SECRETPASS" not in text and "abc==" not in text


def test_only_the_owner_changes_accounts_and_exports_cookies(api):
    client, _store = api
    agent = {"Authorization": "Bearer agent-token"}
    account = _add(client).json()
    assert client.post("/api/accounts", headers=agent,
                       json={"platform": "tiktok", "label": "x", "cookie": "sessionid=a"}).status_code == 403
    assert client.delete(f"/api/accounts/{account['id']}", headers=agent).status_code == 403
    assert client.get("/api/export?accounts=true", headers=agent).status_code == 403
    assert client.get("/api/export", headers=agent).status_code == 200  # без аккаунтов можно
    assert client.post("/api/import", headers=agent, json={"accounts": [{"platform": "tiktok", "label": "y"}]}
                       ).status_code == 403
    assert client.put("/api/settings/telegram", headers=agent, json={"allowed_user_ids": ["1"]}).status_code == 403
    assert client.delete(f"/api/accounts/{account['id']}").status_code == 200


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw, "query_string": b""})


def test_a_made_up_login_cookie_does_not_make_an_agent_the_owner(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "ensure_api_token", lambda: "real-token")
    name = settings.auth_cookie_name
    assert is_agent_request(_request({"Authorization": "Bearer real-token", "Cookie": f"{name}=whatever"}))
    assert not is_agent_request(_request({"Authorization": "Bearer real-token", "Cookie": f"{name}=real-token"}))
    assert not is_agent_request(_request({"Cookie": f"{name}=real-token"}))
