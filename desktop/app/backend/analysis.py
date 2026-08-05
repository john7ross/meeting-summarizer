"""Advanced meeting analysis: the 11-feature pass ported from the Electron
renderer (``ADVANCED_PROMPTS`` + ``performAdvancedAnalysis`` + ``parseJSONResponse``).

Unlike the Electron client — which calls an OpenAI-compatible endpoint directly
with one *user* message per feature (temperature 0.3 / max_tokens 2000) — this
port runs each feature through the same verified ``ai_client.py`` subprocess
used for the summary: system = the feature prompt with ``{transcript}`` stripped,
user = the transcript text-file. For the ``local`` provider that backend path
already uses temperature 0.7 / max_tokens 8000 (decided with the owner).

Each enabled feature is one AI pass. Results are parsed into JSON and assembled
into the analysis object the existing exporters/renderers expect::

    {
      "characteristics": {"keyTopics": [...]},
      "actionItems": [...], "sentiment": {...}|None, "category": {...}|None,
      "risks": [...], "quotes": [...], "technologies": [...], "questions": [...],
      "recommendations": [...], "followupQuestions": [...],
      "formalProtocol": {...}|None
    }

Feature gating mirrors the renderer exactly (settings keys in parentheses)::

    actionItems        : extractActionItems
    sentiment          : analyzeSentiment
    category           : categorizeAutomatically
    keyTopics          : categorizeAutomatically            -> characteristics.keyTopics
    risks              : extractActionItems OR categorizeAutomatically
    quotes             : analyzeSentiment   OR categorizeAutomatically
    technologies       : categorizeAutomatically
    questions          : extractActionItems OR categorizeAutomatically
    recommendations    : extractActionItems OR categorizeAutomatically
    followupQuestions  : generateFollowupQuestions
    formalProtocol     : generateFormalProtocol

If none of the five primary toggles (extractActionItems, analyzeSentiment,
categorizeAutomatically, generateFollowupQuestions, generateFormalProtocol) are
on, no analysis runs at all.
"""
from __future__ import annotations

import json
import re

from .summarization import build_command as _build_summary_command
from .summarization import resolve_output_language

PLACEHOLDER = "{transcript}"

# Order matches performAdvancedAnalysis in the renderer.
FEATURE_ORDER = [
    "actionItems", "sentiment", "category", "keyTopics", "risks", "quotes",
    "technologies", "questions", "recommendations", "followupQuestions",
    "formalProtocol",
]

# Features whose parsed JSON is a single object (everything else is a list).
OBJECT_FEATURES = {"sentiment", "category", "formalProtocol"}

ADVANCED_PROMPTS = {
    "en": {
        "actionItems": """Analyze the following meeting transcript and extract all action items, tasks, and decisions that require follow-up.

For each action item, provide:
1. The task description
2. Who is responsible (if mentioned)
3. Priority (high/medium/low)
4. Deadline (if mentioned)

Format as JSON array:
[
  {
    "task": "Task description",
    "assignee": "Person name or 'Unassigned'",
    "priority": "high|medium|low",
    "deadline": "Date or 'Not specified'"
  }
]

Transcript:
{transcript}""",

        "sentiment": """Analyze the sentiment and tone of the following meeting transcript.

Provide:
1. Overall sentiment (positive/neutral/negative)
2. Engagement level (high/medium/low)
3. Conflict indicators (yes/no)
4. Key emotions detected
5. Brief description
6. Interruption index (0-100, how often speakers interrupt each other)
7. Emotional balance ratio (0-100, balance between positive and negative emotions)
8. Empathy index (0-100, level of empathy and understanding shown)
9. Speech speed variability (low/medium/high, variation in speaking pace)
10. Questions to answers ratio (number, ratio of questions asked to answers given)
11. Dominance distribution (object with speaker percentages, who dominated the conversation)

Format as JSON:
{
  "overall": "positive|neutral|negative",
  "engagement": "high|medium|low",
  "hasConflict": true|false,
  "emotions": ["emotion1", "emotion2"],
  "description": "Brief description of the meeting atmosphere",
  "interruptionIndex": 0-100,
  "emotionalBalance": 0-100,
  "empathyIndex": 0-100,
  "speechSpeedVariability": "low|medium|high",
  "questionsToAnswersRatio": 0.0,
  "dominanceDistribution": {"Speaker_1": 45, "Speaker_2": 35, "Speaker_3": 20}
}

Transcript:
{transcript}""",

        "category": """Categorize the following meeting based on its content and purpose.

Possible categories:
- Planning/Strategy
- Retrospective/Review
- Brainstorming/Ideation
- Status Update/Standup
- Decision Making
- Problem Solving
- Training/Knowledge Sharing
- Client/Stakeholder Meeting
- Team Building
- Other

Also provide relevant tags and a brief explanation.

Format as JSON:
{
  "category": "Category name",
  "tags": ["tag1", "tag2", "tag3"],
  "description": "Brief explanation of why this category was chosen"
}

Transcript:
{transcript}""",

        "risks": """Analyze the following meeting transcript and identify all risks, blockers, and potential problems mentioned.

For each risk, provide:
1. Description of the risk or blocker
2. Severity (high/medium/low)
3. Impact area (technical/business/timeline/resource)
4. Current status (identified/in-progress/resolved)

Format as JSON array:
[
  {
    "description": "Risk description",
    "severity": "high|medium|low",
    "impact": "technical|business|timeline|resource",
    "status": "identified|in-progress|resolved"
  }
]

Transcript:
{transcript}""",

        "quotes": """Extract the most important and meaningful quotes from the following meeting transcript.

Select quotes that:
1. Represent key decisions or insights
2. Show important opinions or perspectives
3. Highlight critical information
4. Demonstrate team dynamics or culture

Format as JSON array (max 10 quotes):
[
  {
    "text": "The actual quote",
    "speaker": "Speaker name or identifier",
    "context": "Brief context of why this quote is important"
  }
]

Transcript:
{transcript}""",

        "technologies": """Identify all systems, technologies, tools, and platforms mentioned in the following meeting transcript.

For each technology, provide:
1. Name of the technology/system
2. Category (programming language/framework/tool/platform/service/database/other)
3. Context of how it was discussed (current use/planned/problem/alternative)

Format as JSON array:
[
  {
    "name": "Technology name",
    "category": "programming language|framework|tool|platform|service|database|other",
    "context": "current use|planned|problem|alternative"
  }
]

Transcript:
{transcript}""",

        "questions": """Identify all unresolved questions, open issues, and topics requiring further discussion from the following meeting transcript.

For each question, provide:
1. The question or open issue
2. Category (technical/business/process/resource)
3. Priority (high/medium/low)
4. Who needs to answer (if mentioned)

Format as JSON array:
[
  {
    "question": "The question or open issue",
    "category": "technical|business|process|resource",
    "priority": "high|medium|low",
    "owner": "Person name or 'Unassigned'"
  }
]

Transcript:
{transcript}""",

        "recommendations": """Based on the following meeting transcript, provide actionable recommendations for follow-up actions, improvements, and next steps.

For each recommendation, provide:
1. The recommendation
2. Category (process/communication/technical/planning)
3. Priority (high/medium/low)
4. Expected impact (high/medium/low)

Format as JSON array:
[
  {
    "recommendation": "The recommendation",
    "category": "process|communication|technical|planning",
    "priority": "high|medium|low",
    "impact": "high|medium|low"
  }
]

Transcript:
{transcript}""",

        "keyTopics": """Analyze the following meeting transcript and identify 5-8 key topics or themes that were discussed.

These should be high-level topics or subject areas, not just frequent words. For example:
- "Project timeline and milestones"
- "Budget allocation and resources"
- "Technical architecture decisions"
- "Team collaboration challenges"

Format as JSON array of strings:
["Topic 1", "Topic 2", "Topic 3", ...]

Transcript:
{transcript}""",

        "followupQuestions": """Based on the following meeting transcript, generate 5-7 follow-up questions for the next meeting.

These questions should:
1. Address unresolved issues or unclear points
2. Help track progress on action items
3. Explore topics that need deeper discussion
4. Clarify decisions that were made

Format as JSON array:
[
  {
    "question": "Question text",
    "category": "clarification|progress|deep-dive|decision-review",
    "priority": "high|medium|low",
    "context": "Brief context why this question is important"
  }
]

Transcript:
{transcript}""",

        "formalProtocol": """Generate a formal meeting protocol based on the following transcript, following GOST/ISO standards.

The protocol should include:
1. Meeting metadata (date, time, participants, location)
2. Agenda items discussed
3. Decisions made (numbered list)
4. Action items with assignees and deadlines
5. Next meeting date/time (if mentioned)
6. Signatures section

Use formal business language and structured format.

Format as JSON:
{
  "protocolNumber": "Auto-generated or 'Not specified'",
  "date": "Meeting date",
  "time": "Meeting time",
  "location": "Meeting location or 'Online'",
  "participants": ["Name 1", "Name 2"],
  "chairman": "Name or 'Not specified'",
  "secretary": "Name or 'Not specified'",
  "agenda": ["Item 1", "Item 2"],
  "decisions": [
    {
      "number": 1,
      "text": "Decision text",
      "votingResult": "Unanimous/Majority/Not specified"
    }
  ],
  "actionItems": [
    {
      "task": "Task description",
      "assignee": "Person name",
      "deadline": "Date or 'Not specified'"
    }
  ],
  "nextMeeting": "Date/time or 'Not scheduled'",
  "protocolText": "Full formatted protocol text in formal style"
}

Transcript:
{transcript}""",
    },
    "ru": {
        "actionItems": """Проанализируй следующую транскрипцию встречи и извлеки все задачи, действия и решения, требующие выполнения.

Для каждой задачи укажи:
1. Описание задачи
2. Ответственный (если упоминается)
3. Приоритет (высокий/средний/низкий)
4. Срок выполнения (если упоминается)

Формат JSON массив:
[
  {
    "task": "Описание задачи",
    "assignee": "Имя человека или 'Не назначен'",
    "priority": "high|medium|low",
    "deadline": "Дата или 'Не указан'"
  }
]

Транскрипция:
{transcript}""",

        "sentiment": """Проанализируй тональность и настроение следующей транскрипции встречи.

Укажи:
1. Общая тональность (позитивная/нейтральная/негативная)
2. Уровень вовлеченности (высокий/средний/низкий)
3. Индикаторы конфликта (да/нет)
4. Обнаруженные эмоции
5. Краткое описание
6. Индекс прерываний (0-100, как часто спикеры перебивают друг друга)
7. Баланс эмоций (0-100, баланс между позитивными и негативными эмоциями)
8. Индекс эмпатии (0-100, уровень проявленной эмпатии и понимания)
9. Вариативность скорости речи (низкая/средняя/высокая, изменение темпа речи)
10. Соотношение вопросов к ответам (число, соотношение заданных вопросов к данным ответам)
11. Распределение доминирования (объект с процентами спикеров, кто доминировал в разговоре)

Формат JSON:
{
  "overall": "positive|neutral|negative",
  "engagement": "high|medium|low",
  "hasConflict": true|false,
  "emotions": ["эмоция1", "эмоция2"],
  "description": "Краткое описание атмосферы встречи",
  "interruptionIndex": 0-100,
  "emotionalBalance": 0-100,
  "empathyIndex": 0-100,
  "speechSpeedVariability": "low|medium|high",
  "questionsToAnswersRatio": 0.0,
  "dominanceDistribution": {"Speaker_1": 45, "Speaker_2": 35, "Speaker_3": 20}
}

Транскрипция:
{transcript}""",

        "category": """Категоризируй следующую встречу на основе её содержания и цели.

Возможные категории:
- Планирование/Стратегия
- Ретроспектива/Обзор
- Мозговой штурм/Генерация идей
- Статус/Стендап
- Принятие решений
- Решение проблем
- Обучение/Обмен знаниями
- Встреча с клиентом/стейкхолдером
- Тимбилдинг
- Другое

Также укажи релевантные теги и краткое объяснение.

Формат JSON:
{
  "category": "Название категории",
  "tags": ["тег1", "тег2", "тег3"],
  "description": "Краткое объяснение почему выбрана эта категория"
}

Транскрипция:
{transcript}""",

        "risks": """Проанализируй следующую транскрипцию встречи и определи все риски, блокеры и потенциальные проблемы.

Для каждого риска укажи:
1. Описание риска или блокера
2. Серьезность (высокая/средняя/низкая)
3. Область влияния (техническая/бизнес/сроки/ресурсы)
4. Текущий статус (выявлен/в работе/решен)

Формат JSON массив:
[
  {
    "description": "Описание риска",
    "severity": "high|medium|low",
    "impact": "technical|business|timeline|resource",
    "status": "identified|in-progress|resolved"
  }
]

Транскрипция:
{transcript}""",

        "quotes": """Извлеки самые важные и значимые цитаты из следующей транскрипции встречи.

Выбери цитаты которые:
1. Представляют ключевые решения или инсайты
2. Показывают важные мнения или перспективы
3. Подчеркивают критическую информацию
4. Демонстрируют динамику команды или культуру

Формат JSON массив (максимум 10 цитат):
[
  {
    "text": "Сама цитата",
    "speaker": "Имя спикера или идентификатор",
    "context": "Краткий контекст почему эта цитата важна"
  }
]

Транскрипция:
{transcript}""",

        "technologies": """Определи все системы, технологии, инструменты и платформы упомянутые в следующей транскрипции встречи.

Для каждой технологии укажи:
1. Название технологии/системы
2. Категория (язык программирования/фреймворк/инструмент/платформа/сервис/база данных/другое)
3. Контекст обсуждения (текущее использование/планируется/проблема/альтернатива)

Формат JSON массив:
[
  {
    "name": "Название технологии",
    "category": "programming language|framework|tool|platform|service|database|other",
    "context": "current use|planned|problem|alternative"
  }
]

Транскрипция:
{transcript}""",

        "questions": """Определи все нерешенные вопросы, открытые проблемы и темы требующие дальнейшего обсуждения из следующей транскрипции встречи.

Для каждого вопроса укажи:
1. Вопрос или открытую проблему
2. Категория (техническая/бизнес/процесс/ресурсы)
3. Приоритет (высокий/средний/низкий)
4. Кто должен ответить (если упоминается)

Формат JSON массив:
[
  {
    "question": "Вопрос или открытая проблема",
    "category": "technical|business|process|resource",
    "priority": "high|medium|low",
    "owner": "Имя человека или 'Не назначен'"
  }
]

Транскрипция:
{transcript}""",

        "recommendations": """На основе следующей транскрипции встречи предоставь практические рекомендации для последующих действий, улучшений и следующих шагов.

Для каждой рекомендации укажи:
1. Рекомендацию
2. Категория (процесс/коммуникация/техническая/планирование)
3. Приоритет (высокий/средний/низкий)
4. Ожидаемое влияние (высокое/среднее/низкое)

Формат JSON массив:
[
  {
    "recommendation": "Рекомендация",
    "category": "process|communication|technical|planning",
    "priority": "high|medium|low",
    "impact": "high|medium|low"
  }
]

Транскрипция:
{transcript}""",

        "keyTopics": """Проанализируй следующую транскрипцию встречи и определи 5-8 ключевых тем или направлений которые обсуждались.

Это должны быть высокоуровневые темы или предметные области, а не просто частые слова. Например:
- "Сроки проекта и этапы"
- "Распределение бюджета и ресурсов"
- "Технические архитектурные решения"
- "Проблемы командной работы"

Формат JSON массив строк:
["Тема 1", "Тема 2", "Тема 3", ...]

Транскрипция:
{transcript}""",

        "followupQuestions": """На основе следующей транскрипции встречи сгенерируй 5-7 вопросов для следующей встречи.

Эти вопросы должны:
1. Затрагивать нерешенные вопросы или неясные моменты
2. Помогать отслеживать прогресс по задачам
3. Углубляться в темы, требующие дополнительного обсуждения
4. Уточнять принятые решения

Формат JSON массив:
[
  {
    "question": "Текст вопроса",
    "category": "clarification|progress|deep-dive|decision-review",
    "priority": "high|medium|low",
    "context": "Краткий контекст почему этот вопрос важен"
  }
]

Транскрипция:
{transcript}""",

        "formalProtocol": """Сгенерируй формальный протокол встречи на основе следующей транскрипции, следуя стандартам ГОСТ/ISO.

Протокол должен включать:
1. Метаданные встречи (дата, время, участники, место)
2. Обсуждаемые вопросы повестки
3. Принятые решения (нумерованный список)
4. Задачи с ответственными и сроками
5. Дата/время следующей встречи (если упоминается)
6. Раздел для подписей

Используй формальный деловой язык и структурированный формат.

Формат JSON:
{
  "protocolNumber": "Автогенерируемый или 'Не указан'",
  "date": "Дата встречи",
  "time": "Время встречи",
  "location": "Место встречи или 'Онлайн'",
  "participants": ["Имя 1", "Имя 2"],
  "chairman": "Имя или 'Не указан'",
  "secretary": "Имя или 'Не указан'",
  "agenda": ["Пункт 1", "Пункт 2"],
  "decisions": [
    {
      "number": 1,
      "text": "Текст решения",
      "votingResult": "Единогласно/Большинством/Не указано"
    }
  ],
  "actionItems": [
    {
      "task": "Описание задачи",
      "assignee": "Имя человека",
      "deadline": "Дата или 'Не указан'"
    }
  ],
  "nextMeeting": "Дата/время или 'Не запланирована'",
  "protocolText": "Полный текст протокола в формальном стиле"
}

Транскрипция:
{transcript}""",
    },
}


# --------------------------------------------------------------------------
# Feature gating (mirror of performAdvancedAnalysis)
# --------------------------------------------------------------------------
def enabled_features(settings: dict) -> list[str]:
    """Return enabled analysis features in renderer order, or [] if none.

    Gating reproduces performAdvancedAnalysis exactly, using the real
    settings.json keys (extractActionItems, analyzeSentiment,
    categorizeAutomatically, generateFollowupQuestions, generateFormalProtocol).
    """
    action = bool(settings.get("extractActionItems"))
    sentiment = bool(settings.get("analyzeSentiment"))
    category = bool(settings.get("categorizeAutomatically"))
    followup = bool(settings.get("generateFollowupQuestions"))
    protocol = bool(settings.get("generateFormalProtocol"))

    if not (action or sentiment or category or followup or protocol):
        return []

    gates = {
        "actionItems": action,
        "sentiment": sentiment,
        "category": category,
        "keyTopics": category,
        "risks": action or category,
        "quotes": sentiment or category,
        "technologies": category,
        "questions": action or category,
        "recommendations": action or category,
        "followupQuestions": followup,
        "formalProtocol": protocol,
    }
    return [f for f in FEATURE_ORDER if gates.get(f)]


def feature_prompt(feature: str, language: str = "ru") -> str:
    """The system prompt for a feature, with the inline ``{transcript}``
    placeholder stripped (the transcript is supplied separately as the user
    text-file, the way ai_client.py already feeds the summary pass)."""
    lang = language if language in ADVANCED_PROMPTS else "en"
    table = ADVANCED_PROMPTS.get(lang, ADVANCED_PROMPTS["en"])
    template = table.get(feature) or ADVANCED_PROMPTS["en"].get(feature, "")
    return template.replace(PLACEHOLDER, "").rstrip()


def build_feature_command(feature: str, transcript_path, settings: dict, *,
                          provider="local", api_key="", endpoint="",
                          model="", advanced=None, timeout=0, no_think=False,
                          retries=0, retry_delay=0, chunk_chars=0, no_chunk=False,
                          agent_command="", agent_cwd="",
                          python_exe=None, ai_client_script=None) -> list[str]:
    """argv for one feature pass through ai_client.py (system=prompt, user=transcript).

    A too-long transcript is chunked in ``uniform`` mode — the feature prompt runs on
    each part and the parts are combined (so a 3-4h meeting exceeding the context is
    still analysed). ``timeout``/``no_think``/``retries`` mirror the summary pass."""
    output_lang = resolve_output_language(settings)
    transcription_lang = settings.get("transcriptionLanguage", "ru")
    prompt = feature_prompt(feature, output_lang)
    return _build_summary_command(
        prompt, transcript_path, provider=provider, api_key=api_key,
        endpoint=endpoint, model=model, advanced=advanced,
        timeout=timeout, no_think=no_think, chunk_mode="uniform",
        retries=retries, retry_delay=retry_delay, chunk_chars=chunk_chars, no_chunk=no_chunk,
        output_language=output_lang, transcription_language=transcription_lang,
        agent_command=agent_command, agent_cwd=agent_cwd,
        python_exe=python_exe, ai_client_script=ai_client_script)


# --------------------------------------------------------------------------
# Result assembly
# --------------------------------------------------------------------------
def empty_results() -> dict:
    """The analysis object skeleton, matching what the renderer persists."""
    return {
        "characteristics": {},
        "actionItems": [],
        "sentiment": None,
        "category": None,
        "risks": [],
        "quotes": [],
        "technologies": [],
        "questions": [],
        "recommendations": [],
        "followupQuestions": [],
        "formalProtocol": None,
    }


def store_feature_result(results: dict, feature: str, parsed) -> None:
    """Place a parsed feature result into the analysis object by feature.

    keyTopics is nested under ``characteristics``; object features keep a dict
    (or None); list features keep a list (or [])."""
    if feature == "keyTopics":
        results.setdefault("characteristics", {})["keyTopics"] = (
            parsed if isinstance(parsed, list) else [])
    elif feature in OBJECT_FEATURES:
        results[feature] = parsed if isinstance(parsed, dict) else None
    else:
        results[feature] = parsed if isinstance(parsed, list) else []


def is_valid_feature_result(feature: str, parsed) -> bool:
    """Return whether the model response has the schema required by *feature*.

    An empty list is a valid "nothing found" answer for list features. Object
    features must be objects; accepting the parser's ``[]`` fallback used to
    turn malformed provider output into a green timeline entry plus ``null``.
    """
    if feature in OBJECT_FEATURES:
        return isinstance(parsed, dict)
    return isinstance(parsed, list)


# --------------------------------------------------------------------------
# JSON extraction (port of parseJSONResponse + fixIncompleteJSON)
# --------------------------------------------------------------------------
def _strip_fences_and_tail(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    last_brace = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if last_brace != -1:
        cleaned = cleaned[:last_brace + 1]
    return cleaned


def _drop_trailing_commas(text: str) -> str:
    for _ in range(20):
        text = re.sub(r",(\s*[\]}])", r"\1", text)
    return text


def parse_json_response(response: str):
    """Port of the renderer's parseJSONResponse: tolerant JSON extraction.

    Returns the parsed object/array, or ``None`` if it cannot be recovered.
    ``None`` is intentionally distinct from a valid empty list: otherwise a
    malformed response for a list-valued feature is silently reported as a
    successful "nothing found" result.
    """
    cleaned = _strip_fences_and_tail(response)
    cleaned = _drop_trailing_commas(cleaned)
    cleaned = cleaned.replace('"assigneee":', '"assignee":')

    open_braces = cleaned.count("{")
    close_braces = cleaned.count("}")
    open_brackets = cleaned.count("[")
    close_brackets = cleaned.count("]")
    if open_braces != close_braces or open_brackets != close_brackets:
        return _fix_incomplete_json(cleaned, response)

    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        return _fix_incomplete_json(cleaned, response)


def _fix_incomplete_json(cleaned: str, original_response: str):
    """Port of fixIncompleteJSON: aggressively repair a truncated JSON blob."""
    try:
        fixed = _strip_fences_and_tail(original_response)
        fixed = _drop_trailing_commas(fixed)
        fixed = fixed.replace('"assigneee":', '"assignee":')

        # Drop an incomplete trailing element (open object past last close/comma).
        last_comma = fixed.rfind(",")
        last_open_brace = fixed.rfind("{")
        last_close_brace = fixed.rfind("}")
        if last_open_brace > last_close_brace and last_open_brace > last_comma:
            if last_comma > 0:
                fixed = fixed[:last_comma]
            else:
                array_start = fixed.rfind("[")
                if array_start != -1:
                    fixed = fixed[:array_start + 1]

        # Close an unterminated string.
        if fixed.count('"') % 2 != 0:
            fixed += '"'

        # Balance brackets/braces.
        fixed += "]" * max(0, fixed.count("[") - fixed.count("]"))
        fixed += "}" * max(0, fixed.count("{") - fixed.count("}"))

        fixed = _drop_trailing_commas(fixed)
        return json.loads(fixed)
    except (ValueError, TypeError):
        return None
