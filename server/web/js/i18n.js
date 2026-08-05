/**
 * Translations for Meeting Summarizer Web UI
 */

const translations = {
    en: {
        // Auth page
        'auth.title': 'Meeting Summarizer',
        'auth.subtitle': 'AI-powered meeting analysis',
        'auth.login': 'Login',
        'auth.register': 'Register',
        'auth.username': 'Username',
        'auth.email': 'Email',
        'auth.password': 'Password',
        'auth.confirmPassword': 'Confirm Password',
        'auth.loginButton': 'Login',
        'auth.registerButton': 'Register',
        'auth.noAccount': "Don't have an account?",
        'auth.hasAccount': 'Already have an account?',
        'auth.passwordMismatch': 'Passwords do not match',
        'auth.invalidCredentials': 'Wrong username or password',
        'errors.noSpeech': 'No speech was recognised - the transcript is empty. Was the recording silent, or does it contain no speech?',
        'errors.silentAudio': 'No speech recognised: the file has no sound. There is an audio track, but it is empty (peak {0} dBFS) - check that the right input was selected when recording.',
        'errors.usernameTaken': 'That username is already taken.',
        'errors.emailTaken': 'That email is already registered.',
        'errors.localModelUnreachable': 'Could not reach the local model at {0}. Check that it is running and that the port matches the Local endpoint setting.',
        'errors.noTranscriptFile': 'Transcription produced no transcript file.',
        'errors.emptyFile': 'The file is empty - there is nothing to download.',
        'errors.noVault': 'No Obsidian vault is configured. Set the vault path in Settings.',
        'errors.unreadableMedia': 'Could not read the recording - the file is damaged or its format is not supported.',
        'errors.vaultMissing': 'The configured Obsidian vault does not exist on the server: {0}',
        'settings.providerLocal': 'Local model (llama.cpp / LM Studio / Ollama / any OpenAI-compatible)',
        'settings.providerAgent': 'Local agent CLI (Claude Code / Codex / Hermes / custom)',
        'settings.agentCommand': 'Agent command template',
        'settings.agentCwd': 'Agent working directory',
        'settings.hfToken': 'HuggingFace token (pyannote)',
        'settings.gsheetsToken': 'Google Sheets shared secret',
        'settings.useSpeakerPrompt': 'Use the speaker-aware prompt when diarisation is on',
        'settings.useSpeakerPromptHint': 'Every built-in template has a speaker-aware variant that asks the model to attribute what each participant said. It only helps when diarisation produced speaker labels.',
        'settings.projectId': 'Default project ID',
        'settings.projectIdPh': 'groups meetings for contextual memory and RAG',
        'settings.selectAll': 'All',
        'settings.selectNone': 'None',
        'settings.ragEmbeddingBackend': 'Embeddings backend',
        'settings.ragEmbeddingModel': 'Embeddings model',
        'settings.obsidian': 'Obsidian',
        'settings.obsidianEnabled': 'Enable Obsidian export',
        'settings.obsidianVault': 'Vault path (on the server)',
        'settings.obsidianHint': 'Notes are written by the server into this directory, in the same format the desktop app produces. Point it at a vault the server can reach - a local folder, a mounted share, or a synced directory.',
        'settings.obsidianIndex': 'Update the meeting index note',
        'settings.obsidianDataview': 'Add Dataview queries',
        'settings.obsidianPeople': 'Create People notes',
        'settings.obsidianTopics': 'Create Topic notes',
        'settings.obsidianMarkdown': 'Also write a plain Markdown copy',
        'modal.obsidianTip': 'Write the selected version into the Obsidian vault',
        'modal.obsidianDone': 'Written to the vault:',
        'modal.obsidianNothing': 'Nothing was written - there is no content of that kind yet.',
        'modal.obsidianFailed': 'Obsidian export failed',
        'admin.title': 'Administration',
        'admin.hint': 'These settings and downloads affect EVERY user of this installation.',
        'admin.workers': 'Parallel workers (load management)',
        'admin.workersAuto': 'Auto (detect from hardware)',
        'admin.workersSaved': 'Applied and saved for the whole installation.',
        'admin.engines': 'Engines and models',
        'admin.installed': 'installed',
        'admin.missing': 'not installed',
        'admin.install': 'Install',
        'admin.update': 'Check for updates',
        'admin.download': 'Download',
        'admin.upToDate': 'Up to date',
        'admin.updateAvailable': 'An update is available',
        'admin.jobStarted': 'Started - progress is shown below.',
        'admin.failed': 'Action failed',
        'common.error': 'Error',

        // Dashboard header
        'dashboard.title': 'Meeting Summarizer',
        'dashboard.logout': 'Logout',

        // Upload section
        'upload.title': 'Upload Meeting',
        'upload.placeholder': 'Click to upload or drag and drop',
        'upload.hint': 'MP4, AVI, MOV, MKV, WebM, MP3, WAV, M4A',
        'upload.uploading': 'Uploading...',
        'upload.complete': 'Upload complete!',
        'upload.failed': 'Upload failed',
        'upload.urlPlaceholder': 'https://… (YouTube or a video link)',
        'upload.urlAdd': 'Add by URL',
        'upload.urlSubmitting': 'Queuing…',
        'upload.urlQueued': 'Queued — the video will be downloaded and processed.',
        'upload.urlFailed': 'Failed to add URL',
        'upload.recordStart': 'Record from microphone',
        'upload.recordStop': 'Stop and upload',
        'upload.recordPrefix': 'Recording',
        'upload.recordHint': 'The recording is uploaded as a meeting when you stop it.',
        'upload.recordDenied': 'Microphone access was denied. Allow it in the browser and try again.',
        'upload.recordNoDevice': 'No microphone was found on this machine.',
        'upload.recordInsecure': 'The browser only gives microphone access over HTTPS or on localhost. Open the cabinet at https://… or http://localhost:8000.',
        'upload.recordUnsupported': 'This browser cannot record audio in a format the server accepts.',
        'upload.recordTooShort': 'The recording was too short - nothing was uploaded.',
        'upload.recordFailed': 'Recording failed',
        'search.title': 'Search',
        'search.placeholder': 'What to look for in the transcripts',
        'search.modeText': 'By text',
        'search.modeRag': 'By meaning (knowledge base)',
        'search.regex': 'Regular expression',
        'search.run': 'Search',
        'search.searching': 'Searching…',
        'search.nothing': 'Nothing found.',
        'search.failed': 'Search failed',
        'search.hits': 'matches',
        'search.ragEmpty': 'The knowledge base is empty - add a meeting to it from its card first.',
        'stats.title': 'Statistics',
        'stats.refresh': 'Refresh',
        'stats.total': 'Meetings',
        'stats.withTx': 'With transcript',
        'stats.withSum': 'With summary',
        'stats.withAn': 'With analysis',
        'stats.words': 'Words in transcripts',
        'stats.byStatus': 'By status',
        'stats.byProject': 'By project',
        'stats.noProject': 'no project',
        'stats.failed': 'Could not load statistics',
        'trim.enable': 'Split the recording into separate meetings first',
        'trim.title': 'Choose segments to process',
        'trim.intro': 'Drag across the waveform to mark a meeting, then add it. Every segment is processed separately: its own transcript, summary and analysis.',
        'trim.add': 'Add segment',
        'trim.remove': 'Remove',
        'trim.cut': 'Cut and queue',
        'trim.whole': 'Process the whole file',
        'trim.none': 'Mark at least one segment, or process the whole file.',
        'trim.cutting': 'Cutting…',
        'trim.queued': 'Segments queued:',
        'trim.failed': 'Could not cut the segments',
        'trim.waveformFailed': 'Could not read the recording',
        'trim.selection': 'Selected',
        'meetings.cancel': 'Cancel',
        'meetings.process': 'Process',
        'meetings.clearFinished': 'Clear finished',
        'meetings.clearConfirm': 'Delete every recording that is not currently processing?',
        'meetings.clearSkipped': 'Kept {n} recording(s) still being processed.',
        'meetings.clearFailed': 'Could not clear the list',
        'meetings.processFailed': 'Could not start processing',
        'meetings.cancelFailed': 'Could not cancel',
        'modal.transcript': 'Transcript (editable)',
        'modal.saveTranscript': 'Save transcript',
        'modal.transcriptSaved': 'Saved. Regenerate to rebuild the summary and analysis from it.',
        'modal.transcriptFailed': 'Could not save the transcript',
        'modal.project': 'Project',
        'modal.projectPlaceholder': 'id for grouping and RAG',
        'modal.saveProject': 'Save',
        'modal.projectSaved': 'Project saved.',
        'modal.projectFailed': 'Could not save the project',
        'filters.cancelled': 'Cancelled',
        'status.cancelled': 'Cancelled',
        'status.extracting': 'Extracting audio',
        'status.downloading': 'Downloading video',
        'status.transcribing': 'Transcribing',
        'status.summarizing': 'Generating summary',
        'status.analyzing': 'Analyzing',
        'status.complete': 'Complete',
        'time.minute': 'm',
        'time.second': 's',

        // Filters
        'filters.status': 'Status:',
        'filters.all': 'All',
        'filters.uploaded': 'Uploaded',
        'filters.processing': 'Processing',
        'filters.completed': 'Completed',
        'filters.failed': 'Failed',
        'filters.refresh': 'Refresh',

        // Meetings list
        'meetings.title': 'Your Meetings',
        'meetings.loading': 'Loading meetings...',
        'meetings.noMeetings': 'No meetings found',
        'meetings.created': 'Created',
        'meetings.duration': 'Duration',
        'meetings.size': 'Size',

        // Meeting detail modal
        'modal.title': 'Meeting Details',
        'modal.information': 'Information',
        'modal.filename': 'Filename',
        'modal.status': 'Status',
        'modal.created': 'Created',
        'modal.duration': 'Duration',
        'modal.size': 'Size',
        'modal.processingTime': 'Processing time',
        'modal.error': 'Error',
        'modal.progress': 'Processing Progress',
        'modal.stages': 'Stages',
        'stage.processing': 'Processing',
        'stage.extractAudio': 'Audio extraction',
        'stage.transcribe': 'Transcription',
        'stage.diarization': 'Speaker separation',
        'stage.summarize': 'Summary',
        'stage.analysis': 'Analysis',
        'modal.waiting': 'Waiting for updates...',
        'modal.downloads': 'Downloads',
        'modal.downloadSource': 'Download source file',
        'modal.downloadTranscript': 'Download Transcript',
        'modal.downloadSummary': 'Download Summary',
        'modal.downloadAnalysis': 'Download Analysis',
        'modal.delete': 'Delete Meeting',
        'modal.deleteConfirm': 'Are you sure you want to delete this meeting?',
        'modal.downloadFailed': 'Download failed',
        'modal.deleteFailed': 'Delete failed',

        // Meeting results (completed)
        'modal.summary': 'Summary',
        'modal.analysis': 'Analysis',
        'modal.speakers': 'Speakers',
        'modal.exportAs': 'Export as',
        'modal.regenerate': 'Regenerate',
        'modal.regenerateConfirm': 'Re-run summary and analysis from the transcript as a new version?',
        'modal.regenerateFailed': 'Regenerate failed',
        'modal.addToRag': 'Add to knowledge base',
        'modal.addedToRag': 'Added to knowledge base',
        'modal.ragFailed': 'Failed to add to knowledge base',
        'modal.exportBySpeaker': 'Export by speaker (zip)',
        'modal.exportFailed': 'Export failed',
        'modal.newName': 'New name',
        'modal.saveNames': 'Save names',
        'modal.namesSaved': 'Speaker names saved',
        'modal.renameFailed': 'Rename failed',

        // Settings panel
        'settings.title': 'Settings',
        'settings.open': 'Settings',
        'settings.save': 'Save',
        'settings.saved': 'Settings saved',
        'settings.saveFailed': 'Failed to save settings',
        'settings.loadFailed': 'Failed to load settings',
        'settings.transcription': 'Transcription',
        'settings.engine': 'Engine',
        'settings.model': 'Model',
        'settings.device': 'Device',
        'settings.transcriptionLanguage': 'Transcription language',
        'settings.outputLanguage': 'Summary/analysis language',
        'settings.outputAuto': 'Auto (same as transcription)',
        'settings.diarization': 'Diarization',
        'settings.ai': 'AI (summary & analysis)',
        'settings.provider': 'Provider',
        'settings.analysisSource': 'Build analysis from',
        'settings.analysisTranscript': 'Full transcript (best quality)',
        'settings.analysisSummary': 'Summary (faster, less complete)',
        'settings.analysisSourceHint': 'The full transcript is the quality-first default. Summary mode is only a speed/cost trade-off.',
        'settings.ragStorage': 'RAG storage',
        'settings.ragCatalogMode': 'Catalog mode',
        'settings.ragIsolated': 'Isolated for this server account',
        'settings.ragShared': 'Shared by secret code',
        'settings.ragSharedKey': 'Shared catalog secret',
        'settings.ragGenerate': 'Generate new code',
        'settings.ragCopy': 'Copy',
        'settings.ragSharedHint': 'Enter the same secret in desktop and this account in the same installation. This does not sync different computers. Anyone who knows it can access the shared catalog.',
        'settings.ragBadKey': 'The shared RAG catalog secret is invalid.',
        'settings.aiModel': 'Model',
        'settings.apiKey': 'API key',
        'settings.endpoint': 'Local endpoint',
        'settings.timeout': 'Request timeout (s, 0 = default)',
        'settings.reasoning': 'Disable reasoning (faster)',
        'settings.chunking': 'Enable chunking (map-reduce)',
        'settings.chunkingWarning': '⚠ Chunking splits long transcripts into parts before summarizing. This can lose meeting-wide context and lower summary/analysis quality. Leave it OFF to always send the whole transcript (best quality — but the model must fit it in its context window). Large-context models (e.g. Qwen 262k) should keep it off.',
        'settings.chunkChars': 'Chunk threshold (chars, 0 = default)',
        'settings.advanced': 'Advanced',
        'settings.gpuHandoff': 'Free VRAM for transcription (stop local LLM)',
        'settings.contextualMemory': 'Contextual memory (inject prior same-project summaries)',
        'settings.gsheets': 'Google Sheets export',
        'settings.gsheetsUrl': 'Apps Script webhook URL',
        'settings.hint': 'Vocabulary / terms',
        'settings.hintPh': 'e.g. API, Kubernetes, project names',
        'settings.aiModelPh': 'provider default',
        'settings.promptSection': 'Prompt & templates',
        'settings.template': 'Template',
        'settings.saveTemplate': 'Save as template',
        'settings.updateTemplate': 'Update',
        'settings.deleteTemplate': 'Delete',
        'settings.deleteBuiltin': 'Built-in templates cannot be deleted.',
        'settings.editBuiltin': 'Built-in templates cannot be overwritten — use "Save as template".',
        'settings.reset': 'Reset to defaults',
        'settings.resetConfirm': 'Reset every setting to its default value?',
        'rag.open': 'Knowledge base',
        'rag.title': 'Knowledge base',
        'rag.project': 'Project (blank = all)',
        'rag.refresh': 'Refresh',
        'rag.loading': 'Loading…',
        'rag.documents': 'Documents',
        'rag.chunks': 'Chunks',
        'rag.chunksShort': 'chunks',
        'rag.delete': 'Remove',
        'rag.confirmDelete': 'Remove this document from the knowledge base?',
        'rag.empty': 'Nothing indexed yet. Open a finished meeting and press "Add to knowledge base".',
        'settings.templateNamePrompt': 'Template name:',
        'settings.prompt': 'AI prompt',
        'settings.analysisFeatures': 'Analysis features',
        'settings.actionItems': 'Extract action items and tasks',
        'settings.sentiment': 'Analyze sentiment and tone',
        'settings.categorize': 'Automatically categorize meeting type',
        'settings.followup': 'Generate follow-up questions',
        'settings.protocol': 'Generate formal protocol (GOST/ISO)',
        'settings.processing': 'AI processing (chunking, speed)',
        'settings.retries': 'Retries on local-model failure',
        'settings.retryDelay': 'Base delay between retries (s)',
        'settings.llamaPort': 'Local LLM port (for hand-off)',
        'settings.ytCookies': 'YouTube cookies (browser, for sign-in)',

        // WebSocket messages
        'ws.connected': 'Connected to meeting updates',
        'ws.processingStarted': 'Processing started',
        'ws.completed': 'Processing completed!',
        'ws.error': 'Error',

        // Pagination
        'pagination.page': 'Page',
        'pagination.total': 'total',
        'pagination.previous': 'Previous',
        'pagination.next': 'Next',

        // Settings
        'settings.language': 'Language',
        'settings.theme': 'Theme',
        'settings.light': 'Light',
        'settings.dark': 'Dark',

        // Queue
        'queue.status': 'Queue Status:',
        'queue.workers': 'Workers',
        'queue.changeFailed': 'Failed to change workers count',

        // Footer
        'footer.tagline': 'AI-powered meeting analysis',
        'footer.api': 'API',
        'footer.version': 'Version'
    },
    ru: {
        // Auth page
        'auth.title': 'Meeting Summarizer',
        'auth.subtitle': 'Анализ встреч с помощью ИИ',
        'auth.login': 'Вход',
        'auth.register': 'Регистрация',
        'auth.username': 'Имя пользователя',
        'auth.email': 'Email',
        'auth.password': 'Пароль',
        'auth.confirmPassword': 'Подтвердите пароль',
        'auth.loginButton': 'Войти',
        'auth.registerButton': 'Зарегистрироваться',
        'auth.noAccount': 'Нет аккаунта?',
        'auth.hasAccount': 'Уже есть аккаунт?',
        'auth.passwordMismatch': 'Пароли не совпадают',
        'auth.invalidCredentials': 'Неверное имя пользователя или пароль',
        'errors.noSpeech': 'Речь не распознана — транскрипт пустой. Возможно, запись без звука или в ней нет речи.',
        'errors.silentAudio': 'Речь не распознана: в файле нет звука. Аудиодорожка есть, но она пустая (пик {0} dBFS) — проверьте, что при записи был выбран нужный источник звука.',
        'errors.usernameTaken': 'Такое имя пользователя уже занято.',
        'errors.emailTaken': 'Этот email уже зарегистрирован.',
        'errors.localModelUnreachable': 'Не удалось подключиться к локальной модели по адресу {0}. Проверьте, что она запущена и что порт совпадает с настройкой «Локальный endpoint».',
        'errors.noTranscriptFile': 'Транскрибация не создала файл транскрипта.',
        'errors.emptyFile': 'Файл пустой — скачивать нечего.',
        'errors.noVault': 'Хранилище Obsidian не настроено. Укажите путь в настройках.',
        'errors.unreadableMedia': 'Не удалось прочитать запись — файл повреждён или формат не поддерживается.',
        'errors.vaultMissing': 'Указанное хранилище Obsidian не существует на сервере: {0}',
        'settings.providerLocal': 'Локальная модель (llama.cpp / LM Studio / Ollama / любой OpenAI-совместимый)',
        'settings.providerAgent': 'Локальный агент (Claude Code / Codex / Hermes / свой)',
        'settings.agentCommand': 'Шаблон команды агента',
        'settings.agentCwd': 'Рабочая папка агента',
        'settings.hfToken': 'Токен HuggingFace (pyannote)',
        'settings.gsheetsToken': 'Общий секрет Google Sheets',
        'settings.useSpeakerPrompt': 'Промпт с указанием спикеров, когда включена диаризация',
        'settings.useSpeakerPromptHint': 'У каждого встроенного шаблона есть вариант с указанием спикеров — он просит модель писать, кто что сказал. Помогает только если диаризация проставила метки спикеров.',
        'settings.projectId': 'ID проекта по умолчанию',
        'settings.projectIdPh': 'группирует встречи для контекстной памяти и RAG',
        'settings.selectAll': 'Все',
        'settings.selectNone': 'Снять',
        'settings.ragEmbeddingBackend': 'Бэкенд эмбеддингов',
        'settings.ragEmbeddingModel': 'Модель эмбеддингов',
        'settings.obsidian': 'Obsidian',
        'settings.obsidianEnabled': 'Включить экспорт в Obsidian',
        'settings.obsidianVault': 'Путь к хранилищу (на сервере)',
        'settings.obsidianHint': 'Заметки пишет сервер в эту папку, в том же формате, что и десктопное приложение. Укажите хранилище, доступное серверу — локальную папку, подключённую сетевую или синхронизируемую директорию.',
        'settings.obsidianIndex': 'Обновлять индексную заметку встреч',
        'settings.obsidianDataview': 'Добавлять запросы Dataview',
        'settings.obsidianPeople': 'Создавать заметки People',
        'settings.obsidianTopics': 'Создавать заметки Topics',
        'settings.obsidianMarkdown': 'Дополнительно писать обычный Markdown',
        'modal.obsidianTip': 'Записать выбранную версию в хранилище Obsidian',
        'modal.obsidianDone': 'Записано в хранилище:',
        'modal.obsidianNothing': 'Ничего не записано — содержимого этого вида пока нет.',
        'modal.obsidianFailed': 'Экспорт в Obsidian не удался',
        'admin.title': 'Администрирование',
        'admin.hint': 'Эти настройки и загрузки действуют на ВСЕХ пользователей установки.',
        'admin.workers': 'Параллельные воркеры (управление нагрузкой)',
        'admin.workersAuto': 'Авто (определить по железу)',
        'admin.workersSaved': 'Применено и сохранено для всей установки.',
        'admin.engines': 'Движки и модели',
        'admin.installed': 'установлен',
        'admin.missing': 'не установлен',
        'admin.install': 'Установить',
        'admin.update': 'Проверить обновления',
        'admin.download': 'Скачать',
        'admin.upToDate': 'Актуально',
        'admin.updateAvailable': 'Доступно обновление',
        'admin.jobStarted': 'Запущено — прогресс ниже.',
        'admin.failed': 'Действие не выполнено',
        'common.error': 'Ошибка',

        // Dashboard header
        'dashboard.title': 'Meeting Summarizer',
        'dashboard.logout': 'Выход',

        // Upload section
        'upload.title': 'Загрузить встречу',
        'upload.placeholder': 'Нажмите для загрузки или перетащите файл',
        'upload.hint': 'MP4, AVI, MOV, MKV, WebM, MP3, WAV, M4A',
        'upload.uploading': 'Загрузка...',
        'upload.complete': 'Загрузка завершена!',
        'upload.failed': 'Ошибка загрузки',
        'upload.urlPlaceholder': 'https://… (YouTube или ссылка на видео)',
        'upload.urlAdd': 'Добавить по ссылке',
        'upload.urlSubmitting': 'Добавляю…',
        'upload.urlQueued': 'В очереди — видео будет скачано и обработано.',
        'upload.urlFailed': 'Не удалось добавить ссылку',
        'upload.recordStart': 'Записать с микрофона',
        'upload.recordStop': 'Остановить и загрузить',
        'upload.recordPrefix': 'Запись',
        'upload.recordHint': 'После остановки запись загрузится как обычная встреча.',
        'upload.recordDenied': 'Доступ к микрофону запрещён. Разрешите его в браузере и попробуйте снова.',
        'upload.recordNoDevice': 'Микрофон на этой машине не найден.',
        'upload.recordInsecure': 'Браузер даёт доступ к микрофону только по HTTPS или на localhost. Откройте кабинет по https://… либо по http://localhost:8000.',
        'upload.recordUnsupported': 'Этот браузер не умеет записывать звук в формате, который принимает сервер.',
        'upload.recordTooShort': 'Запись слишком короткая - ничего не загружено.',
        'upload.recordFailed': 'Ошибка записи',
        'search.title': 'Поиск',
        'search.placeholder': 'Что искать в транскриптах',
        'search.modeText': 'По тексту',
        'search.modeRag': 'По смыслу (база знаний)',
        'search.regex': 'Регулярное выражение',
        'search.run': 'Искать',
        'search.searching': 'Ищу…',
        'search.nothing': 'Ничего не найдено.',
        'search.failed': 'Ошибка поиска',
        'search.hits': 'совпадений',
        'search.ragEmpty': 'База знаний пуста - сначала добавьте в неё встречу из её карточки.',
        'stats.title': 'Статистика',
        'stats.refresh': 'Обновить',
        'stats.total': 'Встреч',
        'stats.withTx': 'С транскриптом',
        'stats.withSum': 'С саммари',
        'stats.withAn': 'С анализом',
        'stats.words': 'Слов в транскриптах',
        'stats.byStatus': 'По статусу',
        'stats.byProject': 'По проекту',
        'stats.noProject': 'без проекта',
        'stats.failed': 'Не удалось загрузить статистику',
        'trim.enable': 'Сначала нарезать запись на отдельные встречи',
        'trim.title': 'Выберите фрагменты для обработки',
        'trim.intro': 'Протяните по волне, чтобы отметить встречу, и добавьте её. Каждый фрагмент обрабатывается отдельно: свой транскрипт, саммари и анализ.',
        'trim.add': 'Добавить фрагмент',
        'trim.remove': 'Убрать',
        'trim.cut': 'Нарезать и поставить в очередь',
        'trim.whole': 'Обработать файл целиком',
        'trim.none': 'Отметьте хотя бы один фрагмент — или обработайте файл целиком.',
        'trim.cutting': 'Режу…',
        'trim.queued': 'Фрагментов в очереди:',
        'trim.failed': 'Не удалось нарезать фрагменты',
        'trim.waveformFailed': 'Не удалось прочитать запись',
        'trim.selection': 'Выделено',
        'meetings.cancel': 'Отменить',
        'meetings.process': 'Обработать',
        'meetings.clearFinished': 'Очистить',
        'meetings.clearConfirm': 'Удалить все записи, кроме обрабатываемых сейчас?',
        'meetings.clearSkipped': 'Оставлено записей в обработке: {n}.',
        'meetings.clearFailed': 'Не удалось очистить список',
        'meetings.processFailed': 'Не удалось запустить обработку',
        'meetings.cancelFailed': 'Не удалось отменить',
        'modal.transcript': 'Транскрипт (можно править)',
        'modal.saveTranscript': 'Сохранить транскрипт',
        'modal.transcriptSaved': 'Сохранено. Нажмите «Перегенерировать», чтобы пересобрать саммари и анализ.',
        'modal.transcriptFailed': 'Не удалось сохранить транскрипт',
        'modal.project': 'Проект',
        'modal.projectPlaceholder': 'id для группировки и RAG',
        'modal.saveProject': 'Сохранить',
        'modal.projectSaved': 'Проект сохранён.',
        'modal.projectFailed': 'Не удалось сохранить проект',
        'filters.cancelled': 'Отменено',
        'status.cancelled': 'Отменено',
        'status.extracting': 'Извлечение аудио',
        'status.downloading': 'Загрузка видео',
        'status.transcribing': 'Транскрибация',
        'status.summarizing': 'Создание саммари',
        'status.analyzing': 'Анализ',
        'status.complete': 'Готово',
        'time.minute': 'м',
        'time.second': 'с',

        // Filters
        'filters.status': 'Статус:',
        'filters.all': 'Все',
        'filters.uploaded': 'Загружено',
        'filters.processing': 'Обработка',
        'filters.completed': 'Завершено',
        'filters.failed': 'Ошибка',
        'filters.refresh': 'Обновить',

        // Meetings list
        'meetings.title': 'Ваши встречи',
        'meetings.loading': 'Загрузка встреч...',
        'meetings.noMeetings': 'Встречи не найдены',
        'meetings.created': 'Создано',
        'meetings.duration': 'Длительность',
        'meetings.size': 'Размер',

        // Meeting detail modal
        'modal.title': 'Детали встречи',
        'modal.information': 'Информация',
        'modal.filename': 'Имя файла',
        'modal.status': 'Статус',
        'modal.created': 'Создано',
        'modal.duration': 'Длительность',
        'modal.size': 'Размер',
        'modal.processingTime': 'Время обработки',
        'modal.error': 'Ошибка',
        'modal.progress': 'Прогресс обработки',
        'modal.stages': 'Этапы',
        'stage.processing': 'Обработка',
        'stage.extractAudio': 'Извлечение аудио',
        'stage.transcribe': 'Транскрибация',
        'stage.diarization': 'Разделение по спикерам',
        'stage.summarize': 'Саммари',
        'stage.analysis': 'Анализ',
        'modal.waiting': 'Ожидание обновлений...',
        'modal.downloads': 'Скачать',
        'modal.downloadSource': 'Скачать исходник',
        'modal.downloadTranscript': 'Скачать транскрипт',
        'modal.downloadSummary': 'Скачать саммари',
        'modal.downloadAnalysis': 'Скачать анализ',
        'modal.delete': 'Удалить встречу',
        'modal.deleteConfirm': 'Вы уверены, что хотите удалить эту встречу?',
        'modal.downloadFailed': 'Ошибка скачивания',
        'modal.deleteFailed': 'Ошибка удаления',

        // Meeting results (completed)
        'modal.summary': 'Саммари',
        'modal.analysis': 'Анализ',
        'modal.speakers': 'Спикеры',
        'modal.exportAs': 'Экспорт',
        'modal.regenerate': 'Перегенерировать',
        'modal.regenerateConfirm': 'Заново построить саммари и анализ из транскрипта как новую версию?',
        'modal.regenerateFailed': 'Ошибка перегенерации',
        'modal.addToRag': 'Добавить в базу знаний',
        'modal.addedToRag': 'Добавлено в базу знаний',
        'modal.ragFailed': 'Не удалось добавить в базу знаний',
        'modal.exportBySpeaker': 'Экспорт по спикерам (zip)',
        'modal.exportFailed': 'Ошибка экспорта',
        'modal.newName': 'Новое имя',
        'modal.saveNames': 'Сохранить имена',
        'modal.namesSaved': 'Имена спикеров сохранены',
        'modal.renameFailed': 'Ошибка переименования',

        // Settings panel
        'settings.title': 'Настройки',
        'settings.open': 'Настройки',
        'settings.save': 'Сохранить',
        'settings.saved': 'Настройки сохранены',
        'settings.saveFailed': 'Не удалось сохранить настройки',
        'settings.loadFailed': 'Не удалось загрузить настройки',
        'settings.transcription': 'Транскрибация',
        'settings.engine': 'Движок',
        'settings.model': 'Модель',
        'settings.device': 'Устройство',
        'settings.transcriptionLanguage': 'Язык транскрибации',
        'settings.outputLanguage': 'Язык саммари/анализа',
        'settings.outputAuto': 'Авто (как транскрипция)',
        'settings.diarization': 'Диаризация',
        'settings.ai': 'ИИ (саммари и анализ)',
        'settings.provider': 'Провайдер',
        'settings.analysisSource': 'Строить анализ по',
        'settings.analysisTranscript': 'Полному транскрипту (лучшее качество)',
        'settings.analysisSummary': 'Саммари (быстрее, менее полно)',
        'settings.analysisSourceHint': 'Полный транскрипт — режим по умолчанию для максимального качества. Анализ по саммари — только компромисс ради скорости и стоимости.',
        'settings.ragStorage': 'Хранилище RAG',
        'settings.ragCatalogMode': 'Режим каталога',
        'settings.ragIsolated': 'Изолированный для этого server-аккаунта',
        'settings.ragShared': 'Общий по секретному коду',
        'settings.ragSharedKey': 'Секретный код общего каталога',
        'settings.ragGenerate': 'Создать новый код',
        'settings.ragCopy': 'Копировать',
        'settings.ragSharedHint': 'Введите тот же секрет в desktop и этом аккаунте одной установки. Это не синхронизация между разными компьютерами. Любой, кто знает код, получает доступ к общей базе.',
        'settings.ragBadKey': 'Секретный код общего RAG-каталога некорректен.',
        'settings.aiModel': 'Модель',
        'settings.apiKey': 'API-ключ',
        'settings.endpoint': 'Локальный endpoint',
        'settings.timeout': 'Таймаут запроса (с, 0 = по умолчанию)',
        'settings.reasoning': 'Отключить reasoning (быстрее)',
        'settings.chunking': 'Включить чанкинг (map-reduce)',
        'settings.chunkingWarning': '⚠ Чанкинг режет длинный транскрипт на части перед саммаризацией. Это может потерять контекст встречи целиком и снизить качество саммари/анализа. Оставьте ВЫКЛ, чтобы всегда отправлять транскрипт целиком (лучшее качество — но модель должна вместить его в контекст). Модели с большим контекстом (напр. Qwen 262k) держат его выключенным.',
        'settings.chunkChars': 'Порог чанкинга (символы, 0 = по умолчанию)',
        'settings.advanced': 'Дополнительно',
        'settings.gpuHandoff': 'Освобождать VRAM под транскрибацию (останавливать локальную LLM)',
        'settings.contextualMemory': 'Контекстная память (подмешивать прошлые саммари того же проекта)',
        'settings.gsheets': 'Экспорт в Google Sheets',
        'settings.gsheetsUrl': 'URL вебхука Apps Script',
        'settings.hint': 'Словарь / термины',
        'settings.hintPh': 'напр.: API, Kubernetes, названия проектов',
        'settings.aiModelPh': 'по умолчанию для провайдера',
        'settings.promptSection': 'Промпт и шаблоны',
        'settings.template': 'Шаблон',
        'settings.saveTemplate': 'Сохранить как шаблон',
        'settings.updateTemplate': 'Обновить',
        'settings.deleteTemplate': 'Удалить',
        'settings.deleteBuiltin': 'Встроенные шаблоны удалить нельзя.',
        'settings.editBuiltin': 'Встроенный шаблон нельзя перезаписать — используйте «Сохранить как шаблон».',
        'settings.reset': 'Сбросить настройки',
        'settings.resetConfirm': 'Вернуть все настройки к значениям по умолчанию?',
        'rag.open': 'База знаний',
        'rag.title': 'База знаний',
        'rag.project': 'Проект (пусто = все)',
        'rag.refresh': 'Обновить',
        'rag.loading': 'Загрузка…',
        'rag.documents': 'Документов',
        'rag.chunks': 'Фрагментов',
        'rag.chunksShort': 'фрагм.',
        'rag.delete': 'Удалить',
        'rag.confirmDelete': 'Удалить этот документ из базы знаний?',
        'rag.empty': 'Пока ничего не проиндексировано. Откройте завершённую встречу и нажмите «Добавить в базу знаний».',
        'settings.templateNamePrompt': 'Название шаблона:',
        'settings.prompt': 'Промпт AI',
        'settings.analysisFeatures': 'Функции анализа',
        'settings.actionItems': 'Извлекать задачи и действия',
        'settings.sentiment': 'Анализ тональности и настроения',
        'settings.categorize': 'Автокатегоризация встречи',
        'settings.followup': 'Вопросы к следующей встрече',
        'settings.protocol': 'Формальный протокол (ГОСТ/ISO)',
        'settings.processing': 'Обработка AI (чанкинг, скорость)',
        'settings.retries': 'Повторы при обрыве локальной модели',
        'settings.retryDelay': 'Базовая пауза между повторами (с)',
        'settings.llamaPort': 'Порт локальной LLM (для handoff)',
        'settings.ytCookies': 'Cookies YouTube (браузер, для входа)',

        // WebSocket messages
        'ws.connected': 'Подключено к обновлениям встречи',
        'ws.processingStarted': 'Обработка начата',
        'ws.completed': 'Обработка завершена!',
        'ws.error': 'Ошибка',

        // Pagination
        'pagination.page': 'Страница',
        'pagination.total': 'всего',
        'pagination.previous': 'Назад',
        'pagination.next': 'Вперед',

        // Settings
        'settings.language': 'Язык',
        'settings.theme': 'Тема',
        'settings.light': 'Светлая',
        'settings.dark': 'Темная',

        // Queue
        'queue.status': 'Статус очереди:',
        'queue.workers': 'Воркеры',
        'queue.changeFailed': 'Не удалось изменить число потоков',

        // Footer
        'footer.tagline': 'Анализ встреч с помощью ИИ',
        'footer.api': 'API',
        'footer.version': 'Версия'
    }
};

// i18n manager
// Server message -> translation key. Order matters: first match wins.
const SERVER_MESSAGES = [
    // The backend tags an empty transcription with the CAUSE. Both must come
    // before the generic noSpeech rule below, which they would otherwise match.
    [/SILENT_AUDIO:.*peak (-?[\d.]+) dBFS/i, 'errors.silentAudio'],
    [/NO_SPEECH:/i, 'errors.noSpeech'],
    [/No speech recognised/i, 'errors.noSpeech'],
    [/Username already registered/i, 'errors.usernameTaken'],
    [/Email already registered/i, 'errors.emailTaken'],
    [/Cannot connect to local API at (\S+)/i, 'errors.localModelUnreachable'],
    [/Transcription produced no transcript file/i, 'errors.noTranscriptFile'],
    [/is empty - there is nothing to download/i, 'errors.emptyFile'],
    [/No Obsidian vault is configured/i, 'errors.noVault'],
    [/Could not read the recording/i, 'errors.unreadableMedia'],
    [/Obsidian vault does not exist on the server: (.+)$/i, 'errors.vaultMissing'],
];


class I18n {
    constructor() {
        this.currentLang = localStorage.getItem('language') || 'en';
    }

    setLanguage(lang) {
        this.currentLang = lang;
        localStorage.setItem('language', lang);
        this.updatePage();
    }

    t(key) {
        return translations[this.currentLang][key] || key;
    }

    /**
     * Translate a message that came FROM THE SERVER.
     *
     * API messages are English (one API, many clients), so rendering `detail` or
     * `error_message` raw put English sentences into the Russian cabinet - "No
     * speech recognised…", "Username already registered". The server strings below
     * are treated as stable identifiers: a self-test asserts the server still emits
     * them, so changing one breaks a test instead of silently un-translating the UI.
     * `{0}` in a translation receives the first capture group, which keeps specifics
     * such as an endpoint URL.
     */
    serverMessage(text) {
        const raw = String(text || '');
        if (!raw) return raw;
        for (const [pattern, key] of SERVER_MESSAGES) {
            const m = raw.match(pattern);
            if (m) {
                const translated = this.t(key);
                return m[1] ? translated.replace('{0}', m[1]) : translated;
            }
        }
        return raw;
    }

    updatePage() {
        // Обновляем все элементы с data-i18n
        document.querySelectorAll('[data-i18n]').forEach(element => {
            const key = element.getAttribute('data-i18n');
            const translation = this.t(key);

            if (element.tagName === 'INPUT' && element.placeholder !== undefined) {
                element.placeholder = translation;
            } else {
                element.textContent = translation;
            }
        });

        // Обновляем title страницы
        if (document.querySelector('[data-i18n="auth.title"]')) {
            document.title = this.t('auth.title');
        } else if (document.querySelector('[data-i18n="dashboard.title"]')) {
            document.title = this.t('dashboard.title');
        }
    }

    getCurrentLanguage() {
        return this.currentLang;
    }
}

// Theme manager
class ThemeManager {
    constructor() {
        this.currentTheme = localStorage.getItem('theme') || 'light';
        this.applyTheme();
    }

    setTheme(theme) {
        this.currentTheme = theme;
        localStorage.setItem('theme', theme);
        this.applyTheme();
    }

    applyTheme() {
        // Set on both <html> and <body> so the root element (full viewport) is
        // covered and the dark: variant matches everywhere.
        document.documentElement.setAttribute('data-theme', this.currentTheme);
        document.body.setAttribute('data-theme', this.currentTheme);
    }

    toggleTheme() {
        const newTheme = this.currentTheme === 'light' ? 'dark' : 'light';
        this.setTheme(newTheme);
        return newTheme;
    }

    getCurrentTheme() {
        return this.currentTheme;
    }
}

// Глобальные экземпляры
window.i18n = new I18n();
window.themeManager = new ThemeManager();
