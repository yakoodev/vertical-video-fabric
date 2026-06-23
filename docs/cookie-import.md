# Импорт cookies для аккаунтов

Сервис не использует логин и пароль платформ. Для публикации нужен сохранённый браузерный session/cookie набор уже залогиненного аккаунта.

Cookies хранятся зашифрованно в Docker volume `/data`. В API и UI они обратно не показываются.

> ⚠️ **Cookies (`sessionid`, `SID`, …) — это живой ключ от аккаунта.** Никому не пересылай их
> (в чат, мессенджеры, issue, commit). Вставляй только в поле приложения.

## Быстрый способ — расширение браузера (рекомендуется)

Подходит для всех площадок одинаково. На примере Firefox + **Get cookies.txt LOCALLY**
(open-source, экспортирует локально, ничего не шлёт наружу; в Chrome — аналогичное расширение).

**1. Поставь расширение.** Firefox: меню `≡` → «Дополнения и темы» (или `Ctrl+Shift+A`) →
поиск `Get cookies.txt LOCALLY` (автор *kairi*) → «Добавить в Firefox».

**2. Открой площадку залогиненным и экспортни куки.** Открой нужный сайт (для YouTube — именно
`studio.youtube.com`), нажми иконку расширения, выбери формат **Netscape** и жми **Copy** —
все куки сайта уйдут в буфер.

![Экспорт cookies расширением](img/cookie-01-extension.svg)

**3. Вставь в сервис.** В приложении: **Настройки → Аккаунты** → выбери платформу, впиши метку,
(опц.) прокси, вставь куки в поле Cookies (`Ctrl+V`) и сохрани.

![Форма добавления аккаунта](img/cookie-02-account-form.svg)

**4. Проверь статус.** У карточки аккаунта должен быть статус **«готов»** и число куки —
значит обязательные куки распознаны и аккаунт готов к публикации.

![Аккаунт готов](img/cookie-03-ready.svg)

Дальше публикуй: вкладка **Клипы** → у клипа **Опубликовать** (или массово на вкладке **Авто**),
статус смотри на странице **Задачи**.

Ниже — детали по форматам, ручной способ через DevTools и нюансы по каждой платформе.

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

1. Откройте `http://localhost:8088` и перейдите в **Настройки → Аккаунты**.
2. Выберите `Платформа`: `youtube`, `tiktok` или `instagram`.
3. Введите `Метку`, например `yt-main` или `tt-us-proxy-1`.
4. (Опц.) укажите proxy для этого аккаунта, например `http://user:pass@host:port`.
   Если пусто — используется глобальный `POSTING_PROXY_URL`, а если и его нет — публикация идёт напрямую.
5. Вставьте cookies в поле для куки (Raw Cookie header или Netscape export).
6. Сохраните аккаунт.
7. В списке аккаунтов у записи должно быть отмечено, что обязательные куки на месте (`Required: ok`).

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

Пример добавления Instagram аккаунта:

```bash
curl -X POST http://localhost:8088/api/accounts \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "instagram",
    "label": "ig-main",
    "proxy_url": "http://user:pass@host:port",
    "cookie": "sessionid=..."
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
- Сервис **сам обновляет ротируемые куки** (`__Secure-*PSIDTS` и т.п.) после каждой успешной загрузки и пишет их обратно в аккаунт, поэтому сессия живёт долго — не нужно переснимать куки каждые пару дней.

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

## Instagram: как снять cookies

Сервис публикует Reels через instagrapi, авторизуясь по куке `sessionid`.

1. Откройте `https://www.instagram.com` под нужным аккаунтом (лучше через тот же proxy/IP).
2. DevTools (`F12`) → вкладка `Application` (или `Storage`) → `Cookies` → `https://www.instagram.com`.
3. Скопируйте значение `sessionid` (либо весь Cookie header из `Network`).
4. Вставьте в сервис: достаточно `sessionid=...`.

Минимальный cookie, который проверяет сервис:

```text
sessionid
```

Важно:

- instagrapi — неофициальный приватный API. На ценных аккаунтах есть риск бана/challenge:
  прогревайте аккаунт, не лейте десятками в час, используйте стабильный proxy/IP.
- При `login_required`/`challenge_required`/`checkpoint` задача уйдёт в `needs_reauth` —
  переснимите `sessionid` (пройдя при необходимости проверку в браузере).

## Проверка после импорта

1. Откройте проект → вкладку **Клипы** (или **Смонтированные**) и нажмите `Опубликовать` у клипа,
   либо запустите авто-конвейер на вкладке **Авто** с выбранными аккаунтами.
2. Выберите нужный account target и приватность, отправьте.
3. Следите за статусом на странице **Задачи** (или **Публикации**).
4. Успешная публикация имеет статус `succeeded` и remote id/url, если платформа вернула ссылку.

Статусы:

- `queued`: задача ожидает worker.
- `running`: идет upload/publish.
- `succeeded`: публикация завершена.
- `failed`: ошибка запроса, upload или платформы.
- `needs_reauth`: cookies больше не принимаются или платформа требует challenge.
