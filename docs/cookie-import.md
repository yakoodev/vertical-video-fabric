# Импорт cookies для аккаунтов

Сервис не использует логин и пароль платформ. Для публикации нужен сохраненный браузерный session/cookie набор уже залогиненного аккаунта.

Cookies хранятся зашифрованно в Docker volume `/data`. В API и UI они обратно не показываются.

## Перед импортом

1. Откройте аккаунт в обычном браузере и убедитесь, что вы залогинены.
2. Если аккаунт должен публиковать через отдельный proxy, откройте платформу в браузере через тот же proxy или хотя бы тот же IP-сегмент.
3. В сервисе укажите этот proxy в поле `Publishing Proxy` аккаунта.
4. Не вставляйте cookies в логи, issue, commit message или `.env`.

Почему proxy важен: YouTube и TikTok могут привязывать session cookies к поведенческому контексту и IP. Если cookies сняты с одного IP, а публикация идет через другой proxy, задача может перейти в `needs_reauth`.

## Форматы, которые принимает сервис

Поддерживаются два формата.

### Raw Cookie header

Одна строка из DevTools Network:

```text
SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
```

Можно вставлять и с префиксом:

```text
Cookie: SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
```

### Netscape cookie export

Многострочный формат:

```text
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	2147483647	SID	...
.youtube.com	TRUE	/	TRUE	2147483647	SAPISID	...
```

Для живой работы Netscape export обычно надежнее, потому что сохраняет domain/path/httpOnly metadata.

## Импорт через Web UI

1. Откройте `http://localhost:8088/accounts`.
2. Выберите `Platform`: `YouTube` или `TikTok`.
3. Введите `Label`, например `yt-main` или `tt-us-proxy-1`.
4. В `Publishing Proxy` укажите proxy для этого аккаунта, например:

```text
http://user:pass@host:port
```

Если оставить поле пустым, сервис использует глобальный `POSTING_PROXY_URL`, если он задан. Если глобального proxy нет, публикация пойдет напрямую.

5. Вставьте cookies в поле `Cookie Header or Netscape Cookies`.
6. Нажмите `Save Account`.
7. Проверьте таблицу `Saved Accounts`: `Required` должен быть `ok`, а `Proxy` должен показать redacted proxy или `global proxy`/`direct`.

## Импорт через API

Все API-запросы требуют Bearer token:

```text
Authorization: Bearer <token>
```

Текущий токен:

```powershell
docker compose exec app cat /data/api_token.txt
```

Пример добавления YouTube аккаунта:

```bash
curl -X POST http://localhost:8088/api/accounts \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "youtube",
    "label": "yt-main",
    "proxy_url": "http://user:pass@host:port",
    "cookie": "SID=...; HSID=...; SSID=...; APISID=...; SAPISID=..."
  }'
```

Пример добавления TikTok аккаунта:

```bash
curl -X POST http://localhost:8088/api/accounts \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "tiktok",
    "label": "tt-main",
    "proxy_url": "http://user:pass@host:port",
    "cookie": "sessionid=...; msToken=...; tt-target-idc=..."
  }'
```

Если аккаунт уже существует с тем же `platform + label`, запрос обновит cookies и proxy.

## YouTube: как снять cookies

Рекомендуемый источник cookies: запросы к `https://studio.youtube.com`.

Вариант через DevTools:

1. Откройте `https://studio.youtube.com` под нужным аккаунтом.
2. Откройте DevTools: `F12`.
3. Перейдите во вкладку `Network`.
4. Обновите страницу.
5. Найдите запрос к `studio.youtube.com`.
6. В `Request Headers` скопируйте значение `Cookie`.
7. Вставьте его в сервис.

Минимальные cookies, которые проверяет сервис:

```text
SID
HSID
SSID
APISID
SAPISID
```

На практике лучше импортировать весь набор `.youtube.com` cookies, включая `LOGIN_INFO`, `VISITOR_INFO1_LIVE`, `__Secure-*`, `PREF`, `YSC`, если они есть.

Важно:

- Не смешивайте `.google.com` cookies вручную в raw `Cookie` header для YouTube.
- Netscape export может содержать `.google.com`, сервис сам отфильтрует cookies по host при публикации.
- Если YouTube возвращает `needs_reauth`/401, первым делом обновите cookies из той же браузерной сессии и proxy.

## TikTok: как снять cookies

Рекомендуемый источник cookies: запросы к `https://www.tiktok.com`.

Вариант через DevTools:

1. Откройте `https://www.tiktok.com` под нужным аккаунтом.
2. Откройте DevTools: `F12`.
3. Перейдите во вкладку `Network`.
4. Обновите страницу или откройте профиль.
5. Найдите запрос к `www.tiktok.com`.
6. В `Request Headers` скопируйте значение `Cookie`.
7. Вставьте его в сервис.

Минимальный cookie, который проверяет сервис:

```text
sessionid
```

На практике нужен полный набор `.tiktok.com` cookies: `sessionid`, `msToken`, `tt-target-idc`, `ttwid`, `passport_csrf_token`, `s_v_web_id` и остальные текущие cookies аккаунта.

Если задача переходит в `needs_reauth`, обновите cookies. Частые причины:

- TikTok потребовал captcha/challenge.
- Cookies сняты на одном IP, а публикация идет через другой proxy.
- Истек или изменился `sessionid`.
- В аккаунте включились дополнительные проверки безопасности.

## Проверка после импорта

1. Откройте `New Post`.
2. Выберите нужный account target.
3. Отправьте короткий тестовый mp4 с `privacy=public` или `private`.
4. Откройте `Jobs`.
5. Успешная публикация должна иметь target status `succeeded` и remote id/url, если платформа вернула ссылку.

Статусы:

- `queued`: задача ожидает worker.
- `running`: идет upload/publish.
- `succeeded`: публикация завершена.
- `failed`: ошибка запроса, upload или платформы.
- `needs_reauth`: cookies больше не принимаются или платформа требует challenge.
