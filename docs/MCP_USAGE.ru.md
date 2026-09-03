# Архив встреч через MCP — руководство

[English](MCP_USAGE.md) · **Русский**

`backend/mcp_server.py` открывает архив обработанных встреч любому агенту с поддержкой MCP,
чтобы агент сам находил нужные встречи, а не получал транскрипт «в руки».

Работает по **MCP через stdio**: JSON-RPC 2.0 построчно на stdin/stdout, SDK не требуется.
В stdout идёт только протокол — диагностика уходит в stderr.

## Подключение

Готовые к вставке команды для вашего клиента:

```
backend\python\python.exe backend\mcp_server.py --print-registration
```

Печатает вариант для командной строки и JSON-блок `mcpServers`. Общая форма — stdio-сервер,
запускаемый командой:

```json
{
  "mcpServers": {
    "meetings": {
      "command": "<путь к python>",
      "args": ["<путь к backend/mcp_server.py>"]
    }
  }
}
```

Где именно лежит этот конфиг — смотрите в документации своего клиента: сервер не завязан ни
на какой конкретный хост.

## Инструменты

| Инструмент | Аргументы | Что возвращает |
|---|---|---|
| `list_meetings` | `limit`, `project`, `only_with_summary` | Список (свежие первыми): id, имя, дата, проект, статус, число версий |
| `get_transcript` | `meeting_id` (обяз.), `max_chars` | Полный текст транскрипта |
| `get_summary` | `meeting_id` (обяз.), `version` | Текст саммари (последняя версия, если `version` не задан) |
| `get_analysis` | `meeting_id` (обяз.), `version`, `feature` | JSON анализа целиком или одна фича |
| `search_transcripts` | `query` (обяз.), `limit`, `context` | Буквальные совпадения без учёта регистра, с цитатами |
| `search_knowledge` | `query` (обяз.), `project`, `top_k` | Семантические совпадения из всех локальных RAG-каталогов с указанием источника |

Значения `meeting_id` берутся из `list_meetings`. Фичи анализа: `actionItems`, `sentiment`,
`category`, `keyTopics`, `risks`, `quotes`, `technologies`, `questions`, `recommendations`,
`followupQuestions`, `formalProtocol`.

## Типичные сценарии

**Найти, что решили по теме.** `search_transcripts` (или `search_knowledge`, если
формулировки могут отличаться) → взять `meeting_id` → `get_summary` для итога либо
`get_analysis` с `feature: "actionItems"` для задач.

**Подготовиться к регулярной встрече.** `list_meetings` с фильтром `project` →
`get_summary` последней → `get_analysis` с `feature: "followupQuestions"`.

**Свести обязательства по нескольким встречам.** `list_meetings` с
`only_with_summary: true`, затем по каждой `get_analysis` с `feature: "actionItems"`.

Предпочитайте саммари и анализ транскриптам: транскрипт может быть в десятки тысяч слов —
берите его, только когда важна точная формулировка, и ограничивайте через `max_chars`.

## Ошибки

Инструмент, который не может ответить, возвращает ошибку MCP (`isError`) с понятным текстом:
`Meeting 999 not found`, `Meeting 222 has no summary`, `Unknown feature 'nope'; available:
[...]`. Неизвестное имя инструмента или метода — ошибки JSON-RPC (`-32602` / `-32601`).
Сервер при этом не падает и продолжает отвечать.

## Замечания

- Только чтение. Сервер никогда не меняет встречи, настройки и артефакты.
- Для `search_knowledge` хотя бы одна база должна быть наполнена из desktop или web.
  Инструмент обнаруживает `rag_knowledge_base`, `rag_data/u*`, `rag_shared/*` и прежний
  корневой `rag_data`, ищет по всем совместимым базам, глобально дедуплицирует результаты и
  возвращает `catalog`/`catalog_kind`. Несовместимые embedding-модели не обрывают поиск:
  причины перечисляются в `catalogs_skipped`.
- MCP имеет доступ ко всем локальным каталогам этой установки и поэтому должен запускаться
  только доверенным пользователем. Выбранный embedding-провайдер берётся из метаданных
  каждого каталога; облачный каталог может требовать сеть и API-ключ.
- Архив читается вживую из `config/history.json` и файлов артефактов — встреча, обработанная
  при подключённом агенте, появится при следующем `list_meetings`.
