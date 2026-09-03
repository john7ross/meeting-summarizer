/**
 * Dashboard page logic
 */

let currentPage = 0;
const pageSize = 10;
let currentFilter = '';

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
}

// Locale date for values that may be absent or unparseable - RAG metadata
// carries raw ISO strings, and `new Date(undefined)` renders "Invalid Date".
function fmtDate(value) {
    if (!value) return '—';
    const d = new Date(value);
    return isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

document.addEventListener('DOMContentLoaded', async () => {
    // Применяем переводы
    i18n.updatePage();

    // Настройка переключателей
    const langToggle = document.getElementById('langToggle');
    const themeToggle = document.getElementById('themeToggle');
    const langIcon = document.getElementById('langIcon');
    const themeIcon = document.getElementById('themeIcon');

    langIcon.textContent = i18n.getCurrentLanguage().toUpperCase();
    themeIcon.textContent = themeManager.getCurrentTheme() === 'light' ? '🌙' : '☀️';

    langToggle.addEventListener('click', () => {
        const newLang = i18n.getCurrentLanguage() === 'en' ? 'ru' : 'en';
        i18n.setLanguage(newLang);
        langIcon.textContent = newLang.toUpperCase();
        // Messages rendered from JS carry their key so a language switch can
        // re-render them; setLanguage() only walks [data-i18n] markup.
        ['urlMsg', 'recordMsg'].forEach(id => {
            const el = document.getElementById(id);
            if (el && el.dataset.i18nKey) el.textContent = i18n.t(el.dataset.i18nKey);
        });
        // Перезагружаем встречи с новыми переводами
        loadMeetings();
    });

    themeToggle.addEventListener('click', () => {
        const newTheme = themeManager.toggleTheme();
        themeIcon.textContent = newTheme === 'light' ? '🌙' : '☀️';
    });

    // Проверка авторизации
    if (!api.isAuthenticated()) {
        window.location.href = '/';
        return;
    }

    // Загрузка информации о пользователе
    try {
        const user = await api.getCurrentUser();
        document.getElementById('userInfo').textContent = `${user.username} (${user.role})`;

        // Загружаем статус очереди если админ
        if (user.role === 'admin') {
            loadQueueStatus();
            // Обновляем каждые 5 секунд
            setInterval(loadQueueStatus, 5000);
            // Administration: installation-wide settings and the shared model/engine
            // resources. Hidden for everyone else - not merely 403-guarded, so a
            // regular user is never shown a control they cannot use.
            const adminBtn = document.getElementById('adminBtn');
            adminBtn.style.display = '';
            adminBtn.addEventListener('click', openAdmin);
            document.getElementById('closeAdmin').addEventListener('click', () => {
                document.getElementById('adminModal').style.display = 'none';
            });
        }
    } catch (error) {
        console.error('Failed to load user info:', error);
    }

    // Logout
    document.getElementById('logoutBtn').addEventListener('click', async () => {
        await api.logout();
        window.location.href = '/';
    });

    // Settings
    document.getElementById('settingsBtn').addEventListener('click', openSettings);
    document.getElementById('closeSettings').addEventListener('click', () => {
        document.getElementById('settingsModal').style.display = 'none';
    });

    // Knowledge base (RAG library + stats + per-document delete)
    document.getElementById('ragBtn').addEventListener('click', openRag);
    document.getElementById('ragClose').addEventListener('click', () => {
        document.getElementById('ragModal').style.display = 'none';
    });
    document.getElementById('ragRefresh').addEventListener('click', loadRag);
    document.getElementById('ragProject').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') loadRag();
    });

    // Upload
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');

    uploadArea.addEventListener('click', () => {
        fileInput.click();
    });

    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.style.borderColor = '#667eea';
    });

    uploadArea.addEventListener('dragleave', () => {
        uploadArea.style.borderColor = '#ddd';
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.style.borderColor = '#ddd';
        const file = e.dataTransfer.files[0];
        if (file) {
            handleFileUpload(file);
        }
    });

    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            handleFileUpload(file);
        }
    });

    // Add by URL (YouTube / file server)
    const urlAddBtn = document.getElementById('urlAddBtn');
    urlAddBtn.addEventListener('click', async () => {
        const input = document.getElementById('urlInput');
        const msg = document.getElementById('urlMsg');
        const url = input.value.trim();
        if (!url) return;
        urlAddBtn.disabled = true;
        msg.textContent = i18n.t('upload.urlSubmitting');
        msg.dataset.i18nKey = 'upload.urlSubmitting';
        msg.className = 'mt-2 text-sm text-slate-500 dark:text-slate-400';
        try {
            await api.uploadFromUrl(url);
            input.value = '';
            msg.textContent = i18n.t('upload.urlQueued');
            msg.dataset.i18nKey = 'upload.urlQueued';
            msg.className = 'mt-2 text-sm text-emerald-600 dark:text-emerald-400';
            loadMeetings();
        } catch (e) {
            msg.textContent = i18n.t('upload.urlFailed') + ': ' + i18n.serverMessage(e.message);
            delete msg.dataset.i18nKey;
            msg.className = 'mt-2 text-sm text-red-500';
        } finally {
            urlAddBtn.disabled = false;
        }
    });

    // Record from the microphone (the desktop client has a recorder; the cabinet
    // used to accept files and URLs only, so a browser user had no way to capture
    // a meeting at all).
    const recordBtn = document.getElementById('recordBtn');
    if (recordBtn) {
        recordBtn.addEventListener('click', toggleRecording);
    }

    // Trim window: waveform selection -> segments -> one job per segment.
    const trimCanvas = document.getElementById('trimCanvas');
    if (trimCanvas) {
        trimCanvas.addEventListener('pointerdown', (e) => {
            trimState.dragging = true;
            trimState.selStart = trimState.selEnd = trimSecondsAt(e);
            trimCanvas.setPointerCapture(e.pointerId);
            updateTrimSelectionLabel();
            drawWaveform();
        });
        trimCanvas.addEventListener('pointermove', (e) => {
            if (!trimState.dragging) return;
            trimState.selEnd = trimSecondsAt(e);
            updateTrimSelectionLabel();
            drawWaveform();
        });
        trimCanvas.addEventListener('pointerup', () => { trimState.dragging = false; });
        document.getElementById('trimAdd').addEventListener('click', addTrimSegment);
        document.getElementById('trimCut').addEventListener('click', () => cutTrimSegments(false));
        document.getElementById('trimWhole').addEventListener('click', () => cutTrimSegments(true));
        document.getElementById('trimClose').addEventListener('click', closeTrim);
        window.addEventListener('resize', () => {
            if (document.getElementById('trimModal').style.display !== 'none') drawWaveform();
        });
    }

    // Archive statistics - the desktop has a Statistics dialog; the cabinet
    // reported nothing at all.
    const statsRefresh = document.getElementById('statsRefresh');
    if (statsRefresh) {
        statsRefresh.addEventListener('click', loadStats);
        loadStats();
    }

    // Search across the archive. Both endpoints (literal and semantic) already
    // existed and were tested; the cabinet simply never exposed them, so a
    // browser user could not search their meetings at all.
    const searchBtn = document.getElementById('searchBtn');
    if (searchBtn) {
        searchBtn.addEventListener('click', runSearch);
        document.getElementById('searchInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') runSearch();
        });
        document.getElementById('searchMode').addEventListener('change', () => {
            // The regex switch only means anything for the literal search.
            const isText = document.getElementById('searchMode').value === 'text';
            document.getElementById('searchRegex').closest('label').style.display =
                isText ? '' : 'none';
        });
    }

    // Filters
    document.getElementById('statusFilter').addEventListener('change', (e) => {
        currentFilter = e.target.value;
        currentPage = 0;
        loadMeetings();
    });

    document.getElementById('refreshBtn').addEventListener('click', () => {
        loadMeetings();
    });

    // Bulk clear: deleting recordings one by one was the only option here, while
    // the desktop has had a "clear the queue" action. A run in progress is kept.
    document.getElementById('clearFinishedBtn').addEventListener('click', async () => {
        if (!confirm(i18n.t('meetings.clearConfirm'))) return;
        try {
            const r = await api.clearFinishedMeetings();
            if (r.skipped) alert(i18n.t('meetings.clearSkipped').replace('{n}', r.skipped));
            loadMeetings();
            loadStats();
        } catch (e) {
            alert(i18n.t('meetings.clearFailed') + ': ' + i18n.serverMessage(e.message));
        }
    });

    // Pagination
    document.getElementById('prevPage').addEventListener('click', () => {
        if (currentPage > 0) {
            currentPage--;
            loadMeetings();
        }
    });

    document.getElementById('nextPage').addEventListener('click', () => {
        currentPage++;
        loadMeetings();
    });

    // Modal
    document.getElementById('closeModal').addEventListener('click', () => {
        document.getElementById('meetingModal').style.display = 'none';
    });

    // Загрузка встреч
    await loadMeetings();
});

async function loadQueueStatus() {
    try {
        const status = await api.getQueueStatus();

        // Показываем панель статуса
        const queueStatusDiv = document.getElementById('queueStatus');
        queueStatusDiv.style.display = 'flex';

        // Обновляем значения
        document.getElementById('queueSize').textContent = status.queue_size;
        document.getElementById('processingCount').textContent = status.processing_count;
        document.getElementById('workersCount').textContent = status.active_workers;
        document.getElementById('maxWorkers').textContent = status.max_workers;

        // Устанавливаем текущее значение в select
        const workersSelect = document.getElementById('workersSelect');
        workersSelect.value = status.max_workers;

        // Добавляем обработчик изменения если еще не добавлен
        if (!workersSelect.dataset.listenerAdded) {
            workersSelect.addEventListener('change', async (e) => {
                const count = parseInt(e.target.value);
                try {
                    await api.setWorkersCount(count);
                    await loadQueueStatus();
                } catch (error) {
                    alert(i18n.t('queue.changeFailed') + ': ' + i18n.serverMessage(error.message));
                }
            });
            workersSelect.dataset.listenerAdded = 'true';
        }
    } catch (error) {
        console.error('Failed to load queue status:', error);
    }
}

async function loadTranscriptEditor(meetingId) {
    const box = document.getElementById('transcriptEditor');
    if (!box) return;
    try {
        const data = await api.getTranscript(meetingId);
        box.value = data.text || '';
    } catch (e) {
        box.value = '';
        transcriptMessage(i18n.t('modal.transcriptFailed') + ': ' + i18n.serverMessage(e.message), true);
    }
}

function transcriptMessage(text, isError) {
    const el = document.getElementById('transcriptMsg');
    if (!el) return;
    el.textContent = text;
    el.className = 'mt-1 text-xs ' + (isError ? 'text-red-500'
                                              : 'text-emerald-600 dark:text-emerald-400');
}

async function saveTranscript(meetingId) {
    const box = document.getElementById('transcriptEditor');
    if (!box) return;
    try {
        await api.saveTranscript(meetingId, box.value);
        transcriptMessage(i18n.t('modal.transcriptSaved'), false);
    } catch (e) {
        transcriptMessage(i18n.t('modal.transcriptFailed') + ': ' + i18n.serverMessage(e.message), true);
    }
}

async function saveProject(meetingId) {
    const input = document.getElementById('meetingProject');
    if (!input) return;
    try {
        await api.updateMeeting(meetingId, { project: input.value.trim() });
        transcriptMessage(i18n.t('modal.projectSaved'), false);
        loadMeetings();
        loadStats();
    } catch (e) {
        transcriptMessage(i18n.t('modal.projectFailed') + ': ' + i18n.serverMessage(e.message), true);
    }
}

async function cancelMeeting(meetingId) {
    // Stopping a run is the user's decision; the desktop asks nothing either.
    try {
        await api.cancelMeeting(meetingId);
        loadMeetings();
    } catch (e) {
        alert(i18n.t('meetings.cancelFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

const STAGE_LABELS = {
    video_processing: 'stage.processing', extract_audio: 'stage.extractAudio',
    transcribe: 'stage.transcribe', diarization: 'stage.diarization',
    summarize: 'stage.summarize', analysis: 'stage.analysis',
};

async function loadStageTimeline(meetingId) {
    try {
        const data = await api.meetingTrace(meetingId);
        const spans = (data && data.spans) || [];
        if (!spans.length) return;
        const list = document.getElementById('stageList');
        const section = document.getElementById('stageSection');
        if (!list || !section) return;
        list.innerHTML = spans.map((s) => {
            const key = STAGE_LABELS[s.name];
            const label = key ? (i18n.t(key) || s.name) : s.name;
            // A stage that finished instantly still has a duration: 0 is a
            // measurement, not a missing value, so only null/undefined is blank.
            const time = (s.duration === null || s.duration === undefined)
                ? '' : `${(s.duration / 1000).toFixed(1)}${i18n.t('time.second')}`;
            return `<div class="flex justify-between gap-4 border-b border-slate-100 py-1 last:border-0 dark:border-slate-800/60">
                <span class="text-slate-600 dark:text-slate-300">${escapeHtml(label)}</span>
                <span class="text-slate-500 dark:text-slate-400">${time}</span></div>`;
        }).join('');
        section.style.display = '';
    } catch (e) { /* no trace for this meeting */ }
}

async function processMeeting(meetingId) {
    // A recording uploaded with "trim first" is deliberately NOT queued. Closing
    // the trim window used to leave it stranded: the card offered only Cancel, so
    // the only way to process it was to upload the file again.
    try {
        await api.processMeeting(meetingId);
        loadMeetings();
    } catch (e) {
        alert(i18n.t('meetings.processFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

// ============================================================================
// Trim - split one recording into per-meeting segments
// ============================================================================

let trimState = { meetingId: null, duration: 0, peaks: [], segments: [],
                  selStart: null, selEnd: null, dragging: false };

function trimTime(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    const two = n => String(n).padStart(2, '0');
    return h ? `${h}:${two(m)}:${two(s % 60)}` : `${m}:${two(s % 60)}`;
}

async function openTrim(meetingId) {
    trimState = { meetingId, duration: 0, peaks: [], segments: [],
                  selStart: null, selEnd: null, dragging: false };
    document.getElementById('trimModal').style.display = 'flex';
    const msg = document.getElementById('trimMsg');
    msg.textContent = '';
    msg.className = 'mt-2 text-sm';
    updateTrimSelectionLabel();
    renderTrimList();
    drawWaveform();
    try {
        const w = await api.meetingWaveform(meetingId);
        trimState.duration = w.duration || 0;
        trimState.peaks = w.peaks || [];
        drawWaveform();
    } catch (e) {
        msg.textContent = i18n.t('trim.waveformFailed') + ': ' + i18n.serverMessage(e.message);
        msg.className = 'mt-2 text-sm text-red-500';
    }
}

function closeTrim() {
    document.getElementById('trimModal').style.display = 'none';
}

function drawWaveform() {
    const canvas = document.getElementById('trimCanvas');
    if (!canvas) return;
    // Match the backing store to the CSS size, or the waveform is blurry and the
    // pointer maths is off by the device pixel ratio.
    const width = canvas.clientWidth || 800;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = 150 * ratio;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, 150);

    const dark = document.documentElement.classList.contains('dark');
    const mid = 75;
    const peaks = trimState.peaks;
    ctx.strokeStyle = dark ? '#64748b' : '#94a3b8';
    ctx.lineWidth = 1;
    if (peaks.length) {
        for (let x = 0; x < width; x++) {
            const v = peaks[Math.floor(x / width * peaks.length)] || 0;
            const h = Math.max(1, v * 68);
            ctx.beginPath();
            ctx.moveTo(x + 0.5, mid - h);
            ctx.lineTo(x + 0.5, mid + h);
            ctx.stroke();
        }
    }
    const toX = s => trimState.duration ? (s / trimState.duration) * width : 0;
    ctx.fillStyle = dark ? 'rgba(56,189,248,0.28)' : 'rgba(37,99,235,0.20)';
    trimState.segments.forEach(seg => {
        ctx.fillRect(toX(seg.start), 0, Math.max(2, toX(seg.end) - toX(seg.start)), 150);
    });
    if (trimState.selStart !== null && trimState.selEnd !== null) {
        const a = Math.min(trimState.selStart, trimState.selEnd);
        const b = Math.max(trimState.selStart, trimState.selEnd);
        ctx.fillStyle = dark ? 'rgba(248,113,113,0.30)' : 'rgba(220,38,38,0.22)';
        ctx.fillRect(toX(a), 0, Math.max(2, toX(b) - toX(a)), 150);
    }
}

function trimSecondsAt(event) {
    const canvas = document.getElementById('trimCanvas');
    const rect = canvas.getBoundingClientRect();
    const x = Math.min(Math.max(0, event.clientX - rect.left), rect.width);
    return trimState.duration ? (x / rect.width) * trimState.duration : 0;
}

function updateTrimSelectionLabel() {
    const el = document.getElementById('trimSelection');
    if (!el) return;
    if (trimState.selStart === null || trimState.selEnd === null) {
        el.textContent = '—';
        return;
    }
    const a = Math.min(trimState.selStart, trimState.selEnd);
    const b = Math.max(trimState.selStart, trimState.selEnd);
    el.textContent = `${i18n.t('trim.selection')}: ${trimTime(a)} – ${trimTime(b)}`;
}

function renderTrimList() {
    const box = document.getElementById('trimList');
    if (!box) return;
    box.innerHTML = trimState.segments.map((seg, i) => `
        <div class="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-700">
            <span>${i + 1}. ${trimTime(seg.start)} – ${trimTime(seg.end)}</span>
            <button class="btn-secondary" onclick="removeTrimSegment(${i})">${i18n.t('trim.remove')}</button>
        </div>`).join('');
    document.getElementById('trimCut').textContent =
        `${i18n.t('trim.cut')} (${trimState.segments.length})`;
}

function removeTrimSegment(index) {
    trimState.segments.splice(index, 1);
    renderTrimList();
    drawWaveform();
}

function addTrimSegment() {
    if (trimState.selStart === null || trimState.selEnd === null) return;
    const a = Math.min(trimState.selStart, trimState.selEnd);
    const b = Math.max(trimState.selStart, trimState.selEnd);
    if (b - a < 1) return;   // a stray click is not a meeting
    trimState.segments.push({ start: +a.toFixed(2), end: +b.toFixed(2) });
    trimState.segments.sort((x, y) => x.start - y.start);
    trimState.selStart = trimState.selEnd = null;
    updateTrimSelectionLabel();
    renderTrimList();
    drawWaveform();
}

async function cutTrimSegments(whole) {
    const msg = document.getElementById('trimMsg');
    if (whole) {
        // "Process the whole file" is the desktop's escape hatch: the upload was
        // held back from the queue, so put the original in as one meeting.
        try {
            await api.processMeeting(trimState.meetingId);
            closeTrim();
            loadMeetings();
        } catch (e) {
            msg.textContent = i18n.t('trim.failed') + ': ' + i18n.serverMessage(e.message);
            msg.className = 'mt-2 text-sm text-red-500';
        }
        return;
    }
    if (!trimState.segments.length) {
        msg.textContent = i18n.t('trim.none');
        msg.className = 'mt-2 text-sm text-red-500';
        return;
    }
    msg.textContent = i18n.t('trim.cutting');
    msg.className = 'mt-2 text-sm text-slate-500 dark:text-slate-400';
    try {
        const res = await api.cutSegments(trimState.meetingId, trimState.segments);
        msg.textContent = `${i18n.t('trim.queued')} ${res.created.length}`;
        msg.className = 'mt-2 text-sm text-emerald-600 dark:text-emerald-400';
        setTimeout(() => { closeTrim(); loadMeetings(); loadStats(); }, 900);
    } catch (e) {
        msg.textContent = i18n.t('trim.failed') + ': ' + i18n.serverMessage(e.message);
        msg.className = 'mt-2 text-sm text-red-500';
    }
}

// ============================================================================
// Statistics
// ============================================================================

// ── Knowledge base ──────────────────────────────────────────────────────────
// Mirrors the desktop RAG dialog's Library and Stats tabs. Search already lives
// in the top search bar (mode = "rag"), so it is not duplicated here.
function openRag() {
    document.getElementById('ragModal').style.display = 'flex';
    loadRag();
}

async function loadRag() {
    const statsBox = document.getElementById('ragStatsBox');
    const libBox = document.getElementById('ragLibraryBox');
    const msg = document.getElementById('ragMsg');
    const project = document.getElementById('ragProject').value.trim();
    msg.textContent = '';
    libBox.innerHTML = '';
    statsBox.textContent = i18n.t('rag.loading');

    try {
        const s = await api.ragStats();
        statsBox.innerHTML =
            `${i18n.t('rag.documents')}: <b>${s.documents ?? 0}</b> · ` +
            `${i18n.t('rag.chunks')}: <b>${s.chunks ?? 0}</b>` +
            (s.model ? ` · ${escapeHtml(s.provider || '')} ${escapeHtml(s.model)}` : '');
    } catch (e) {
        statsBox.textContent = i18n.serverMessage(e.message);
    }

    try {
        const lib = await api.ragLibrary(project);
        const docs = lib.documents || [];
        if (!docs.length) {
            libBox.innerHTML =
                `<p class="text-sm text-slate-500 dark:text-slate-400">${i18n.t('rag.empty')}</p>`;
            return;
        }
        libBox.innerHTML = docs.map((d) => `
            <div class="flex items-center justify-between gap-3 rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                <div class="min-w-0">
                    <div class="truncate text-sm font-medium">${escapeHtml(d.title || d.doc_id)}</div>
                    <div class="text-xs text-slate-500 dark:text-slate-400">
                        ${escapeHtml(d.project || '—')} · ${escapeHtml(fmtDate(d.added_at || d.date))} ·
                        ${d.chunks ?? 0} ${i18n.t('rag.chunksShort')}
                    </div>
                </div>
                <button class="btn-ghost text-red-600" data-rag-del="${escapeHtml(d.doc_id)}"
                        data-i18n="rag.delete">${i18n.t('rag.delete')}</button>
            </div>`).join('');
        libBox.querySelectorAll('[data-rag-del]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                if (!confirm(i18n.t('rag.confirmDelete'))) return;
                try {
                    await api.ragDelete(btn.dataset.ragDel);
                    loadRag();
                } catch (e) {
                    msg.textContent = i18n.serverMessage(e.message);
                }
            });
        });
    } catch (e) {
        msg.textContent = i18n.serverMessage(e.message);
    }
}

async function loadStats() {
    const box = document.getElementById('statsBody');
    if (!box) return;
    try {
        const s = await api.meetingStats();
        const tile = (labelKey, value) => `
            <div class="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                <div class="text-xs text-slate-500 dark:text-slate-400">${i18n.t(labelKey)}</div>
                <div class="mt-1 text-xl font-semibold">${value}</div>
            </div>`;
        const breakdown = (labelKey, map, emptyKey) => {
            const rows = Object.entries(map || {});
            if (!rows.length) return '';
            return `
            <div class="col-span-2 rounded-lg border border-slate-200 p-3 dark:border-slate-700 sm:col-span-3 lg:col-span-5">
                <div class="text-xs text-slate-500 dark:text-slate-400">${i18n.t(labelKey)}</div>
                <div class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
                    ${rows.map(([k, v]) => `<span>${escapeHtml(k || i18n.t(emptyKey))}: <b>${v}</b></span>`).join('')}
                </div>
            </div>`;
        };
        box.innerHTML =
            tile('stats.total', s.total) +
            tile('stats.withTx', s.with_tx) +
            tile('stats.withSum', s.with_sum) +
            tile('stats.withAn', s.with_an) +
            tile('stats.words', (s.words || 0).toLocaleString()) +
            breakdown('stats.byStatus', s.by_status, 'stats.noProject') +
            breakdown('stats.byProject', s.by_project, 'stats.noProject');
    } catch (e) {
        box.innerHTML = `<p class="col-span-full text-sm text-red-500">${i18n.t('stats.failed')}: ${escapeHtml(i18n.serverMessage(e.message))}</p>`;
    }
}

// ============================================================================
// Search
// ============================================================================

async function runSearch() {
    const input = document.getElementById('searchInput');
    const box = document.getElementById('searchResults');
    const mode = document.getElementById('searchMode').value;
    const q = input.value.trim();
    if (!q) { box.innerHTML = ''; return; }

    box.innerHTML = `<p class="text-sm text-slate-500 dark:text-slate-400">${i18n.t('search.searching')}</p>`;
    try {
        const data = mode === 'rag'
            ? await api.ragSearch(q, '', 10)
            : await api.textSearch(q, document.getElementById('searchRegex').checked, false);
        box.innerHTML = mode === 'rag' ? renderRagHits(data) : renderTextHits(data);
    } catch (e) {
        box.innerHTML = `<p class="text-sm text-red-500">${i18n.t('search.failed')}: ${escapeHtml(i18n.serverMessage(e.message))}</p>`;
    }
}

function renderTextHits(data) {
    const groups = (data && data.results) || [];
    if (!groups.length) {
        return `<p class="text-sm text-slate-500 dark:text-slate-400">${i18n.t('search.nothing')}</p>`;
    }
    return groups.map(g => `
        <div class="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
            <div class="mb-1 flex items-center justify-between gap-3">
                <button class="text-left text-sm font-medium text-brand-600 hover:underline dark:text-brand-400"
                        onclick="showMeetingDetail(${g.meeting_id})">${escapeHtml(g.filename || '')}</button>
                <span class="text-xs text-slate-400">${g.hit_count} ${i18n.t('search.hits')}</span>
            </div>
            ${g.hits.slice(0, 5).map(h => `
                <p class="mt-1 text-sm text-slate-600 dark:text-slate-300">
                    <span class="text-xs text-slate-400">${h.line_number}:</span>
                    ${escapeHtml(h.line)}
                </p>`).join('')}
        </div>`).join('');
}

function renderRagHits(data) {
    const hits = (data && data.results) || [];
    if (!hits.length) {
        const empty = data && data.count === 0 ? 'search.ragEmpty' : 'search.nothing';
        return `<p class="text-sm text-slate-500 dark:text-slate-400">${i18n.t(empty)}</p>`;
    }
    return hits.map(h => `
        <div class="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
            <div class="mb-1 flex items-center justify-between gap-3">
                <span class="text-sm font-medium">${escapeHtml(h.title || h.kind || '')}</span>
                <span class="text-xs text-slate-400">${(h.score ?? 0).toFixed(3)}</span>
            </div>
            <p class="text-sm text-slate-600 dark:text-slate-300">${escapeHtml((h.text || '').slice(0, 400))}</p>
        </div>`).join('');
}

// ============================================================================
// Microphone capture
// ============================================================================

let mediaRecorder = null;
let recordChunks = [];
let recordStream = null;
let recordTimer = null;
let recordStartedAt = 0;

// Only formats the upload endpoint accepts. Chromium records WebM/Opus, Safari
// records MP4/AAC - anything else (e.g. bare Ogg) would be rejected on arrival,
// so it is refused up front with a clear message instead.
const RECORD_TYPES = [
    { mime: 'audio/webm;codecs=opus', ext: '.webm' },
    { mime: 'audio/webm', ext: '.webm' },
    { mime: 'audio/mp4', ext: '.m4a' },
];

function pickRecordType() {
    if (typeof MediaRecorder === 'undefined') return null;
    for (const candidate of RECORD_TYPES) {
        if (!MediaRecorder.isTypeSupported || MediaRecorder.isTypeSupported(candidate.mime)) {
            return candidate;
        }
    }
    return null;
}

function recordMessage(key, isError) {
    const msg = document.getElementById('recordMsg');
    if (!msg) return;
    msg.textContent = key ? i18n.t(key) : '';
    if (key) { msg.dataset.i18nKey = key; } else { delete msg.dataset.i18nKey; }
    msg.className = 'mt-2 text-sm ' + (isError ? 'text-red-500'
                                               : 'text-slate-500 dark:text-slate-400');
}

function setRecordingUi(active) {
    const btn = document.getElementById('recordBtn');
    const indicator = document.getElementById('recordIndicator');
    if (btn) {
        btn.textContent = i18n.t(active ? 'upload.recordStop' : 'upload.recordStart');
        btn.dataset.i18n = active ? 'upload.recordStop' : 'upload.recordStart';
    }
    if (indicator) {
        indicator.classList.toggle('hidden', !active);
        indicator.classList.toggle('inline-flex', active);
    }
}

function tickRecordTimer() {
    const el = document.getElementById('recordElapsed');
    if (!el) return;
    const total = Math.floor((Date.now() - recordStartedAt) / 1000);
    const mm = Math.floor(total / 60);
    const ss = String(total % 60).padStart(2, '0');
    el.textContent = `${mm}:${ss}`;
}

function releaseRecordStream() {
    if (recordStream) {
        recordStream.getTracks().forEach(track => track.stop());
        recordStream = null;
    }
    if (recordTimer) {
        clearInterval(recordTimer);
        recordTimer = null;
    }
}

function recordFileName(ext) {
    const d = new Date();
    const two = n => String(n).padStart(2, '0');
    const stamp = `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}` +
                  ` ${two(d.getHours())}-${two(d.getMinutes())}-${two(d.getSeconds())}`;
    return `${i18n.t('upload.recordPrefix')} ${stamp}${ext}`;
}

async function toggleRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        return;
    }

    // getUserMedia only exists in a secure context: HTTPS, or http on localhost.
    // Over plain http on a LAN address the API is simply absent, which would
    // otherwise look like "the button does nothing".
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        recordMessage(window.isSecureContext === false
            ? 'upload.recordInsecure' : 'upload.recordUnsupported', true);
        return;
    }
    const type = pickRecordType();
    if (!type) {
        recordMessage('upload.recordUnsupported', true);
        return;
    }

    try {
        recordStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
        const missing = e && (e.name === 'NotFoundError' || e.name === 'OverconstrainedError');
        recordMessage(denied ? 'upload.recordDenied'
                             : (missing ? 'upload.recordNoDevice' : 'upload.recordFailed'), true);
        releaseRecordStream();
        return;
    }

    recordChunks = [];
    try {
        mediaRecorder = new MediaRecorder(recordStream, { mimeType: type.mime });
    } catch (e) {
        recordMessage('upload.recordUnsupported', true);
        releaseRecordStream();
        return;
    }

    mediaRecorder.addEventListener('dataavailable', (e) => {
        if (e.data && e.data.size) recordChunks.push(e.data);
    });
    mediaRecorder.addEventListener('error', () => {
        recordMessage('upload.recordFailed', true);
        setRecordingUi(false);
        releaseRecordStream();
    });
    mediaRecorder.addEventListener('stop', async () => {
        const seconds = (Date.now() - recordStartedAt) / 1000;
        releaseRecordStream();
        setRecordingUi(false);
        const blob = new Blob(recordChunks, { type: type.mime });
        recordChunks = [];
        // A sub-second blob is almost always a misclick and yields an empty
        // transcript, which the pipeline then reports as a failed meeting.
        if (seconds < 1 || blob.size === 0) {
            recordMessage('upload.recordTooShort', true);
            return;
        }
        recordMessage('', false);
        const file = new File([blob], recordFileName(type.ext), { type: type.mime });
        await handleFileUpload(file);
    });

    recordStartedAt = Date.now();
    mediaRecorder.start();
    setRecordingUi(true);
    recordMessage('', false);
    tickRecordTimer();
    recordTimer = setInterval(tickRecordTimer, 1000);
}

async function handleFileUpload(file) {
    // When the user asked to split the recording, the file is stored but NOT
    // queued: the trim window decides what actually gets processed.
    const trimFirst = !!(document.getElementById('trimBeforeProcessing') || {}).checked;
    const uploadArea = document.getElementById('uploadArea');
    const uploadProgress = document.getElementById('uploadProgress');
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');

    uploadArea.querySelector('.upload-placeholder').style.display = 'none';
    uploadProgress.style.display = 'block';

    try {
        const uploaded = await api.uploadMeeting(file, (percent) => {
            progressFill.style.width = percent + '%';
            progressText.textContent = `${i18n.t('upload.uploading')} ${Math.round(percent)}%`;
        }, !trimFirst);

        progressText.textContent = i18n.t('upload.complete');
        if (trimFirst && uploaded && uploaded.id) openTrim(uploaded.id);
        setTimeout(() => {
            uploadArea.querySelector('.upload-placeholder').style.display = 'block';
            uploadProgress.style.display = 'none';
            progressFill.style.width = '0%';
            loadMeetings();
            loadStats();
        }, 2000);
    } catch (error) {
        progressText.textContent = i18n.t('upload.failed') + ': ' + i18n.serverMessage(error.message);
        progressText.style.color = '#d32f2f';
        setTimeout(() => {
            uploadArea.querySelector('.upload-placeholder').style.display = 'block';
            uploadProgress.style.display = 'none';
            progressFill.style.width = '0%';
            progressText.style.color = '';
        }, 3000);
    }
}

async function loadMeetings() {
    const meetingsList = document.getElementById('meetingsList');
    meetingsList.innerHTML = `<div class="col-span-full py-10 text-center text-sm text-slate-400">${i18n.t('meetings.loading')}</div>`;

    try {
        const data = await api.listMeetings(
            currentPage * pageSize,
            pageSize,
            currentFilter || null
        );

        if (data.meetings.length === 0) {
            meetingsList.innerHTML = `<div class="col-span-full py-10 text-center text-sm text-slate-400">${i18n.t('meetings.noMeetings')}</div>`;
            document.getElementById('pagination').style.display = 'none';
            return;
        }

        meetingsList.innerHTML = '';
        data.meetings.forEach(meeting => {
            const card = createMeetingCard(meeting);
            meetingsList.appendChild(card);
        });
        startListPolling();

        // Pagination
        const pagination = document.getElementById('pagination');
        const pageInfo = document.getElementById('pageInfo');
        const prevBtn = document.getElementById('prevPage');
        const nextBtn = document.getElementById('nextPage');

        pagination.style.display = 'flex';
        pageInfo.textContent = `${i18n.t('pagination.page')} ${currentPage + 1} (${data.total} ${i18n.t('pagination.total')})`;
        prevBtn.disabled = currentPage === 0;
        nextBtn.disabled = (currentPage + 1) * pageSize >= data.total;

    } catch (error) {
        meetingsList.innerHTML = `<div class="col-span-full py-10 text-center text-sm text-red-500">${i18n.t('common.error')}: ${escapeHtml(i18n.serverMessage(error.message))}</div>`;
    }
}

function formatEta(sec) {
    if (sec == null || sec <= 0) return '';
    const m = Math.floor(sec / 60), s = sec % 60;
    const minute = i18n.t('time.minute');
    const second = i18n.t('time.second');
    return m > 0 ? `~${m}${minute} ${s}${second}` : `~${s}${second}`;
}

function formatStage(stage) {
    const key = String(stage || '');
    if (!key) return '';
    const translationKey = key.startsWith('status.') ? key : `status.${key}`;
    const translated = i18n.t(translationKey);
    return translated === translationKey ? key.replace('status.', '') : translated;
}

function createMeetingCard(meeting) {
    const card = document.createElement('div');
    card.className = 'card group cursor-pointer p-4 transition hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-md dark:hover:border-brand-500/50';
    card.dataset.id = meeting.id;
    card.dataset.status = meeting.status;
    card.onclick = () => showMeetingDetail(meeting.id);

    const date = new Date(meeting.created_at).toLocaleString();
    const statusText = i18n.t(`filters.${meeting.status}`) || meeting.status;
    const filename = escapeHtml(meeting.original_filename);
    const active = meeting.status === 'processing' || meeting.status === 'uploaded';

    card.innerHTML = `
        <div class="flex items-start justify-between gap-2">
            <div class="min-w-0 flex-1 truncate font-medium text-slate-800 group-hover:text-brand-600 dark:text-slate-100" title="${filename}">${filename}</div>
            <span class="badge-${meeting.status} shrink-0">${statusText}</span>
        </div>
        <div class="mt-2 space-y-0.5 text-xs text-slate-500 dark:text-slate-400">
            <div>${i18n.t('meetings.created')}: ${date}</div>
            ${meeting.duration ? `<div>${i18n.t('meetings.duration')}: ${meeting.duration}</div>` : ''}
            ${meeting.file_size ? `<div>${i18n.t('meetings.size')}: ${formatFileSize(meeting.file_size)}</div>` : ''}
        </div>
        ${active ? `
        <div class="mt-3">
            <div class="progress-track"><div class="progress-value" data-role="fill" style="width:${meeting.progress || 0}%"></div></div>
            <div class="mt-1 flex justify-between text-xs text-slate-500 dark:text-slate-400">
                <span data-role="stage">${formatStage(meeting.stage)}</span><span data-role="eta">${formatEta(meeting.eta_seconds)}</span>
            </div>
            <button class="btn-secondary mt-2 w-full" data-role="cancel"
                    onclick="event.stopPropagation(); cancelMeeting(${meeting.id})">${i18n.t('meetings.cancel')}</button>
            ${meeting.status === 'uploaded' ? `
            <button class="btn-secondary mt-2 w-full" data-role="process"
                    onclick="event.stopPropagation(); processMeeting(${meeting.id})">${i18n.t('meetings.process')}</button>` : ''}
        </div>` : ''}
    `;
    return card;
}

let listPollTimer = null;
function startListPolling() {
    if (listPollTimer) clearInterval(listPollTimer);
    listPollTimer = setInterval(async () => {
        const cards = document.querySelectorAll('#meetingsList [data-id][data-status="processing"], #meetingsList [data-id][data-status="uploaded"]');
        if (!cards.length) { clearInterval(listPollTimer); listPollTimer = null; return; }
        for (const card of cards) {
            try {
                const st = await api.getStatus(card.dataset.id);
                if (st.status !== card.dataset.status && (st.status === 'completed' || st.status === 'failed')) {
                    return loadMeetings();   // terminal change -> refresh whole list
                }
                const fill = card.querySelector('[data-role="fill"]');
                const stage = card.querySelector('[data-role="stage"]');
                const eta = card.querySelector('[data-role="eta"]');
                if (fill) fill.style.width = (st.progress || 0) + '%';
                if (stage) stage.textContent = formatStage(st.stage);
                if (eta) eta.textContent = formatEta(st.eta_seconds);
                // The open modal is refreshed from the SAME poll as its card, so the
                // two can no longer disagree if the websocket drops or a stage runs
                // for a minute without emitting anything.
                if (openModalMeetingId === Number(card.dataset.id)) {
                    applyModalProgress(st);
                }
            } catch (e) { /* ignore transient */ }
        }
    }, 5000);
}

let openModalMeetingId = null;

function applyModalProgress(st) {
    const fill = document.getElementById('modalProgressFill');
    const text = document.getElementById('modalProgressText');
    const badge = document.getElementById('meetingStatus');
    if (fill) fill.style.width = (st.progress || 0) + '%';
    if (text && st.stage) {
        text.textContent = formatStage(st.stage)
            + (st.eta_seconds ? ' · ' + formatEta(st.eta_seconds) : '');
    }
    if (badge && st.status) {
        badge.textContent = i18n.t(`filters.${st.status}`) || st.status;
        badge.className = `badge-${st.status}`;
    }
}

async function showMeetingDetail(meetingId) {
    const modal = document.getElementById('meetingModal');
    const modalBody = document.getElementById('modalBody');

    modal.style.display = 'flex';
    openModalMeetingId = Number(meetingId);
    modalBody.innerHTML = `<div class="py-10 text-center text-sm text-slate-400">${i18n.t('meetings.loading')}</div>`;

    try {
        const meeting = await api.getMeeting(meetingId);
        const statusText = i18n.t(`filters.${meeting.status}`) || meeting.status;

        const row = (label, val) => val
            ? `<div class="flex justify-between gap-4 border-b border-slate-100 py-1.5 last:border-0 dark:border-slate-800/60"><span class="text-slate-500 dark:text-slate-400">${label}</span><span class="text-right font-medium">${val}</span></div>`
            : '';

        let content = `
            <section class="mb-5">
                <h3 class="section-title mb-2">${i18n.t('modal.information')}</h3>
                <div class="text-sm">
                    ${row(i18n.t('modal.filename'), escapeHtml(meeting.original_filename))}
                    ${row(i18n.t('modal.status'), `<span id="meetingStatus" class="badge-${meeting.status}">${statusText}</span>`)}
                    ${row(i18n.t('modal.created'), new Date(meeting.created_at).toLocaleString())}
                    ${row(i18n.t('modal.duration'), meeting.duration)}
            <div class="flex flex-wrap items-center justify-between gap-3 py-1">
                <span class="text-sm text-slate-500 dark:text-slate-400">${i18n.t('modal.project')}</span>
                <span class="flex flex-1 flex-wrap items-center justify-end gap-2">
                    <!-- w-48 cut the hint to "id для группировки и RA(". The field sizes
                         itself from its own placeholder now, and still shrinks on narrow
                         screens instead of forcing a horizontal scrollbar. -->
                    <input id="meetingProject" class="field h-8 min-w-[18rem] max-w-full flex-1 text-sm" value="${escapeHtml(meeting.project || '')}"
                           placeholder="${i18n.t('modal.projectPlaceholder')}"
                           title="${i18n.t('modal.projectPlaceholder')}">
                    <button class="btn-secondary" onclick="saveProject(${meetingId})">${i18n.t('modal.saveProject')}</button>
                </span>
            </div>
                    ${row(i18n.t('modal.size'), meeting.file_size ? formatFileSize(meeting.file_size) : '')}
                    ${row(i18n.t('modal.processingTime'), meeting.processing_time ? meeting.processing_time + i18n.t('time.second') : '')}
                </div>
            </section>
        `;

        // Real-time progress для processing статуса. SEEDED from the meeting we just
        // fetched: a hard-coded 0% plus "waiting for updates" ignored the progress
        // already known, so opening a run mid-stage showed an empty bar and that
        // placeholder until the next broadcast - while the card next to it already
        // said "Создание саммари ~8с". The websocket then refines it live.
        if (meeting.status === 'processing' || meeting.status === 'uploaded') {
            const pct = meeting.progress || 0;
            const seeded = meeting.stage
                ? `${formatStage(meeting.stage)}${meeting.eta_seconds ? ' · ' + formatEta(meeting.eta_seconds) : ''}`
                : i18n.t('modal.waiting');
            content += `
                <section class="mb-5" id="progressSection">
                    <h3 class="section-title mb-2">${i18n.t('modal.progress')}</h3>
                    <div class="progress-track"><div class="progress-value" id="modalProgressFill" style="width: ${pct}%"></div></div>
                    <p id="modalProgressText" class="mt-2 text-sm text-slate-500 dark:text-slate-400">${escapeHtml(seeded)}</p>
                </section>
            `;
        }

        // Stage timeline of a finished run. The desktop restores every stage and
        // its duration when a meeting is selected; the cabinet showed only a
        // status badge, so "how long did what take" was unanswerable here.
        content += `<section class="mb-5" id="stageSection" style="display:none">
                <h3 class="section-title mb-2">${i18n.t('modal.stages')}</h3>
                <div id="stageList" class="space-y-1 text-sm"></div>
            </section>`;

        if (meeting.error_message) {
            content += `
                <section class="mb-5">
                    <h3 class="section-title mb-2">${i18n.t('modal.error')}</h3>
                    <p class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-300">${escapeHtml(i18n.serverMessage(meeting.error_message))}</p>
                </section>
            `;
        }

        // Сохраняем доступ к уже созданным результатам даже если более поздний
        // этап завершился ошибкой (например, облачная квота кончилась на 8-й
        // функции анализа). Пользователь должен видеть частичный результат и
        // иметь возможность повторить AI-этап по готовой транскрипции.
        if (meeting.summary_path || meeting.analysis_path) {
            const EXPORT_FMTS = ['txt', 'md', 'json', 'html', 'pdf', 'docx'];
            const preCls = 'max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-50 p-3 text-[13px] leading-relaxed text-slate-700 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-200';
            const exportRow = (kind) => `
                <div class="mt-3 flex flex-wrap items-center gap-1.5">
                    <span class="mr-1 text-xs text-slate-500 dark:text-slate-400">${i18n.t('modal.exportAs')}:</span>
                    ${EXPORT_FMTS.map(f => `<button class="btn-chip" onclick="exportArt(${meetingId}, '${kind}', '${f}')">${f.toUpperCase()}</button>`).join('')}
                    <button class="btn-chip" onclick="exportObsidian(${meetingId}, '${kind}')"
                            title="${i18n.t('modal.obsidianTip')}">→ Obsidian</button>
                </div>`;

            if (meeting.summary_path) {
                content += `
                    <section class="mb-5">
                        <div class="mb-2 flex items-center justify-between gap-2">
                            <h3 class="section-title">${i18n.t('modal.summary')}</h3>
                            <span id="summaryVersionPicker"></span>
                        </div>
                        <pre class="${preCls}" id="summaryPreview">…</pre>
                        ${exportRow('summary')}
                    </section>`;
            }
            if (meeting.analysis_path) {
                content += `
                    <section class="mb-5">
                        <div class="mb-2 flex items-center justify-between gap-2">
                            <h3 class="section-title">${i18n.t('modal.analysis')}</h3>
                            <span id="analysisVersionPicker"></span>
                        </div>
                        <pre class="${preCls}" id="analysisPreview">…</pre>
                        ${exportRow('analysis')}
                    </section>`;
            }
        }

        if (meeting.transcript_path) {
            // The transcript is EDITABLE, like the desktop pane: a correction
            // saved here is what Regenerate then summarises and analyses.
            content += `
                <section class="mb-5">
                    <div class="mb-2 flex items-center justify-between gap-2">
                        <h3 class="section-title">${i18n.t('modal.transcript')}</h3>
                        <button class="btn-secondary" onclick="saveTranscript(${meetingId})">${i18n.t('modal.saveTranscript')}</button>
                    </div>
                    <textarea id="transcriptEditor" rows="10"
                              class="field w-full font-mono text-[13px] leading-relaxed"></textarea>
                    <p id="transcriptMsg" class="mt-1 text-xs"></p>
                </section>
                <section class="mb-5" id="speakersSection" style="display:none">
                    <h3 class="section-title mb-2">${i18n.t('modal.speakers')}</h3>
                    <div id="speakersBody" class="space-y-2"></div>
                </section>
                <section class="mb-5 flex flex-wrap gap-2">
                    <button class="btn-secondary" onclick="regenerateMeeting(${meetingId})">${i18n.t('modal.regenerate')}</button>
                    ${meeting.status === 'completed' ? `<button class="btn-secondary" onclick="addToRag(${meetingId})">${i18n.t('modal.addToRag')}</button>` : ''}
                    <button class="btn-secondary" id="exportSpeakersBtn" style="display:none"
                            onclick="exportSpeakers(${meetingId})">${i18n.t('modal.exportBySpeaker')}</button>
                </section>`;
        }

        // Download buttons
        // Gate on CONTENT, not on a path: a run that recognised no speech still has
        // a transcript path, and the button then downloaded a zero-byte file.
        const downloads = [];
        if (meeting.has_source) downloads.push({ type: 'video', label: i18n.t('modal.downloadSource') });
        if (meeting.has_transcript) downloads.push({ type: 'transcript', label: i18n.t('modal.downloadTranscript') });
        if (meeting.has_summary) downloads.push({ type: 'summary', label: i18n.t('modal.downloadSummary') });
        if (meeting.has_analysis) downloads.push({ type: 'analysis', label: i18n.t('modal.downloadAnalysis') });

        if (downloads.length > 0) {
            content += `
                <section class="mb-5">
                    <h3 class="section-title mb-2">${i18n.t('modal.downloads')}</h3>
                    <div class="flex flex-wrap gap-2">
                        ${downloads.map(d => `<button class="btn-chip" onclick="downloadFile(${meetingId}, '${d.type}')">${d.label}</button>`).join('')}
                    </div>
                </section>
            `;
        }

        // Delete button
        content += `
            <section class="border-t border-slate-200 pt-4 dark:border-slate-800">
                <button class="btn-danger" onclick="deleteMeeting(${meetingId})">${i18n.t('modal.delete')}</button>
            </section>
        `;

        modalBody.innerHTML = content;
        loadStageTimeline(meetingId);

        // Подключаем WebSocket для real-time обновлений
        if (meeting.status === 'processing' || meeting.status === 'uploaded') {
            connectWebSocket(meetingId);
        }

        // Догружаем все уже существующие результаты, в том числе частичные
        // артефакты встречи, упавшей на более позднем AI-этапе.
        if (meeting.transcript_path || meeting.summary_path || meeting.analysis_path) {
            hydrateCompleted(meetingId, meeting);
        }

    } catch (error) {
        modalBody.innerHTML = `<div class="py-10 text-center text-sm text-red-500">${i18n.t('common.error')}: ${escapeHtml(i18n.serverMessage(error.message))}</div>`;
    }
}

let currentWebSocket = null;

function connectWebSocket(meetingId) {
    // Закрываем предыдущее соединение если есть
    if (currentWebSocket) {
        currentWebSocket.close();
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/${meetingId}` +
        `?token=${encodeURIComponent(api.token || '')}`;

    currentWebSocket = new WebSocket(wsUrl);

    currentWebSocket.onopen = () => {
        console.log('WebSocket connected');
    };

    currentWebSocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };

    currentWebSocket.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    currentWebSocket.onclose = () => {
        console.log('WebSocket closed');
        currentWebSocket = null;
    };

    // Ping каждые 30 секунд чтобы держать соединение
    const pingInterval = setInterval(() => {
        if (currentWebSocket && currentWebSocket.readyState === WebSocket.OPEN) {
            currentWebSocket.send('ping');
        } else {
            clearInterval(pingInterval);
        }
    }, 30000);
}

function handleWebSocketMessage(data) {
    const statusElement = document.getElementById('meetingStatus');
    const progressFill = document.getElementById('modalProgressFill');
    const progressText = document.getElementById('modalProgressText');

    switch (data.type) {
        case 'connected':
            console.log('Connected to meeting updates');
            break;

        case 'status':
            if (statusElement) {
                const statusText = i18n.t(`filters.${data.status}`) || data.status;
                statusElement.textContent = statusText;
            }
            if (progressText) {
                progressText.textContent = data.details || data.status;
            }
            break;

        case 'progress':
            if (progressFill) {
                progressFill.style.width = data.progress + '%';
            }
            if (progressText) {
                progressText.textContent = `${formatStage(data.stage)}: ${data.details}`;
            }
            break;

        case 'error':
            if (statusElement) {
                statusElement.textContent = i18n.t('filters.failed');
            }
            if (progressText) {
                progressText.textContent = i18n.t('ws.error') + ': ' + data.error;
                progressText.style.color = '#d32f2f';
            }
            // Закрываем WebSocket
            if (currentWebSocket) {
                currentWebSocket.close();
            }
            break;

        case 'completed':
            if (statusElement) {
                statusElement.textContent = i18n.t('filters.completed');
            }
            if (progressText) {
                progressText.textContent = i18n.t('ws.completed');
            }
            if (progressFill) {
                progressFill.style.width = '100%';
            }
            // Закрываем WebSocket
            if (currentWebSocket) {
                currentWebSocket.close();
            }
            // Обновляем список встреч
            setTimeout(() => {
                loadMeetings();
            }, 2000);
            break;

        case 'pong':
            // Ответ на ping
            break;
    }
}

// Закрываем WebSocket при закрытии модального окна
document.getElementById('closeModal').addEventListener('click', () => {
    if (currentWebSocket) {
        currentWebSocket.close();
    }
    openModalMeetingId = null;      // stop refreshing a modal nobody is looking at
});

async function downloadFile(meetingId, fileType) {
    try {
        await api.downloadFile(meetingId, fileType);
    } catch (error) {
        alert(i18n.t('modal.downloadFailed') + ': ' + i18n.serverMessage(error.message));
    }
}

async function deleteMeeting(meetingId) {
    if (!confirm(i18n.t('modal.deleteConfirm'))) {
        return;
    }

    try {
        await api.deleteMeeting(meetingId);
        document.getElementById('meetingModal').style.display = 'none';
        await loadMeetings();
    } catch (error) {
        alert(i18n.t('modal.deleteFailed') + ': ' + i18n.serverMessage(error.message));
    }
}

// ============================================================================
// Completed-meeting details: previews, versions, speakers, RAG, regenerate
// ============================================================================

async function hydrateCompleted(meetingId, meeting) {
    // Превью саммари/анализа
    if (meeting.summary_path) {
        api.fetchText(meetingId, 'summary')
            .then(t => { const el = document.getElementById('summaryPreview'); if (el) el.textContent = t; })
            .catch(() => { const el = document.getElementById('summaryPreview'); if (el) el.textContent = '—'; });
    }
    if (meeting.analysis_path) {
        api.fetchText(meetingId, 'analysis')
            .then(t => { const el = document.getElementById('analysisPreview'); if (el) el.textContent = t; })
            .catch(() => { const el = document.getElementById('analysisPreview'); if (el) el.textContent = '—'; });
    }

    // Независимые селекторы версий: превью и экспорт всегда относятся к
    // одной и той же явно выбранной версии каждого артефакта.
    try {
        const data = await api.listVersions(meetingId);
        const renderVersionPicker = (kind) => {
            const versions = data[kind] || [];
            const picker = document.getElementById(`${kind}VersionPicker`);
            if (!picker || !versions.length) return;
            const latest = Math.max(...versions.map(v => v.version));
            if (versions.length === 1) {
                picker.textContent = `v${latest}`;
                picker.className = 'text-xs text-slate-500 dark:text-slate-400';
                return;
            }
            const selectId = `${kind}VersionSelect`;
            picker.innerHTML = `<select id="${selectId}" class="field w-auto py-1 text-xs">${versions.map(v =>
                `<option value="${v.version}"${v.version === latest ? ' selected' : ''}>v${v.version}</option>`).join('')}</select>`;
            document.getElementById(selectId).addEventListener('change', async (event) => {
                const preview = document.getElementById(`${kind}Preview`);
                if (!preview) return;
                preview.textContent = '…';
                try {
                    preview.textContent = await api.fetchText(
                        meetingId, kind, parseInt(event.target.value, 10));
                } catch (e) {
                    preview.textContent = '—';
                }
            });
        };
        renderVersionPicker('summary');
        renderVersionPicker('analysis');
    } catch (e) { /* versions endpoint optional */ }

    loadTranscriptEditor(meetingId);

    // Спикеры (только если транскрипт диаризован): speakers=["SPEAKER_00",…], stats={label:{segments,words}}
    try {
        const data = await api.getSpeakers(meetingId);
        const speakers = data.speakers || [];
        const stats = data.stats || {};
        if (speakers.length) {
            document.getElementById('speakersSection').style.display = 'block';
            // The per-speaker export only exists for a diarised transcript; the
            // button used to be shown for every meeting and answered with an
            // error alert on the ones that have no speakers at all.
            const exportBtn = document.getElementById('exportSpeakersBtn');
            if (exportBtn) exportBtn.style.display = '';
            const body = document.getElementById('speakersBody');
            body.innerHTML = speakers.map(label => `
                <div class="flex items-center gap-3">
                    <span class="w-40 shrink-0 text-sm">${escapeHtml(label)} <span class="text-slate-400">(${(stats[label] || {}).segments || 0})</span></span>
                    <input class="field speaker-rename" data-label="${escapeHtml(label)}" placeholder="${i18n.t('modal.newName')}">
                </div>`).join('') +
                `<div class="pt-1"><button class="btn-secondary" onclick="submitRename(${meetingId})">${i18n.t('modal.saveNames')}</button></div>`;
        }
    } catch (e) { /* not diarised */ }
}

// ============================================================================
// Administration - installation-wide, admin only
// ============================================================================

async function openAdmin() {
    const modal = document.getElementById('adminModal');
    const body = document.getElementById('adminBody');
    modal.style.display = 'flex';
    body.innerHTML = `<div class="py-10 text-center text-sm text-slate-400">${i18n.t('meetings.loading')}</div>`;
    let srv, catalog, packages;
    try {
        srv = (await api.serverSettings()).settings || {};
        catalog = await api.getEngines();
        packages = (await api.enginePackages()).engines || [];
    } catch (e) {
        body.innerHTML = `<div class="py-10 text-center text-sm text-red-500">${escapeHtml(i18n.serverMessage(e.message))}</div>`;
        return;
    }
    const pkgByEngine = Object.fromEntries(packages.map(p => [p.engine, p]));
    const workers = Number(srv.parallelWorkers || 0);

    body.innerHTML = `
        <p class="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">${i18n.t('admin.hint')}</p>
        <section class="mb-6 space-y-2">
            <h3 class="section-title">${i18n.t('admin.workers')}</h3>
            <div class="flex flex-wrap items-center gap-2">
                <select id="adminWorkers" class="field w-auto py-1">
                    <option value="0"${workers === 0 ? ' selected' : ''}>${i18n.t('admin.workersAuto')}</option>
                    ${[1, 2, 3, 4].map(n => `<option value="${n}"${workers === n ? ' selected' : ''}>${n}</option>`).join('')}
                </select>
                <span class="text-xs text-slate-500 dark:text-slate-400">${i18n.t('queue.workers')} ${srv.effectiveWorkers}</span>
                <span id="adminWorkersMsg" class="text-xs text-emerald-600 dark:text-emerald-400"></span>
            </div>
        </section>
        <section class="space-y-3">
            <h3 class="section-title">${i18n.t('admin.engines')}</h3>
            <div id="adminEngines" class="space-y-3"></div>
            <div id="adminJobs" class="space-y-1 text-xs text-slate-500 dark:text-slate-400"></div>
        </section>`;

    document.getElementById('adminWorkers').addEventListener('change', async (e) => {
        const msg = document.getElementById('adminWorkersMsg');
        try {
            const r = await api.saveServerSettings({parallelWorkers: parseInt(e.target.value, 10)});
            msg.textContent = `${i18n.t('admin.workersSaved')} (${r.settings.effectiveWorkers})`;
        } catch (err) {
            msg.textContent = i18n.t('admin.failed') + ': ' + err.message;
        }
    });

    const list = document.getElementById('adminEngines');
    list.innerHTML = (catalog.engines || []).map((eng) => {
        const pkg = pkgByEngine[eng.id] || {installed: true, missing: []};
        const models = (eng.models || []).map((m) => `
            <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 py-1 last:border-0 dark:border-slate-800/60">
                <span class="text-sm">${escapeHtml(m.id)}
                    <span class="text-xs text-slate-400">${m.approx_mb ? m.approx_mb + ' MB' : ''}${m.available ? ' ✓' : ''}</span>
                </span>
                <span class="flex flex-wrap gap-1.5">
                    <button class="btn-chip" onclick="adminDownload('${eng.id}','${m.id}')">${i18n.t('admin.download')}</button>
                    <button class="btn-chip" onclick="adminUpdateCheck('${eng.id}','${m.id}')">${i18n.t('admin.update')}</button>
                </span>
            </div>`).join('');
        return `<div class="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <div class="mb-1 flex flex-wrap items-center justify-between gap-2">
                <span class="font-medium">${escapeHtml(eng.label || eng.id)}</span>
                <span class="flex items-center gap-2">
                    <span class="text-xs ${pkg.installed ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}">
                        ${pkg.installed ? i18n.t('admin.installed') : i18n.t('admin.missing') + ': ' + (pkg.missing || []).join(', ')}
                    </span>
                    ${pkg.installed ? '' : `<button class="btn-chip" onclick="adminInstall('${eng.id}')">${i18n.t('admin.install')}</button>`}
                </span>
            </div>
            ${models || `<p class="text-xs text-slate-400">—</p>`}
        </div>`;
    }).join('');
    adminPollJobs();
}

async function adminDownload(engine, model) {
    try {
        await api.downloadModel(engine, model);
        document.getElementById('adminJobs').textContent = i18n.t('admin.jobStarted');
        adminPollJobs();
    } catch (e) { alert(i18n.t('admin.failed') + ': ' + i18n.serverMessage(e.message)); }
}

async function adminInstall(engine) {
    try {
        await api.installEngine(engine);
        document.getElementById('adminJobs').textContent = i18n.t('admin.jobStarted');
        adminPollJobs();
    } catch (e) { alert(i18n.t('admin.failed') + ': ' + i18n.serverMessage(e.message)); }
}

async function adminUpdateCheck(engine, model) {
    try {
        const r = await api.checkModelUpdate(engine, model);
        alert(`${engine}/${model}: ` + (r.update_available
            ? i18n.t('admin.updateAvailable') : i18n.t('admin.upToDate')));
    } catch (e) { alert(i18n.t('admin.failed') + ': ' + i18n.serverMessage(e.message)); }
}

let adminJobTimer = null;
async function adminPollJobs() {
    if (adminJobTimer) clearInterval(adminJobTimer);
    const render = async () => {
        const box = document.getElementById('adminJobs');
        if (!box || document.getElementById('adminModal').style.display === 'none') {
            clearInterval(adminJobTimer); adminJobTimer = null; return;
        }
        try {
            const jobs = (await api.modelDownloads()).downloads || [];
            box.innerHTML = jobs.map(j =>
                `<div>${escapeHtml(j.engine)}/${escapeHtml(String(j.model))}: ${escapeHtml(j.status)} ${j.percent || 0}% ${escapeHtml((j.detail || '').slice(0, 90))}</div>`).join('');
        } catch (e) { /* transient */ }
    };
    await render();
    adminJobTimer = setInterval(render, 3000);
}

async function exportObsidian(meetingId, kind) {
    // Follows the version picker, not "the newest": exporting v2 while the panel
    // showed v2 and getting v4 in the vault is the defect this avoids.
    const sel = document.getElementById(`${kind}VersionSelect`);
    const version = sel ? parseInt(sel.value, 10) : 0;
    const body = {kinds: [kind]};
    if (kind === 'summary') body.summary_version = version || 0;
    if (kind === 'analysis') body.analysis_version = version || 0;
    try {
        const r = await api.exportObsidian(meetingId, body);
        const written = Object.values(r.written || {});
        alert(written.length
            ? i18n.t('modal.obsidianDone') + '\n' + written.join('\n')
            : i18n.t('modal.obsidianNothing'));
    } catch (e) {
        alert(i18n.t('modal.obsidianFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

async function exportArt(meetingId, kind, fmt) {
    const sel = document.getElementById(`${kind}VersionSelect`);
    const version = sel ? parseInt(sel.value, 10) : 0;
    try {
        await api.exportArtifact(meetingId, kind, fmt, version);
    } catch (e) {
        alert(i18n.t('modal.exportFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

async function regenerateMeeting(meetingId) {
    if (!confirm(i18n.t('modal.regenerateConfirm'))) return;
    try {
        await api.regenerate(meetingId);
        document.getElementById('meetingModal').style.display = 'none';
        await loadMeetings();
    } catch (e) {
        alert(i18n.t('modal.regenerateFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

async function addToRag(meetingId) {
    try {
        await api.ragAdd(meetingId);
        alert(i18n.t('modal.addedToRag'));
    } catch (e) {
        alert(i18n.t('modal.ragFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

async function submitRename(meetingId) {
    const map = {};
    document.querySelectorAll('.speaker-rename').forEach(inp => {
        const v = inp.value.trim();
        if (v) map[inp.dataset.label] = v;
    });
    if (!Object.keys(map).length) return;
    try {
        await api.renameSpeakers(meetingId, map);
        alert(i18n.t('modal.namesSaved'));
    } catch (e) {
        alert(i18n.t('modal.renameFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

async function exportSpeakers(meetingId) {
    try {
        await api.exportBySpeaker(meetingId);
    } catch (e) {
        alert(i18n.t('modal.exportFailed') + ': ' + i18n.serverMessage(e.message));
    }
}

// ============================================================================
// Settings panel
// ============================================================================

// The same ten providers the desktop offers, with the same readable labels. The
// cabinet listed nine bare ids ("local", "openai", …): the local agent CLI was
// missing outright, and the rest gave no hint which service they mean.
function aiProviders() {
    return [
        { value: 'local', label: i18n.t('settings.providerLocal') },
        { value: 'agent', label: i18n.t('settings.providerAgent') },
        { value: 'openai', label: 'OpenAI (ChatGPT)' },
        { value: 'anthropic', label: 'Anthropic (Claude)' },
        { value: 'google', label: 'Google (Gemini)' },
        { value: 'xai', label: 'xAI (Grok)' },
        { value: 'qwen', label: 'Qwen (Alibaba Cloud)' },
        { value: 'mistral', label: 'Mistral AI' },
        { value: 'deepseek', label: 'DeepSeek' },
        { value: 'gemma', label: 'Gemma' },
    ];
}

async function openSettings() {
    const modal = document.getElementById('settingsModal');
    const body = document.getElementById('settingsBody');
    modal.style.display = 'flex';
    body.innerHTML = `<div class="py-10 text-center text-sm text-slate-400">${i18n.t('meetings.loading')}</div>`;

    const lang = i18n.getCurrentLanguage();
    let s, engines = [], templates = { builtin: [], user: [] };
    try {
        s = (await api.getSettings()).settings || {};
    } catch (e) {
        body.innerHTML = `<div class="py-10 text-center text-sm text-red-500">${i18n.t('settings.loadFailed')}: ${escapeHtml(i18n.serverMessage(e.message))}</div>`;
        return;
    }
    // Engine catalog + templates are best-effort (fall back gracefully).
    try { engines = (await api.getEngines()).engines || []; } catch (e) { /* fallback below */ }
    // The built-in library ships a speaker-aware variant of every template, and the
    // cabinet hard-coded `false`, so diarisation never reached the prompt.
    const wantSpeaker = !!s.useSpeakerPrompt;
    try { templates = await api.listTemplates(lang, wantSpeaker); } catch (e) { /* fallback below */ }

    const g = (k, d = '') => (s[k] !== undefined && s[k] !== null ? s[k] : d);
    const esc = (v) => escapeHtml(v);
    const optLabel = (o) => (o.label && (o.label[lang] || o.label.en)) || o.id;

    const txt = (k, labelKey, val, type = 'text', ph = '') =>
        `<label class="block"><span class="field-label">${i18n.t(labelKey)}</span>
            <input type="${type}" class="field" data-key="${k}" data-type="${type === 'number' ? 'number' : 'text'}" value="${esc(val)}" placeholder="${esc(ph)}"></label>`;
    const chk = (k, labelKey, val) =>
        `<label class="flex cursor-pointer items-start gap-2.5 text-sm">
            <input type="checkbox" class="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500 dark:border-slate-600 dark:bg-slate-800" data-key="${k}" data-type="bool" ${val ? 'checked' : ''}>
            <span class="text-slate-600 dark:text-slate-300">${i18n.t(labelKey)}</span></label>`;
    // options: array of {value,label} OR plain strings
    const selOpts = (k, labelKey, val, options, extraAttr = '') =>
        `<label class="block"><span class="field-label">${i18n.t(labelKey)}</span>
            <select class="field" data-key="${k}" data-type="text" ${extraAttr}>${options.map(o => {
                const v = typeof o === 'string' ? o : o.value, l = typeof o === 'string' ? o : o.label;
                return `<option value="${esc(v)}"${v === val ? ' selected' : ''}>${l}</option>`;
            }).join('')}</select></label>`;

    // Engine + model dropdowns from the catalog (fallback to a text field if empty).
    const curEngine = g('transcriptionEngine', engines[0] && engines[0].id || 'faster-whisper');
    const engineField = engines.length
        ? selOpts('transcriptionEngine', 'settings.engine', curEngine,
            engines.map(e => ({ value: e.id, label: optLabel(e) })), 'id="engineSelect"')
        : txt('transcriptionEngine', 'settings.engine', g('transcriptionEngine'));
    const modelField = engines.length
        ? `<label class="block"><span class="field-label">${i18n.t('settings.model')}</span>
            <select class="field" data-key="whisperModel" data-type="text" id="modelSelect"></select></label>`
        : txt('whisperModel', 'settings.model', g('whisperModel'));

    const tplOptions = [...(templates.builtin || []), ...(templates.user || [])];

    body.innerHTML = `
        <div class="space-y-6">
            <section class="space-y-3">
                <h3 class="section-title">${i18n.t('settings.transcription')}</h3>
                <div class="grid items-end gap-3 sm:grid-cols-2">
                    ${engineField}
                    ${modelField}
                    ${selOpts('transcriptionLanguage', 'settings.transcriptionLanguage', g('transcriptionLanguage', 'ru'), [{ value: 'ru', label: 'Русский' }, { value: 'en', label: 'English' }])}
                    ${selOpts('outputLanguage', 'settings.outputLanguage', g('outputLanguage', 'auto'), [{ value: 'auto', label: i18n.t('settings.outputAuto') }, { value: 'ru', label: 'Русский' }, { value: 'en', label: 'English' }])}
                    ${selOpts('whisperDevice', 'settings.device', g('whisperDevice', 'auto'), ['auto', 'cuda', 'cpu'])}
                    ${selOpts('diarizationBackend', 'settings.diarization', g('diarizationBackend', 'sherpa'), ['sherpa', 'pyannote', 'off'])}
                    ${txt('hfToken', 'settings.hfToken', g('hfToken'), 'password')}
                    ${txt('transcriptionHint', 'settings.hint', g('transcriptionHint'), 'text', i18n.t('settings.hintPh'))}
                </div>
                ${chk('useSpeakerPrompt', 'settings.useSpeakerPrompt', g('useSpeakerPrompt', true))}
                <p class="text-xs leading-relaxed text-slate-500 dark:text-slate-400">${i18n.t('settings.useSpeakerPromptHint')}</p>
            </section>

            <section class="space-y-3">
                <h3 class="section-title">${i18n.t('settings.ai')}</h3>
                <div class="grid items-end gap-3 sm:grid-cols-2">
                    ${selOpts('aiProvider', 'settings.provider', g('aiProvider', 'local'), aiProviders())}
                    ${txt('aiModel', 'settings.aiModel', g('aiModel'), 'text', i18n.t('settings.aiModelPh'))}
                    ${txt('apiKey', 'settings.apiKey', g('apiKey'), 'password')}
                    ${txt('localEndpoint', 'settings.endpoint', g('localEndpoint'), 'text', 'http://127.0.0.1:8080/v1')}
                    ${txt('agentCommand', 'settings.agentCommand', g('agentCommand'), 'text', 'claude -p {prompt}')}
                    ${txt('agentCwd', 'settings.agentCwd', g('agentCwd'))}
                    ${selOpts('analysisSource', 'settings.analysisSource', g('analysisSource', 'transcript'), [
                        { value: 'transcript', label: i18n.t('settings.analysisTranscript') },
                        { value: 'summary', label: i18n.t('settings.analysisSummary') }
                    ])}
                </div>
                <p class="text-xs leading-relaxed text-slate-500 dark:text-slate-400">${i18n.t('settings.analysisSourceHint')}</p>
            </section>

            <section class="space-y-3">
                <h3 class="section-title">${i18n.t('settings.promptSection')}</h3>
                <div class="flex flex-wrap items-end gap-2">
                    <label class="block flex-1">
                        <span class="field-label">${i18n.t('settings.template')}</span>
                        <select class="field" id="templateSelect">
                            ${tplOptions.map((t, i) => `<option value="${i}">${esc(t.name)}${t.builtin ? '' : ' •'}</option>`).join('')}
                        </select>
                    </label>
                    <button type="button" class="btn-secondary" id="saveTplBtn">${i18n.t('settings.saveTemplate')}</button>
                    <button type="button" class="btn-secondary" id="updTplBtn">${i18n.t('settings.updateTemplate')}</button>
                    <button type="button" class="btn-secondary" id="delTplBtn">${i18n.t('settings.deleteTemplate')}</button>
                </div>
                <label class="block">
                    <span class="field-label">${i18n.t('settings.prompt')}</span>
                    <textarea class="field min-h-[140px]" data-key="prompt" data-type="text">${esc(g('prompt'))}</textarea>
                </label>
            </section>

            <section class="space-y-3">
                <div class="flex flex-wrap items-center justify-between gap-2">
                    <h3 class="section-title">${i18n.t('settings.analysisFeatures')}</h3>
                    <span class="flex gap-2">
                        <button type="button" class="btn-secondary" id="featAllBtn">${i18n.t('settings.selectAll')}</button>
                        <button type="button" class="btn-secondary" id="featNoneBtn">${i18n.t('settings.selectNone')}</button>
                    </span>
                </div>
                <div class="space-y-2.5" id="analysisFeatureList">
                    ${chk('extractActionItems', 'settings.actionItems', g('extractActionItems', true))}
                    ${chk('analyzeSentiment', 'settings.sentiment', g('analyzeSentiment', true))}
                    ${chk('categorizeAutomatically', 'settings.categorize', g('categorizeAutomatically', true))}
                    ${chk('generateFollowupQuestions', 'settings.followup', g('generateFollowupQuestions', true))}
                    ${chk('generateFormalProtocol', 'settings.protocol', g('generateFormalProtocol', true))}
                </div>
            </section>

            <section class="space-y-3">
                <h3 class="section-title">${i18n.t('settings.processing')}</h3>
                ${chk('chunkingEnabled', 'settings.chunking', g('chunkingEnabled', false))}
                <p class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">${i18n.t('settings.chunkingWarning')}</p>
                <div class="grid items-end gap-3 sm:grid-cols-2">
                    ${txt('chunkChars', 'settings.chunkChars', g('chunkChars', 0), 'number')}
                    ${txt('aiTimeout', 'settings.timeout', g('aiTimeout', 0), 'number')}
                    ${txt('aiRetries', 'settings.retries', g('aiRetries', 0), 'number')}
                    ${txt('aiRetryDelay', 'settings.retryDelay', g('aiRetryDelay', 0), 'number')}
                </div>
                ${chk('disableReasoning', 'settings.reasoning', g('disableReasoning', false))}
            </section>

            <section class="space-y-3">
                <h3 class="section-title">${i18n.t('settings.advanced')}</h3>
                <div class="space-y-2.5">
                    ${chk('gpuHandoff', 'settings.gpuHandoff', g('gpuHandoff', false))}
                    ${chk('useContextualMemory', 'settings.contextualMemory', g('useContextualMemory', false))}
                    ${chk('googleSheetsIntegration', 'settings.gsheets', g('googleSheetsIntegration', false))}
                </div>
                <div class="grid items-end gap-3 sm:grid-cols-2">
                    ${txt('projectId', 'settings.projectId', g('projectId'), 'text', i18n.t('settings.projectIdPh'))}
                    ${txt('llamaPort', 'settings.llamaPort', g('llamaPort', 8080), 'number')}
                    ${selOpts('youtubeCookiesBrowser', 'settings.ytCookies', g('youtubeCookiesBrowser', 'auto'), ['auto', 'off', 'chrome', 'firefox', 'edge', 'brave', 'opera'])}
                    ${txt('googleSheetsUrl', 'settings.gsheetsUrl', g('googleSheetsUrl'))}
                    ${txt('googleSheetsToken', 'settings.gsheetsToken', g('googleSheetsToken'), 'password')}
                </div>
            </section>

            <section class="space-y-3">
                <h3 class="section-title">${i18n.t('settings.obsidian')}</h3>
                ${chk('obsidianIntegration', 'settings.obsidianEnabled', g('obsidianIntegration', false))}
                <div class="grid items-end gap-3 sm:grid-cols-2">
                    ${txt('obsidianVaultPath', 'settings.obsidianVault', g('obsidianVaultPath'), 'text', 'D:\\\\Obsidian\\\\MyVault')}
                </div>
                <p class="text-xs leading-relaxed text-slate-500 dark:text-slate-400">${i18n.t('settings.obsidianHint')}</p>
                <div class="space-y-2.5">
                    ${chk('updateMeetingIndex', 'settings.obsidianIndex', g('updateMeetingIndex', false))}
                    ${chk('createDataviewQueries', 'settings.obsidianDataview', g('createDataviewQueries', false))}
                    ${chk('createPeopleNotes', 'settings.obsidianPeople', g('createPeopleNotes', false))}
                    ${chk('createTopicNotes', 'settings.obsidianTopics', g('createTopicNotes', false))}
                    ${chk('enableMarkdownExport', 'settings.obsidianMarkdown', g('enableMarkdownExport', false))}
                </div>
            </section>

            <section class="space-y-3">
                <h3 class="section-title">${i18n.t('settings.ragStorage')}</h3>
                ${selOpts('ragCatalogMode', 'settings.ragCatalogMode',
                    g('ragCatalogMode', 'isolated'), [
                        { value: 'isolated', label: i18n.t('settings.ragIsolated') },
                        { value: 'shared', label: i18n.t('settings.ragShared') }
                    ], 'id="ragCatalogMode"')}
                <div id="ragSharedFields" class="space-y-2">
                    ${txt('ragSharedCatalogKey', 'settings.ragSharedKey',
                        g('ragSharedCatalogKey'), 'password')}
                    <div class="flex flex-wrap gap-2">
                        <button type="button" class="btn-secondary" id="generateRagKey">${i18n.t('settings.ragGenerate')}</button>
                        <button type="button" class="btn-secondary" id="copyRagKey">${i18n.t('settings.ragCopy')}</button>
                    </div>
                    <p class="text-xs leading-relaxed text-amber-700 dark:text-amber-300">${i18n.t('settings.ragSharedHint')}</p>
                </div>
                <div class="grid items-end gap-3 sm:grid-cols-2">
                    ${selOpts('ragEmbeddingBackend', 'settings.ragEmbeddingBackend',
                        g('ragEmbeddingBackend', 'sentence-transformers'),
                        ['sentence-transformers', 'openai', 'local'])}
                    ${txt('ragEmbeddingModel', 'settings.ragEmbeddingModel', g('ragEmbeddingModel'))}
                </div>
            </section>

            <div class="flex items-center gap-3 border-t border-slate-200 pt-4 dark:border-slate-800">
                <button class="btn-primary" id="saveSettingsBtn">${i18n.t('settings.save')}</button>
                <button class="btn-secondary" id="resetSettingsBtn">${i18n.t('settings.reset')}</button>
                <span id="settingsMsg" class="text-sm"></span>
            </div>
        </div>`;

    // -- wire engine -> model catalog --
    const modelSel = document.getElementById('modelSelect');
    const engineSel = document.getElementById('engineSelect');
    if (modelSel) {
        const fillModels = (engineId, preselect) => {
            const e = engines.find(x => x.id === engineId) || engines[0] || { models: [] };
            const chosen = preselect || e.default_model;
            modelSel.innerHTML = (e.models || []).map(m =>
                `<option value="${esc(m.id)}"${m.id === chosen ? ' selected' : ''}>${optLabel(m)}${m.available ? ' ✓' : ' ⬇'}</option>`).join('');
        };
        fillModels(curEngine, g('whisperModel'));
        if (engineSel) engineSel.addEventListener('change', () => fillModels(engineSel.value, null));
    }

    // -- wire prompt templates --
    // Toggle every analysis feature at once - five checkboxes had to be clicked
    // one by one to turn the whole analysis on or off.
    const featList = document.getElementById('analysisFeatureList');
    const setAllFeatures = (on) => featList.querySelectorAll('input[type="checkbox"]')
        .forEach((box) => { box.checked = on; });
    document.getElementById('featAllBtn').addEventListener('click', () => setAllFeatures(true));
    document.getElementById('featNoneBtn').addEventListener('click', () => setAllFeatures(false));

    // Re-fetch the template library when the speaker variant is toggled, so the
    // prompt list matches the switch immediately instead of after a reopen.
    const speakerBox = document.querySelector('#settingsBody input[data-key="useSpeakerPrompt"]');
    if (speakerBox) {
        speakerBox.addEventListener('change', async () => {
            try {
                const fresh = await api.listTemplates(lang, speakerBox.checked);
                const opts = [...(fresh.builtin || []), ...(fresh.user || [])];
                const sel = document.getElementById('templateSelect');
                if (!sel) return;
                const chosen = parseInt(sel.value, 10) || 0;
                sel.innerHTML = opts.map((t, i) =>
                    `<option value="${i}">${esc(t.name)}${t.builtin ? '' : ' •'}</option>`).join('');
                sel.value = String(Math.min(chosen, opts.length - 1));
                tplOptions.length = 0;
                opts.forEach((o) => tplOptions.push(o));
                // Refreshing only the LIST changed nothing the user could see: the two
                // variants share every template NAME and differ only in prompt text,
                // so the textarea itself has to be re-rendered from the new variant.
                const area = document.querySelector('#settingsBody textarea[data-key="prompt"]');
                const active = tplOptions[parseInt(sel.value, 10)];
                if (area && active && active.prompt) area.value = active.prompt;
            } catch (e) { /* keep the current list */ }
        });
    }

    const tplSel = document.getElementById('templateSelect');
    const promptArea = document.querySelector('#settingsBody textarea[data-key="prompt"]');
    if (tplSel && promptArea) {
        tplSel.addEventListener('change', () => {
            const t = tplOptions[parseInt(tplSel.value)];
            if (t && (t.prompt || t.id !== 'custom')) promptArea.value = t.prompt || '';
        });
        document.getElementById('saveTplBtn').addEventListener('click', async () => {
            const name = prompt(i18n.t('settings.templateNamePrompt'));
            if (!name || !name.trim()) return;
            try { await api.createTemplate(name.trim(), promptArea.value); openSettings(); }
            catch (e) { alert(i18n.t('settings.saveFailed') + ': ' + i18n.serverMessage(e.message)); }
        });
        // Edit in place. Built-ins have no id and cannot be overwritten - saving
        // a changed built-in is what "Save as new" is for, matching the desktop.
        document.getElementById('updTplBtn').addEventListener('click', async () => {
            const t = tplOptions[parseInt(tplSel.value)];
            if (!t || t.builtin || t.id == null) { alert(i18n.t('settings.editBuiltin')); return; }
            try { await api.updateTemplate(t.id, t.name, promptArea.value); openSettings(); }
            catch (e) { alert(i18n.t('settings.saveFailed') + ': ' + i18n.serverMessage(e.message)); }
        });
        document.getElementById('delTplBtn').addEventListener('click', async () => {
            const t = tplOptions[parseInt(tplSel.value)];
            if (!t || t.builtin || t.id == null) { alert(i18n.t('settings.deleteBuiltin')); return; }
            try { await api.deleteTemplate(t.id); openSettings(); }
            catch (e) { alert(i18n.serverMessage(e.message)); }
        });
    }

    const ragMode = document.getElementById('ragCatalogMode');
    const ragFields = document.getElementById('ragSharedFields');
    const ragKey = document.querySelector('[data-key="ragSharedCatalogKey"]');
    const refreshRagVisibility = () => {
        if (ragFields) ragFields.style.display =
            ragMode && ragMode.value === 'shared' ? '' : 'none';
    };
    if (ragMode) ragMode.addEventListener('change', refreshRagVisibility);
    refreshRagVisibility();
    document.getElementById('generateRagKey').addEventListener('click', () => {
        const bytes = new Uint8Array(32);
        crypto.getRandomValues(bytes);
        const raw = Array.from(bytes, b => String.fromCharCode(b)).join('');
        ragKey.value = 'rsc_' + btoa(raw).replaceAll('+', '-')
            .replaceAll('/', '_').replaceAll('=', '');
    });
    document.getElementById('copyRagKey').addEventListener('click', async () => {
        if (ragKey && ragKey.value) await navigator.clipboard.writeText(ragKey.value);
    });

    document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);
    document.getElementById('resetSettingsBtn').addEventListener('click', async () => {
        if (!confirm(i18n.t('settings.resetConfirm'))) return;
        try { await api.resetSettings(); openSettings(); }
        catch (e) {
            document.getElementById('settingsMsg').textContent =
                i18n.t('settings.saveFailed') + ': ' + i18n.serverMessage(e.message);
        }
    });
}

async function saveSettings() {
    const patch = {};
    document.querySelectorAll('#settingsBody [data-key]').forEach(el => {
        const key = el.dataset.key, type = el.dataset.type;
        if (type === 'bool') patch[key] = el.checked;
        else if (type === 'number') patch[key] = parseInt(el.value) || 0;
        else patch[key] = el.value;
    });
    const msg = document.getElementById('settingsMsg');
    try {
        if (patch.ragCatalogMode === 'shared'
                && !/^rsc_[A-Za-z0-9_-]{40,128}$/.test(patch.ragSharedCatalogKey || '')) {
            throw new Error(i18n.t('settings.ragBadKey'));
        }
        await api.updateSettings(patch);
        msg.textContent = i18n.t('settings.saved');
        msg.className = 'text-sm font-medium text-emerald-600 dark:text-emerald-400';
        setTimeout(() => { document.getElementById('settingsModal').style.display = 'none'; }, 900);
    } catch (e) {
        msg.textContent = i18n.t('settings.saveFailed') + ': ' + i18n.serverMessage(e.message);
        msg.className = 'text-sm font-medium text-red-500';
    }
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}
