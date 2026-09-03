# Развёртывание — веб-сервер Meeting Summarizer

[English](DEPLOYMENT.md) · **Русский**

Веб-слой — приложение FastAPI (`server/`) на **SQLite**, которое запускает обработку через
**встроенный python-рантайм** (`backend/python/python.exe`) как подпроцесс. Никакого Postgres
и отдельного сервиса моделей: всё крутится на одной машине с встроенным рантаймом. Docker
намеренно **не используется** — рантайм обработки это Windows-сборка python с CUDA, она не
укладывается в Linux-контейнер. Разворачивайте как **службу / задачу планировщика** на хосте.

## 1. Предварительные требования

- Репозиторий с встроенным рантаймом `backend/python/` и моделями транскрибации/AI.
- **venv сервера** (лёгкий, без torch) с веб-зависимостями:
  ```powershell
  py -m venv server\.venv
  server\.venv\Scripts\pip install -r server\requirements.txt
  ```
- Для транскрибации на GPU и автомасштабирования воркеров — видеокарта NVIDIA (torch/CUDA
  есть во встроенном рантайме, в venv сервера их нет; GPU определяется через встроенный python).

## 2. Конфигурация (переменные окружения)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `JWT_SECRET_KEY` | *(генерируется автоматически, сохраняется в `config/.jwt_secret`)* | **Задайте в продакшене.** Подписывает токены авторизации. Если не задана, генерируется случайный секрет и сохраняется на диск, чтобы токены пережили перезапуск, но явная установка предпочтительнее (позволяет ротацию и масштабирование). |
| `PORT` | `8000` | Порт прослушивания. |
| `HOST` | `0.0.0.0` | Адрес привязки. |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Источники CORS через запятую. Укажите свои реальные. |
| `MAX_UPLOAD_BYTES` | `10737418240` (10 ГиБ) | Жёсткий лимит одного загружаемого файла; проверяется в потоке, даже если `Content-Length` отсутствует или подделан. |
| `ALLOW_PRIVATE_URLS` | `false` | На публичном сервере оставьте `false`: импорт по URL не сможет обращаться к localhost, частным, link-local и другим непубличным адресам. Включайте только в доверенной внутренней сети, если импорт из интранета нужен намеренно. |
| `DATABASE_URL` | `sqlite:///<repo>/config/server.db` | Асинхронный URL SQLite. Меняйте только чтобы перенести БД. |
| `TRUSTED_PROXIES` | `127.0.0.1` | Кто может выставлять `X-Forwarded-For`/`X-Forwarded-Proto`. Укажите адрес обратного прокси, если он не на этой машине. |
| `SERVER_MODE` | *(ставится лаунчером)* | Должна быть `true`; лаунчер выставляет её сам. |

Смена `JWT_SECRET_KEY` один раз обнуляет все выданные токены (все переавторизуются).

## 3. Первый запуск и администратор

**Первая учётная запись, зарегистрированная на чистой установке, становится администратором** —
это аккаунт того, кто разворачивает сервер. Все, кто регистрируются после, обычные
пользователи и видят только свои встречи.

У администратора в шапке кабинета появляется кнопка **Администрирование** (остальным она не
показывается) — там всё, что действует на установку целиком:

| Операция | Действие | Эндпоинт |
|---|---|---|
| Параллельные воркеры | управление нагрузкой машины; **сохраняется**, поэтому переживает перезапуск, а не сбрасывается на автоопределение по железу | `PUT /api/admin/settings`, `POST /api/queue/workers/{n}` |
| Скачать модель распознавания | файл на диске, которым потом пользуются все аккаунты | `POST /api/engines/{engine}/models/{model}/download` |
| Проверить обновление модели | сравнивает локальную ревизию с опубликованной | `GET /api/engines/{engine}/models/{model}/update-check` |
| Установить пакеты движка | меняет установку для всех пользователей | `POST /api/admin/engines/{engine}/install` |

Всё остальное — провайдер ИИ, промпты, функции анализа, хранилище Obsidian, RAG — остаётся
**персональным**: два аккаунта на одном сервере могут работать с разными моделями и
провайдерами. Язык интерфейса и тема выбираются в браузере и вообще не являются серверными
настройками.

Назначить администратора позже можно прямо в базе (сервер при этом можно не останавливать):

```powershell
backend\python\python.exe -c "import sqlite3; c=sqlite3.connect(r'config/server.db'); c.execute(\"update users set role='admin' where username=?\", ('ivan',)); c.commit(); print(c.execute('select username, role from users').fetchall())"
```

Если вы разворачиваете сервер из **готовой раздачи**, а не из репозитория, шаг с venv не нужен:

- **full** — распаковать и запустить `SERVER.bat`; во встроенном рантайме уже всё есть.
- **min** — распаковать, один раз запустить `INSTALL.bat` и отметить в списке компонентов
  *Web cabinet*, затем запустить `SERVER.bat`. Python ставить заранее не нужно: если его на
  машине нет, `INSTALL.bat` предложит поставить 3.11 сам. Если компонент не отметить,
  `SERVER.bat` не упадёт трейсбеком, а скажет, чего не хватает и что переотметить.

## 4. Запуск

```powershell
# по умолчанию (0.0.0.0:8000)
server\start_server.ps1

# свой порт + продакшен-секрет
$env:JWT_SECRET_KEY = "<длинная-случайная-строка>"
server\start_server.ps1 -Port 9000
```

Лаунчер выставляет `SERVER_MODE`, запускается из корня репозитория (чтобы `uploads/`,
`transcripts/`, `config/` резолвились одинаково) и стартует uvicorn из venv сервера.
Интерфейс отдаётся по `/` (вход) и `/dashboard.html`; документация API — на `/api/docs`.

## 5. Автозапуск (старт при загрузке, перезапуск при падении)

**Планировщик заданий** (проще всего, без дополнительных инструментов):

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"C:\Scripts\meeting-summarizer\server\start_server.ps1`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$set     = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "MeetingSummarizerServer" -Action $action -Trigger $trigger `
    -Settings $set -RunLevel Highest -User "SYSTEM"
```

Задайте `JWT_SECRET_KEY` (и остальные переменные) как **системные** переменные окружения,
чтобы задача их подхватила, либо пропишите их в начале `start_server.ps1`.

Для настоящей windows-*службы* с супервизией оберните ту же команду через
[NSSM](https://nssm.cc/): `nssm install MeetingSummarizerServer powershell.exe "-NoProfile -ExecutionPolicy Bypass -File C:\Scripts\meeting-summarizer\server\start_server.ps1"`.

## 6. Сеть / HTTPS

Приложение отдаёт обычный HTTP. Для доступа извне поставьте его за обратный прокси
(IIS/ARR, Caddy или nginx), который терминирует TLS и проксирует на `HOST:PORT`. В
`ALLOWED_ORIGINS` укажите публичный origin.

Со стороны браузера настраивать нечего: интерфейс берёт адрес API из
`window.location.origin` и сам переключает сокет живого прогресса на `wss://`, когда
страница отдаётся по HTTPS. А вот прокси требует трёх настроек, дефолты которых ломают
работу:

| Настройка | Зачем |
|---|---|
| **Upgrade WebSocket на `/ws/`** и большой read timeout (≥ 1 ч) | живой прогресс — одно соединение на всю обработку; дефолтные 60 с в nginx рвут его посреди транскрибации |
| **Максимальный размер тела ≥ вашего лимита загрузки** (`client_max_body_size` в nginx, `maxAllowedContentLength`/`maxRequestLength` в IIS) | загружаются целые записи встреч; дефолтный 1 МБ в nginx отклонит любую. Держите в согласии с `MAX_UPLOAD_BYTES` |
| **Прокидывать `X-Forwarded-For` / `X-Forwarded-Proto`** | сервер их читает (`proxy_headers=True`); если прокси не на `127.0.0.1`, укажите его адрес в `TRUSTED_PROXIES` |

nginx, значимая часть:

```nginx
server {
    server_name meetings.example.com;
    client_max_body_size 10g;                  # согласовать с MAX_UPLOAD_BYTES

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

Caddy достаточно `reverse_proxy 127.0.0.1:8000` (WebSocket и forwarded-заголовки он делает
сам) плюс `request_body { max_size 10GB }`.

Про хост: рантайм обработки — **встроенный Windows-python с CUDA**, поэтому VPS должен быть
на Windows. Linux-VPS годится только как TLS-фронт, само приложение там не работает.

## 7. Данные и резервные копии

Постоянное состояние лежит внутри репозитория:

- `config/server.db` — пользователи, встречи, настройки, версии артефактов.
- `config/.jwt_secret` — ключ подписи (сохраняйте в бэкап, если полагаетесь на него, а не на
  переменную окружения).
- `uploads/` — исходные медиафайлы; `transcripts/` — транскрипты и сгенерированные экспорты.
- `rag_data/u<id>/` — изолированная база знаний пользователя (режим по умолчанию).
- `rag_shared/<sha256 ключа>/` — базы, которые server-аккаунт делит с desktop в той же
  установке по секретному коду. Сам код хранится в настройках пользователя в БД.

Бэкапьте `config/`, `transcripts/`, `rag_data/` и `rag_shared/` (и `uploads/`, если храните
исходники). SQLite — один
файл: копируйте `config/server.db` при остановленном сервере либо используйте `.backup`.

## 8. Обновление

1. Остановите службу/задачу.
2. Обновите код; если изменился `server/requirements.txt`:
   `server\.venv\Scripts\pip install -r server\requirements.txt`.
3. Если менялся веб-интерфейс, пересоберите стили: `cd server\web; npm run build:css`.
4. Запустите службу. Статика отдаётся с `Cache-Control: no-cache`, поэтому браузеры подхватят
   новые JS/CSS при следующей загрузке (жёсткая перезагрузка не нужна). Миграции БД
   аддитивные и применяются автоматически при старте (`init_db` → `_ensure_columns`).
