# Развёртывание Vertical Video Fabric

Гайд: как поднять сервис на новом ПК/сервере через Docker. Всё состояние (БД,
секреты, загруженные видео, отрендеренные клипы, модели Whisper) живёт в одном
Docker-томе — перенос и бэкап тривиальны.

---

## 1. Что нужно на целевой машине

- **Docker** + **Docker Compose v2**
  - Windows/macOS: установить **Docker Desktop**.
  - Linux: `docker` + плагин `docker-compose-plugin` (команда `docker compose`).
- **Git** (чтобы склонировать репозиторий).
- **Интернет на время первой сборки** — образ при сборке клонирует TiktokAutoUploader,
  ставит npm- и pip-зависимости, фронт собирается внутри образа.
- Диск: ~3–4 ГБ под образ + место под видео/клипы (зависит от объёма работы).
- ОЗУ: 4 ГБ минимум, 8+ ГБ комфортно (Whisper на CPU ест ядра при транскрибации).

> GPU не требуется. Whisper по умолчанию работает на CPU (`int8`).

---

## 2. Получить код

```bash
git clone https://github.com/yakoodev/vertical-video-fabric.git
cd vertical-video-fabric
git checkout codex/real-pipeline-api   # рабочая ветка (пока не смержена в main)
```

---

## 3. Настроить `.env`

```bash
cp .env.example .env
```

Открой `.env` и заполни. **Минимум для боевого запуска:**

```ini
# --- доступ к веб-интерфейсу ---
POSTING_AUTH_ENABLED=true
POSTING_API_TOKEN=                 # задай свой длинный токен (см. ниже). Пусто = сгенерится автоматически

# --- режим публикации ---
POSTING_PROVIDER_MODE=real         # real = реально публикует; mock = ничего не шлёт (для теста пайплайна)
POSTING_PROXY_URL=                 # опционально, общий прокси для загрузок

# --- AI-анализ и субтитры ---
AI_VIDEO_PROVIDER=gemini           # gemini | polza | action | mock
SUBTITLE_PROVIDER=whisper          # whisper (локально) | gemini | mock

GEMINI_API_KEY=...                 # ключ Google AI Studio (если AI_VIDEO_PROVIDER=gemini)
GEMINI_VIDEO_MODEL=gemini-2.5-flash

POLZA_API_KEY=...                  # если используешь провайдер polza
```

**Про токен доступа:**
- Если задать `POSTING_API_TOKEN` — это и есть пароль для входа в веб-интерфейс.
  Сгенерировать надёжный: `openssl rand -base64 32` (или придумать длинную строку).
- Если оставить пустым — приложение само сгенерирует токен при первом старте и
  запишет его в `/data/api_token.txt` внутри тома. Прочитать его:
  ```bash
  docker compose exec app cat /data/api_token.txt
  ```

Полный список переменных и значений по умолчанию — в [.env.example](.env.example).
Часто полезные:
- `WHISPER_MODEL_SIZE=small` — размер модели Whisper (`tiny`/`base`/`small`/`medium`). Больше = точнее и медленнее.
- `MAX_UPLOAD_BYTES=1073741824` — лимит размера загружаемого файла (1 ГБ).
- `GEMINI_HTTP_RETRIES=5` — ретраи при перегрузке Gemini.

---

## 4. Запуск

```bash
docker compose up -d --build
```

- `--build` — собрать образ (нужно при первом запуске и после `git pull`).
- `-d` — в фоне.

Проверить, что поднялось:

```bash
docker compose ps
docker compose logs -f app          # логи (Ctrl+C чтобы выйти из просмотра)
```

Открой в браузере: **http://localhost:8088** → введи токен из шага 3.

> Сервис слушает порт **8088**. Поменять: в `docker-compose.yml` строка `ports: - "8088:8088"` → первое число это порт на хосте.

---

## 5. Где живут данные (том `vv-fabric-data` → `/data`)

Внутри тома:
- `app.sqlite` — база (проекты, анализы, клипы, аккаунты, пресеты, задачи).
- `secret.key` — ключ шифрования куки аккаунтов. **Не теряй** — иначе сохранённые куки не расшифруются.
- `api_token.txt` — авто-сгенерированный токен (если не задал свой).
- загруженные исходники, отрендеренные клипы, кадры сторибордов.
- `whisper-models/` — модели Whisper (качаются один раз при первой транскрибации).

Дефолтный пак (луки рендера, стили субтитров, промпты) засевается автоматически
при первом старте — на новой машине заготовки уже на месте.

---

## 6. Бэкап и перенос на другую машину

Всё состояние = один том. Перенос:

```bash
# на старой машине: выгрузить том в архив
docker run --rm -v vv-fabric-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/vvf-data.tar.gz -C /data .

# на новой машине (после clone + .env + build, но до или после первого старта):
docker run --rm -v vv-fabric-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/vvf-data.tar.gz -C /data
```

(Имя тома по умолчанию `vv-fabric-data`; если меняешь префикс проекта в compose — поправь имя.)

Регулярный бэкап — достаточно периодически архивировать этот том (или хотя бы
`app.sqlite` + `secret.key`).

---

## 7. Подключить аккаунты для публикации

В интерфейсе: **Настройки → Аккаунты → Добавить**. Выбери платформу и вставь куки:

- **YouTube** — куки studio.youtube.com (нужны `SID, HSID, SSID, APISID, SAPISID`).
  Ротируемые куки сервис **сам обновляет** после каждой загрузки, так что сессия
  живёт долго.
- **TikTok** — кука `sessionid`.
- **Instagram** — кука `sessionid` (публикация Reels через instagrapi).

Куки удобно выгружать расширением-экспортёром cookies (формат Netscape или заголовок
`name=value; name=value`). Опционально на аккаунт можно задать прокси.

---

## 8. Обновление до новой версии

```bash
git pull
docker compose up -d --build
```

Том с данными переживает пересборку. Миграции БД применяются автоматически при старте.

---

## 9. Управление

```bash
docker compose stop          # остановить
docker compose start         # запустить снова
docker compose restart app   # перезапуск
docker compose down          # остановить и удалить контейнер (ТОМ остаётся)
docker compose down -v       # ⚠️ удалить ВМЕСТЕ с томом (сотрёт все данные!)
```

---

## 10. Удалённый доступ (опционально)

По умолчанию сервис доступен на `localhost:8088`. Чтобы открыть наружу:

1. **Не публикуй порт 8088 в интернет напрямую без TLS.** Токен ходит в куке.
2. Поставь обратный прокси (Nginx Proxy Manager / Caddy / Traefik) с HTTPS и
   проксируй на `app:8088`. Тогда вход по HTTPS, кука `Secure` отработает корректно.
3. Убедись, что `POSTING_AUTH_ENABLED=true` и токен — длинный и секретный.

---

## 11. Траблшутинг

- **Не открывается / 502** — `docker compose logs app`. Часто это незаполненный `.env`
  или занятый порт 8088.
- **«Invalid token» на входе** — токен не совпадает. Возьми актуальный:
  `docker compose exec app cat /data/api_token.txt` (или сверь `POSTING_API_TOKEN`).
- **Анализ падает с «high demand»** — перегрузка Gemini на стороне Google,
  не баг. Перезапусти анализ; ретраи с `Retry-After` обычно проходят.
- **YouTube/Instagram статус `needs_reauth`** — куки протухли или невалидны;
  обнови их в Настройках → Аккаунты.
- **Сборка падает на clone/npm/pip** — нет интернета во время `--build`. Дай доступ и пересобери.
- **Whisper долго думает на длинном видео** — это первый проход транскрибации (CPU).
  Результат кэшируется на исходнике; повторный анализ его переиспользует.

---

## Кратко (TL;DR)

```bash
git clone https://github.com/yakoodev/vertical-video-fabric.git
cd vertical-video-fabric && git checkout codex/real-pipeline-api
cp .env.example .env        # заполнить ключи + POSTING_API_TOKEN
docker compose up -d --build
# открыть http://localhost:8088, войти по токену
```
