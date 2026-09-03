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


# --------------------------------------------------------------------------
# Formal protocol: metadata that is a FACT, not something to invent
# --------------------------------------------------------------------------
# A recorder names its file after the meeting: '2026-08-17 15-33-43.mkv'. Left to
# itself the model invented the metadata, and measured over the owner's 14 real
# analyses it wrote "24.10.2023" in 10 of them, "Текущая дата (на основании
# транскрипции)" in one, and a differently-shaped protocol number every time. When
# the name carries the date, these three fields are computed here and the model is
# told them; when it does not, nothing is imposed and its own answer stands.
_PROTOCOL_FACT_LABELS = {
    "ru": {"header": "ТОЧНЫЕ МЕТАДАННЫЕ ВСТРЕЧИ (используй ИМЕННО их в полях "
                     "protocolNumber/date/time и в тексте протокола; не выдумывай "
                     "дату и не пиши «текущая дата»):",
           "number": "Номер протокола", "date": "Дата встречи",
           "time": "Время встречи"},
    "en": {"header": "EXACT MEETING METADATA (use EXACTLY these in the "
                     "protocolNumber/date/time fields and in the protocol text; do "
                     "not invent a date and never write \"current date\"):",
           "number": "Protocol number", "date": "Meeting date",
           "time": "Meeting time"},
}


def protocol_facts(video_name: str, duration: str = "",
                   language: str = "ru") -> dict:
    """Known protocol metadata from the file name, or ``{}`` when it has no date.

    ``duration`` is the meeting length as the app displays it ('12м 47с'); it turns
    the start time from the file name into a real interval. The date is rendered in
    the analysis output language (RU documents write 17.08.2026).
    """
    from .media import meeting_datetime_from_name, parse_duration_label, shift_clock
    date_iso, start = meeting_datetime_from_name(video_name)
    if not date_iso:
        return {}
    year, month, day = date_iso.split("-")
    facts = {
        "date": (f"{day}.{month}.{year}" if language == "ru" else date_iso),
        # Date + start time: unique per meeting, sortable, and nothing is invented.
        "protocolNumber": (f"{date_iso}-{start.replace(':', '')}" if start else date_iso),
    }
    if start:
        end = shift_clock(start, parse_duration_label(duration))
        facts["time"] = f"{start} – {end}" if end else start
    return facts


def protocol_facts_block(facts: dict, language: str = "ru") -> str:
    """The facts rendered for the prompt, so the protocol TEXT carries them too."""
    if not facts:
        return ""
    labels = _PROTOCOL_FACT_LABELS.get(language, _PROTOCOL_FACT_LABELS["en"])
    lines = [labels["header"]]
    for key, label in (("protocolNumber", "number"), ("date", "date"), ("time", "time")):
        if facts.get(key):
            lines.append(f"- {labels[label]}: {facts[key]}")
    return "\n".join(lines)


def apply_protocol_facts(parsed, facts: dict):
    """Overwrite the model's invented protocol metadata with the known facts.

    The prompt already states them, but a model that ignores its instructions must
    not be the reason a protocol is dated three years wrong: the parsed fields are
    the authority the exports read, so they are set here regardless.
    """
    if not facts or not isinstance(parsed, dict):
        return parsed
    for key, value in facts.items():
        if value:
            parsed[key] = value
    return parsed


def feature_prompt(feature: str, language: str = "ru",
                   facts_block: str = "") -> str:
    """The system prompt for a feature, with the inline ``{transcript}``
    placeholder stripped (the transcript is supplied separately as the user
    text-file, the way ai_client.py already feeds the summary pass).

    ``facts_block`` (formal protocol only) is inserted just before the trailing
    "Transcript:" label — last thing the model reads before the transcript."""
    lang = language if language in ADVANCED_PROMPTS else "en"
    table = ADVANCED_PROMPTS.get(lang, ADVANCED_PROMPTS["en"])
    template = table.get(feature) or ADVANCED_PROMPTS["en"].get(feature, "")
    prompt = template.replace(PLACEHOLDER, "").rstrip()
    if not facts_block:
        return prompt
    for label in ("Транскрипция:", "Transcript:"):
        if prompt.endswith(label):
            return (prompt[: -len(label)].rstrip() + "\n\n" + facts_block
                    + "\n\n" + label)
    return prompt + "\n\n" + facts_block


def build_feature_command(feature: str, transcript_path, settings: dict, *,
                          provider="local", api_key="", endpoint="",
                          model="", advanced=None, timeout=0, no_think=False,
                          retries=0, retry_delay=0, chunk_chars=0, no_chunk=False,
                          agent_command="", agent_cwd="", facts=None,
                          python_exe=None, ai_client_script=None) -> list[str]:
    """argv for one feature pass through ai_client.py (system=prompt, user=transcript).

    A too-long transcript is chunked in ``uniform`` mode — the feature prompt runs on
    each part and the parts are combined (so a 3-4h meeting exceeding the context is
    still analysed). ``timeout``/``no_think``/``retries`` mirror the summary pass.

    ``facts`` (from :func:`protocol_facts`) states the meeting's real date/time to
    the formal-protocol pass; other features ignore it."""
    output_lang = resolve_output_language(settings)
    transcription_lang = settings.get("transcriptionLanguage", "ru")
    block = (protocol_facts_block(facts, output_lang)
             if feature == "formalProtocol" else "")
    prompt = feature_prompt(feature, output_lang, facts_block=block)
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
    """Cut a model's answer down to the JSON it contains.

    Both ends need trimming, and only one of them used to be. A model regularly
    introduces its answer — "Вот JSON массив с извлеченными задачами:" — and then
    opens a fence, so the ``^```json`` anchors never match and the leading prose
    stays. `json.loads` then fails on a response whose JSON is perfectly intact,
    and the user is told "AI returned invalid JSON/schema", which points at the
    model instead of at this function. Measured on one real meeting against a
    local Qwen: 2 of 8 runs of the same feature failed this way, and the discarded
    payload held nine correct action items.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if starts:
        cleaned = cleaned[min(starts):]
    last_brace = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if last_brace != -1:
        cleaned = cleaned[:last_brace + 1]
    return cleaned


def _drop_trailing_commas(text: str) -> str:
    for _ in range(20):
        text = re.sub(r",(\s*[\]}])", r"\1", text)
    return text


def _scan_json(text: str):
    """Walk *text* as JSON while tracking string state.

    Returns ``(stack, in_string, escaped, safe_points)``:

    * ``stack``     — the ``{``/``[`` still open when the text ran out;
    * ``in_string``/``escaped`` — whether it ended inside a string literal;
    * ``safe_points`` — ``(index, stack)`` pairs taken right after a nested
      value closed, i.e. the places a truncated document can be cut back to
      without losing an element that DID complete.

    Counting the delimiters with ``str.count`` instead (what this replaces)
    also counts the ones inside string values: a single unmatched ``[`` or
    ``{`` in a question's text — "п. [3", "формат { для отчёта" — made a
    perfectly valid response look unbalanced, so it never reached
    ``json.loads`` and the user was told "AI returned invalid JSON/schema".
    """
    stack: list[str] = []
    safe: list[tuple[int, list[str]]] = []
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
            if stack:                      # a nested value just completed
                safe.append((i + 1, list(stack)))
    return stack, in_string, escaped, safe


# A dangling key at the end of a truncated object — `..., "owner"` or
# `..., "owner":` — has no value to keep, so the whole pair goes away.
_DANGLING_KEY_TAIL = re.compile(r',\s*"[^"]*"\s*:?\s*$')
_DANGLING_KEY_ONLY = re.compile(r'(\{)\s*"[^"]*"\s*:?\s*$')


def parse_json_response(response: str):
    """Tolerant JSON extraction from a model's answer.

    Returns the parsed object/array, or ``None`` if it cannot be recovered.
    ``None`` is intentionally distinct from a valid empty list: otherwise a
    malformed response for a list-valued feature is silently reported as a
    successful "nothing found" result.

    Order matters: intact JSON is parsed as-is FIRST, and the repair pass runs
    only on what ``json.loads`` genuinely rejects. Repair is lossy — it drops
    the truncated tail — so it must never touch a response that was fine.
    """
    cleaned = _strip_fences_and_tail(response)
    cleaned = cleaned.replace('"assigneee":', '"assignee":')
    for candidate in (cleaned, _drop_trailing_commas(cleaned)):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
    return _fix_incomplete_json(cleaned, response)


def _fix_incomplete_json(cleaned: str, original_response: str):
    """Repair a truncated JSON blob: close what is open, drop what is partial.

    Candidates are tried longest-first — close the containers exactly where the
    text stopped, then fall back to cutting at each element that DID complete,
    newest first — so a response cut off mid-question keeps every question
    before it instead of being discarded whole. The closers are emitted from
    the scanner's stack in reverse order; the previous version appended every
    ``]`` before every ``}``, which cannot close an array of objects.
    """
    if not cleaned.strip():
        return None
    stack, in_string, escaped, safe = _scan_json(cleaned)

    tail = cleaned[:-1] if escaped else cleaned      # a lone trailing backslash
    if in_string:
        tail += '"'
    candidates = [(tail, stack)]
    candidates += [(cleaned[:cut], cut_stack) for cut, cut_stack in reversed(safe)]

    for body, open_stack in candidates:
        body = _drop_trailing_commas(body)
        body = _DANGLING_KEY_TAIL.sub("", body)
        body = _DANGLING_KEY_ONLY.sub(r"\1", body)
        body = _drop_trailing_commas(body)
        body += "".join("]" if ch == "[" else "}" for ch in reversed(open_stack))
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            continue
        # A response that clearly carried content must never be salvaged into an
        # empty result: "nothing found" and "nothing survived" are indistinguishable
        # to the caller, and the empty one would be stored as a valid green answer.
        if parsed in ([], {}) and ":" in cleaned:
            return None
        return parsed
    return None
