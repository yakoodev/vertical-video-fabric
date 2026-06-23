# Развёртывание на Windows с нуля

Пошаговая установка Vertical Video Fabric на чистый Windows 10/11 ПК через Docker.
Рассчитано на новичка — по порядку, без пропусков. Время: ~30–40 минут (большая часть — ожидание).

> Всё состояние сервиса (база, видео, клипы, аккаунты) хранится в одном Docker-томе.
> Перенос/бэкап — копированием этого тома. GPU не нужен.

---

## 0. Что нужно от ПК

- **Windows 10 (64-bit, версия 2004+) или Windows 11.**
- **8 ГБ ОЗУ** (минимум 4), ~5–10 ГБ свободного диска под образ и видео.
- **Включённая виртуализация** в BIOS/UEFI (нужна для WSL2/Docker).
  Проверка: `Ctrl+Shift+Esc` → **Диспетчер задач** → вкладка **Производительность** → **ЦП** →
  справа внизу строка **«Виртуализация: Включено»**. Если «Отключено» — включить в BIOS
  (Intel VT-x / AMD-V или SVM). Без этого Docker не запустится.
- **Интернет** (особенно на время первой сборки).

---

## 1. Установить WSL2 (движок для Docker)

1. Нажмите **Пуск**, наберите **PowerShell**, правый клик → **Запуск от имени администратора**.
2. Выполните:
   ```powershell
   wsl --install
   ```
   Команда поставит WSL2 и Ubuntu по умолчанию.
3. **Перезагрузите ПК** (обязательно).
4. После перезагрузки может открыться окно Ubuntu с просьбой задать имя пользователя/пароль —
   задайте любые (для самого сервиса они не нужны) или просто закройте окно.

> Если `wsl --install` пишет, что компонент уже установлен — всё ок, идём дальше.

---

## 2. Установить Docker Desktop

**Вариант А (проще) — через winget** (в обычном PowerShell):
```powershell
winget install -e --id Docker.DockerDesktop
```

**Вариант Б — вручную:** скачайте «Docker Desktop for Windows» с
`https://www.docker.com/products/docker-desktop/` и установите (галка
**«Use WSL 2 instead of Hyper-V»** должна быть включена).

После установки:
1. Запустите **Docker Desktop** (Пуск → Docker Desktop). Первый старт — 1–2 минуты.
2. Дождитесь, пока значок кита 🐳 в трее станет стабильным (статус **Running**).
3. Если попросит включить WSL2 integration — согласитесь.

> Docker Desktop должен быть **запущен** каждый раз, когда вы работаете с сервисом.
> Можно включить автозапуск: Settings → General → «Start Docker Desktop when you sign in».

---

## 3. Установить Git

**winget:**
```powershell
winget install -e --id Git.Git
```
или вручную: `https://git-scm.com/download/win` (установка «по умолчанию», ничего менять не надо).

После установки **закройте и снова откройте PowerShell**, чтобы команда `git` появилась.

---

## 4. Скачать проект

В PowerShell выберите папку и склонируйте репозиторий:
```powershell
cd $HOME\source\repos        # или любая ваша папка; создастся при clone
git clone https://github.com/yakoodev/vertical-video-fabric.git
cd vertical-video-fabric
```

---

## 5. Настроить `.env`

Скопируйте шаблон и откройте на редактирование:
```powershell
copy .env.example .env
notepad .env
```

Заполните минимум (остальное можно оставить как есть):
```ini
POSTING_AUTH_ENABLED=true
POSTING_API_TOKEN=придумайте-длинный-секрет   # это пароль для входа в веб-интерфейс
POSTING_PROVIDER_MODE=real                     # real = публикует; mock = тест без отправки

AI_VIDEO_PROVIDER=gemini
SUBTITLE_PROVIDER=whisper
GEMINI_API_KEY=ваш-ключ-google-ai-studio
GEMINI_VIDEO_MODEL=gemini-3.5-flash
```
Сохраните файл (Файл → Сохранить) и закройте Notepad.

> Если оставить `POSTING_API_TOKEN` пустым — сервис сам сгенерирует токен при первом запуске,
> прочитать его можно командой из шага 7.

---

## 6. Собрать и запустить

В той же папке (`vertical-video-fabric`), при запущенном Docker Desktop:
```powershell
docker compose up -d --build
```
- Первая сборка — **10–20 минут** (качаются зависимости, собирается фронтенд). Это разово.
- `-d` — запуск в фоне.
- При первом запуске Windows может показать запрос брандмауэра — нажмите **«Разрешить доступ»**.

Проверить, что поднялось:
```powershell
docker compose ps
docker compose logs -f app    # логи; выйти из просмотра — Ctrl+C
```

---

## 7. Первый вход

Откройте в браузере **http://localhost:8088** и введите токен из `.env`.

Если токен не задавали — узнать сгенерированный:
```powershell
docker compose exec app cat /data/api_token.txt
```

Дальше — как пользоваться сервисом: **[Руководство пользователя](user-guide.md)**.
Подключение аккаунтов и cookies: **[Импорт cookies](cookie-import.md)**.

---

## 8. Повседневное управление

```powershell
docker compose stop          # остановить
docker compose start         # запустить снова (Docker Desktop должен быть включён)
docker compose restart app   # перезапустить
docker compose logs -f app   # смотреть логи
```

**Обновить до новой версии:**
```powershell
cd $HOME\source\repos\vertical-video-fabric
git pull
docker compose up -d --build
```
Данные переживают обновление (миграции базы применяются сами).

**Бэкап данных** (база, ключ шифрования, видео, клипы — всё в томе `vv-fabric-data`):
```powershell
docker run --rm -v vv-fabric-data:/data -v ${PWD}:/backup alpine tar czf /backup/vvf-data.tar.gz -C /data .
```
Файл `vvf-data.tar.gz` появится в текущей папке. Восстановление на другом ПК:
```powershell
docker run --rm -v vv-fabric-data:/data -v ${PWD}:/backup alpine tar xzf /backup/vvf-data.tar.gz -C /data
```

> ⚠️ `docker compose down -v` удаляет том вместе со всеми данными. Просто `docker compose down`
> (без `-v`) — безопасно, том остаётся.

---

## 9. Если что-то не так

| Симптом | Что делать |
|---|---|
| `docker: command not found` / «не является командой» | Docker Desktop не установлен или PowerShell открыт до установки — переоткройте PowerShell, проверьте, что Docker Desktop запущен. |
| `error during connect` / `pipe/dockerDesktopLinuxEngine` | Docker Desktop не запущен — откройте его и дождитесь статуса Running. |
| `wsl` ошибка / Docker не стартует | Не включена виртуализация в BIOS (см. шаг 0) или не сделана перезагрузка после `wsl --install`. |
| Сборка падает на загрузке зависимостей | Нет интернета во время `--build`. Дайте доступ и повторите команду. |
| `http://localhost:8088` не открывается | `docker compose ps` — контейнер должен быть `running`; смотрите `docker compose logs app`. |
| «Invalid token» при входе | Сверьте токен: `docker compose exec app cat /data/api_token.txt` или значение `POSTING_API_TOKEN` в `.env`. |
| Порт 8088 занят | Поменяйте в `docker-compose.yml` строку `ports: - "8088:8088"` (левое число — порт на ПК). |

---

## 10. (Опционально) Запуск без Docker

Для разработки есть лаунчер на Python+Node: `scripts\start-local.ps1` (см. README).
Для обычной эксплуатации рекомендуется Docker по инструкции выше — меньше ручных зависимостей.
