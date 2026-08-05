# Meeting Summarizer

![Version](https://img.shields.io/badge/version-1.2.1-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

[English](README.md) · **Русский**

Локальный инструмент: из записи встречи делает структурированное **саммари** и подробный
**анализ** — транскрибация (7 движков, работает офлайн) → саммари → анализ из 11 фич →
экспорт (txt/md/json/html/pdf/docx, Obsidian, Google Sheets). Всё может работать на вашей
машине; облачные AI-провайдеры — по желанию.

## Два фронтенда, один бэкенд

Проверенный Python-бэкенд (`backend/`) общий для двух независимых фронтендов:

- **Десктоп** (`desktop/`) — нативное приложение на **PySide6**: перетаскивание, добавление по
  ссылке, запись с микрофона, нарезка на отдельные встречи, живая очередь, управление
  спикерами, панели анализа, RAG-поиск, диагностика.
- **Веб-кабинет** (`server/`) — многопользовательский сервис **FastAPI** (JWT, SQLite) с
  дашбордом на скомпилированном Tailwind. Тот же набор функций, но по HTTP — включая
  запись с микрофона прямо в браузере (нужен HTTPS или localhost), нарезку одной
  записи на отдельные встречи по волне, поиск, статистику, управление базой знаний
  (что проиндексировано, её объём, удаление документа) и экспорт в хранилище
  Obsidian. Всё, что общее для установки — число воркеров, скачивание и обновление
  моделей, установка движков — доступно **только администратору** и действует на все
  аккаунты; остальное персонально.

Оба гоняют один встроенный runtime (`backend/python`) как подпроцессы.

## Как запустить

Нужен работающий приёмник AI — локальный эндпоинт, облачный ключ, локальный агент-CLI или
встроенный ИИ (см. ниже).

**Запуск — двойной клик, консоль не нужна:** `RUN.bat` (десктоп) или `SERVER.bat` (веб-кабинет,
затем откройте `http://localhost:8000`). Эти же файлы лежат в корне портативной сборки. То же
из консоли: `backend\python\python.exe desktop\run.py` /
`backend\python\python.exe server\run_server.py`.

Дальше: **Настройки** → выберите, как работает ИИ (ниже); движок `faster-whisper`, модель
`medium`. Если локальная LLM занимает весь GPU — включите галочку **«Освобождать VRAM под
транскрибацию»**: приложение остановит её на время транскрибации и вернёт обратно. Добавьте
файл (или запишите), при необходимости разбейте на фрагменты по встречам, нажмите
**«Обработать»**.

## Четыре способа обработать транскрипт

1. **Локальный эндпоинт** — любой OpenAI-совместимый сервер, который вы уже подняли
   (llama.cpp, LM Studio, Ollama).
2. **Облако** — OpenAI / Anthropic / Google / xAI / Qwen / Mistral / DeepSeek по API-ключу,
   плюс полностью кастомный запрос через Advanced API.
3. **Локальный агент-CLI** — отдать работу **Claude Code**, **Codex**, **Hermes** или любой
   команде, читающей stdin и печатающей ответ. Ключи и модель остаются в конфиге агента.
4. **Встроенный локальный ИИ** — для тех, кто ничего не поднимал: приложение само скачает
   актуальную сборку llama.cpp и подобранную под машину модель и запустит их. В дистрибутив
   не входит — качается по требованию.

На одной видеокарте резидентная LLM не оставляет места движку транскрибации, поэтому
приложение умеет передавать GPU: останавливает локальную модель (вашу — по порту, встроенную —
по id) на время транскрибации и возвращает обратно. Чтобы модель поднималась и после сбоев и
перезагрузок, запустите сторожа — во время транскрибации он не вмешивается:

```bash
python backend/local_ai_watchdog.py --model qwen3-14b        # постоянный присмотр
python backend/local_ai_watchdog.py --once                   # разовая проверка, для планировщика
```

Агент может работать и в обратную сторону: **MCP-сервер** открывает архив встреч как
инструменты (`list_meetings`, `get_transcript`, `get_summary`, `get_analysis`,
`search_transcripts`, `search_knowledge`) — см. [docs/MCP_USAGE.ru.md](docs/MCP_USAGE.ru.md).

## Собрать раздачи

```
backend\python\python.exe desktop\packaging\build.py --variant min  --out dist
backend\python\python.exe desktop\packaging\build.py --variant full --out dist
```

- **min** (~320 МБ) — исходники + ffmpeg + установщик; получатель один раз запускает
  `INSTALL.bat`. Установщик сканирует машину (Python, ОЗУ, диск, GPU/VRAM), рекомендует
  сборку torch под CUDA или CPU и даёт выбрать движки, модели, RAG, веб-кабинет и —
  по желанию — локальную LLM, прямо сообщая, если машина её не потянет. `--recommended --yes`
  пропускает меню, `--plan-only` печатает план, ничего не устанавливая.
- **full** (~12 ГБ) — встроенный runtime + по одной medium-модели на движок; распаковал и
  запустил, сеть не нужна.

## Требования

Windows; для быстрой транскрибации желателен GPU NVIDIA (runtime CUDA 12.4), но работает и на CPU
(медленнее). Full-сборка везёт runtime; min-сборке хватает `INSTALL.bat` — Python он поставит сам,
если его нет. Локальному ИИ
нужен OpenAI-совместимый эндпоинт или агент-CLI; облаку — ключ API.

**min-сборке нужен Python 3.9 – 3.12 (рекомендуется 3.11).** Если Python вообще нет —
`INSTALL.bat` предложит поставить 3.11 сам: только для текущего пользователя, с python.org,
с проверкой подписи Python Software Foundation перед запуском. Отказаться можно, тогда он
покажет, что поставить вручную. Не 3.13+: закреплённый `numpy<2.0`, который требуют текущие
версии torch и движков, не публикует под него колёса, и pip полезет собирать numpy из
исходников. Если такая версия уже стоит, `INSTALL.bat` останавливается с инструкцией, а не
падает на середине; несколько версий уживаются рядом (`py -0p` покажет установленные), и он
сам выберет подходящую. Отдельно учтите: на чистой Windows 11 `python` в PATH — это заглушка
Microsoft Store, а не интерпретатор; ставить оттуда не нужно, там 3.13+. Full-сборки всё это
не касается — она везёт свой 3.11.

## Карта документации

- [desktop/README.ru.md](desktop/README.ru.md) — десктоп: функции, запуск, модели, настройки.
- [desktop/ARCHITECTURE.ru.md](desktop/ARCHITECTURE.ru.md) — слои и поток данных (оба фронта) +
  диаграммы [C4-компонент](desktop/architecture-c4-component.puml) и
  [последовательности](desktop/architecture-sequence.puml).
- [desktop/ROADMAP.md](desktop/ROADMAP.md) — состояние и история десктопа.
- [docs/MCP_USAGE.ru.md](docs/MCP_USAGE.ru.md) — как агенту работать с архивом встреч.
- [docs/google-sheets/README.ru.md](docs/google-sheets/README.ru.md) — интеграция с Google Sheets (`code.gs` + настройка).
- [server/DEPLOYMENT.ru.md](server/DEPLOYMENT.ru.md) — деплой веб-кабинета; API-доки на `/api/docs`.
- [server/SERVER_ROADMAP.md](server/SERVER_ROADMAP.md) — состояние и история веб-слоя.
- [WHISPER_ENGINES_COMPATIBILITY.ru.md](WHISPER_ENGINES_COMPATIBILITY.ru.md) — движки и модели.
- [CONTRIBUTING.ru.md](CONTRIBUTING.ru.md) — как запустить, как устроены селфтесты, что нужно для PR.
- [SECURITY.ru.md](SECURITY.ru.md) — как приватно сообщить об уязвимости и что нельзя прикладывать.
- [THIRD-PARTY-NOTICES.ru.md](THIRD-PARTY-NOTICES.ru.md) — что едет в архивах и под какой
  лицензией. В поставке есть сборка FFmpeg под **GPLv3** и (в `full`) Qt под **LGPL**; их
  тексты лежат внутри архивов в [licenses/](licenses/).

## Поддержать автора

<p align="center">
  <img src="donate-qr.png" alt="Donate QR" width="200"/>
</p>

BTC: bc1q3frrup5neh7nhfg944etu2agd4j9u0vg3jyee6

ETH(Arbitrum): 0x43B349d8Cea83215D707EBa3bc35e9917f746b0a

TRX: THSzvy49KNeqRjXsGkurh2A5G4avV4RgN4

XRP: rLWZjS3DMupC4ZdXCX3BVYn4dEtC3iNhgy

SOL: 3xwfybxJ6Tz5t6pjBBkL5yYQCZo6wfbv932UNA4ThdP8

ADA: addr1q926ys75jp5wn2pv32a3t8r8pdhr7w02v0t9j4a8pmg0ruww5rlkctu4lnz2hfcwa5qfn3zhsd0s23r22uqwzx9gu6cq5c4e76

TON: UQC4qlAOD9Nly4K_66GJ_yCsSM3x2sB0vZ2GrBQbc--gZUui

DOGE: DTjNYmbtymzcjUiV4MsZY8MP4dM7MJ6qLC

XMR: 44qRqM6YtnxXUhkgCFqDDrKMPjWriu69FLBoop8Kwp7e1VQsBUJoVQ8JYQjfMV5C6uidTUgSSyoJ65mq8aYG2esZ1rrqfwt
