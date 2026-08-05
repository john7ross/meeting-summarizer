/**
 * API Client для работы с Meeting Summarizer API
 */

const API_BASE_URL = window.location.origin + '/api';

class APIClient {
    constructor() {
        this.token = localStorage.getItem('token');
    }

    // Получение заголовков с токеном
    getHeaders(includeAuth = true) {
        const headers = {
            'Content-Type': 'application/json'
        };

        if (includeAuth && this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }

        return headers;
    }

    // Сохранение токена
    setToken(token) {
        this.token = token;
        localStorage.setItem('token', token);
    }

    // Удаление токена
    clearToken() {
        this.token = null;
        localStorage.removeItem('token');
    }

    // Проверка авторизации
    isAuthenticated() {
        return !!this.token;
    }

    // Обработка ошибок
    // `redirectOn401: false` for requests where a 401 is an ANSWER, not an expired
    // session. Signing in with wrong credentials returns 401, and treating it as
    // "your session died" reloaded the login page: the error message appeared for
    // a fraction of a second and the form reset itself with nothing shown.
    async handleResponse(response, { redirectOn401 = true } = {}) {
        const onLoginPage = /^\/(index\.html)?$/.test(window.location.pathname);
        if (response.status === 401 && redirectOn401 && !onLoginPage) {
            this.clearToken();
            window.location.href = '/';
            throw new Error('Unauthorized');
        }

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            let detail = data.detail;
            if (Array.isArray(detail)) {
                detail = detail
                    .map((item) => item && (item.msg || item.message))
                    .filter(Boolean)
                    .join('; ');
            } else if (detail && typeof detail === 'object') {
                detail = detail.msg || detail.message || JSON.stringify(detail);
            }
            // Carry the code: the server's detail is English, so a caller that can
            // phrase the case in the user's language needs to recognise it.
            const err = new Error(detail || 'Request failed');
            err.status = response.status;
            throw err;
        }

        return data;
    }

    // ========================================================================
    // Auth endpoints
    // ========================================================================

    async register(username, email, password) {
        const response = await fetch(`${API_BASE_URL}/auth/register`, {
            method: 'POST',
            headers: this.getHeaders(false),
            body: JSON.stringify({ username, email, password })
        });

        return this.handleResponse(response);
    }

    async login(username, password) {
        const response = await fetch(`${API_BASE_URL}/auth/login`, {
            method: 'POST',
            headers: this.getHeaders(false),
            body: JSON.stringify({ username, password })
        });

        const data = await this.handleResponse(response, { redirectOn401: false });
        this.setToken(data.access_token);
        return data;
    }

    async logout() {
        try {
            await fetch(`${API_BASE_URL}/auth/logout`, {
                method: 'POST',
                headers: this.getHeaders()
            });
        } finally {
            this.clearToken();
        }
    }

    async getCurrentUser() {
        const response = await fetch(`${API_BASE_URL}/auth/me`, {
            headers: this.getHeaders()
        });

        return this.handleResponse(response);
    }

    // ========================================================================
    // Meetings endpoints
    // ========================================================================

    async uploadMeeting(file, onProgress, process = true) {
        const formData = new FormData();
        formData.append('file', file);
        // false = store it but do not queue it: the cabinet then offers to cut
        // the recording into per-meeting segments first.
        formData.append('process', process ? 'true' : 'false');

        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();

            xhr.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable && onProgress) {
                    const percentComplete = (e.loaded / e.total) * 100;
                    onProgress(percentComplete);
                }
            });

            xhr.addEventListener('load', () => {
                if (xhr.status === 201) {
                    resolve(JSON.parse(xhr.responseText));
                } else {
                    const error = JSON.parse(xhr.responseText);
                    reject(new Error(error.detail || 'Upload failed'));
                }
            });

            xhr.addEventListener('error', () => {
                reject(new Error('Upload failed'));
            });

            xhr.open('POST', `${API_BASE_URL}/meetings/upload`);
            xhr.setRequestHeader('Authorization', `Bearer ${this.token}`);
            xhr.send(formData);
        });
    }

    // Create a meeting from a video URL (YouTube / file server / …).
    async uploadFromUrl(url, project = null) {
        const response = await fetch(`${API_BASE_URL}/meetings/from-url`, {
            method: 'POST',
            headers: this.getHeaders(),
            body: JSON.stringify({ url, project })
        });
        return this.handleResponse(response);
    }

    async listMeetings(skip = 0, limit = 50, statusFilter = null) {
        let url = `${API_BASE_URL}/meetings/?skip=${skip}&limit=${limit}`;
        if (statusFilter) {
            url += `&status_filter=${statusFilter}`;
        }

        const response = await fetch(url, {
            headers: this.getHeaders()
        });

        return this.handleResponse(response);
    }

    async getMeeting(meetingId) {
        const response = await fetch(`${API_BASE_URL}/meetings/${meetingId}`, {
            headers: this.getHeaders()
        });

        return this.handleResponse(response);
    }

    async updateMeeting(meetingId, data) {
        const response = await fetch(`${API_BASE_URL}/meetings/${meetingId}`, {
            method: 'PATCH',
            headers: this.getHeaders(),
            body: JSON.stringify(data)
        });

        return this.handleResponse(response);
    }

    async clearFinishedMeetings() {
        const response = await fetch(`${API_BASE_URL}/meetings/finished`, {
            method: 'DELETE',
            headers: this.getHeaders()
        });
        if (!response.ok) {
            const e = await response.json().catch(() => ({}));
            throw new Error(e.detail || 'Clear failed');
        }
        return response.json();
    }

    async deleteMeeting(meetingId) {
        const response = await fetch(`${API_BASE_URL}/meetings/${meetingId}`, {
            method: 'DELETE',
            headers: this.getHeaders()
        });

        if (response.status !== 204) {
            throw new Error('Delete failed');
        }
    }

    async downloadFile(meetingId, fileType) {
        const response = await fetch(
            `${API_BASE_URL}/meetings/${meetingId}/download/${fileType}`,
            { headers: this.getHeaders() }
        );

        if (!response.ok) {
            const e = await response.json().catch(() => ({}));
            throw new Error(e.detail || 'Download failed');
        }

        // The server already sends the real file name; this used to overwrite it
        // with "video_8" - no extension at all, so Windows could not even open the
        // file. Same helper as the format exports, so the two cannot drift again.
        const fallbackExt = {raw: '.txt', summary: '.txt', analysis: '.json'}[fileType] || '';
        await this._downloadBlob(response, `${fileType}_${meetingId}${fallbackExt}`);
    }

    // ========================================================================
    // Settings endpoints
    // ========================================================================

    // Returns { user_id, settings: {...typed keys...}, updated_at }
    async getSettings() {
        const response = await fetch(`${API_BASE_URL}/settings/`, {
            headers: this.getHeaders()
        });

        return this.handleResponse(response);
    }

    // patch = a partial object of settings keys (merged server-side)
    async updateSettings(patch) {
        const response = await fetch(`${API_BASE_URL}/settings/`, {
            method: 'PUT',
            headers: this.getHeaders(),
            body: JSON.stringify(patch)
        });

        return this.handleResponse(response);
    }

    async resetSettings() {
        const response = await fetch(`${API_BASE_URL}/settings/`, {
            method: 'DELETE',
            headers: this.getHeaders()
        });

        if (response.status !== 204) {
            throw new Error('Reset failed');
        }
    }

    // ========================================================================
    // Queue endpoints
    // ========================================================================

    async getQueueStatus() {
        const response = await fetch(`${API_BASE_URL}/queue/status`, {
            headers: this.getHeaders()
        });

        return this.handleResponse(response);
    }

    async setWorkersCount(count) {
        const response = await fetch(`${API_BASE_URL}/queue/workers/${count}`, {
            method: 'POST',
            headers: this.getHeaders()
        });

        return this.handleResponse(response);
    }

    // ========================================================================
    // Live status / versions / regenerate / export
    // ========================================================================

    async getStatus(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/status`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async listVersions(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/versions`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async exportObsidian(meetingId, body) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/obsidian`, {
            method: 'POST', headers: this.getHeaders(), body: JSON.stringify(body || {})
        });
        return this.handleResponse(r);
    }

    async serverSettings() {
        const r = await fetch(`${API_BASE_URL}/admin/settings`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async saveServerSettings(patch) {
        const r = await fetch(`${API_BASE_URL}/admin/settings`, {
            method: 'PUT', headers: this.getHeaders(), body: JSON.stringify(patch)
        });
        return this.handleResponse(r);
    }

    async enginePackages() {
        const r = await fetch(`${API_BASE_URL}/admin/engines/packages`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async installEngine(engine) {
        const r = await fetch(`${API_BASE_URL}/admin/engines/${encodeURIComponent(engine)}/install`, {
            method: 'POST', headers: this.getHeaders()
        });
        return this.handleResponse(r);
    }

    async checkModelUpdate(engine, model) {
        const r = await fetch(
            `${API_BASE_URL}/engines/${encodeURIComponent(engine)}/models/${encodeURIComponent(model)}/update-check`,
            { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async meetingTrace(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/trace`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async regenerate(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/regenerate`, { method: 'POST', headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    // Fetches a formatted export (kind: raw|summary|analysis; fmt: txt|md|json|html|pdf|docx)
    // and triggers a browser download. version=0 => latest.
    async exportArtifact(meetingId, kind, fmt, version = 0) {
        let url = `${API_BASE_URL}/meetings/${meetingId}/export/${kind}/${fmt}`;
        if (version > 0) url += `?version=${version}`;
        const r = await fetch(url, { headers: this.getHeaders() });
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || 'Export failed'); }
        await this._downloadBlob(r, `${kind}${version ? '_v' + version : ''}.${fmt}`);
    }

    async _downloadBlob(response, filename) {
        const cd = response.headers.get('content-disposition') || '';
        // Starlette sends filename*=utf-8''… whenever the name is non-ASCII, so a
        // regex that only knows filename="…" silently loses every Cyrillic name.
        const star = cd.match(/filename\*=\s*utf-8''([^;]+)/i);
        const plain = cd.match(/filename="?([^";]+)"?/i);
        let name = filename;
        if (star) {
            try { name = decodeURIComponent(star[1].trim()); } catch (e) { /* keep fallback */ }
        } else if (plain) {
            name = plain[1].trim();
        }
        const blob = await response.blob();
        const u = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = u; a.download = name;
        document.body.appendChild(a); a.click();
        window.URL.revokeObjectURL(u); document.body.removeChild(a);
    }

    // Reads a stored artifact file as text (for on-screen preview)
    async fetchText(meetingId, fileType, version = 0) {
        let url = `${API_BASE_URL}/meetings/${meetingId}/download/${fileType}`;
        if (version > 0) url += `?version=${version}`;
        const r = await fetch(url, { headers: this.getHeaders() });
        if (!r.ok) throw new Error('not found');
        return r.text();
    }

    // ========================================================================
    // Engines / models
    // ========================================================================

    async getEngines() {
        const r = await fetch(`${API_BASE_URL}/engines/`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async downloadModel(engine, model) {
        const r = await fetch(`${API_BASE_URL}/engines/${engine}/models/${encodeURIComponent(model)}/download`, { method: 'POST', headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    async modelDownloads() {
        const r = await fetch(`${API_BASE_URL}/engines/downloads`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    // ========================================================================
    // RAG + search
    // ========================================================================

    async ragAdd(meetingId) {
        const r = await fetch(`${API_BASE_URL}/rag/meetings/${meetingId}`, { method: 'POST', headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async ragSearch(q, project = '', topK = 5) {
        const r = await fetch(`${API_BASE_URL}/rag/search?q=${encodeURIComponent(q)}&project=${encodeURIComponent(project)}&top_k=${topK}`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    // List what is actually indexed, and drop a single document again. The
    // desktop RAG dialog has had both since day one; without them the web
    // cabinet could add to the knowledge base but never inspect or prune it.
    async ragLibrary(project = '') {
        const r = await fetch(`${API_BASE_URL}/rag/library?project=${encodeURIComponent(project)}`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async ragDelete(meetingId) {
        const r = await fetch(`${API_BASE_URL}/rag/meetings/${meetingId}`, { method: 'DELETE', headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async getTranscript(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/transcript`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async saveTranscript(meetingId, text) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/transcript`, {
            method: 'PUT', headers: this.getHeaders(), body: JSON.stringify({ text })
        });
        return this.handleResponse(r);
    }
    async cancelMeeting(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/cancel`, {
            method: 'POST', headers: this.getHeaders()
        });
        return this.handleResponse(r);
    }
    async processMeeting(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/process`, {
            method: 'POST', headers: this.getHeaders()
        });
        return this.handleResponse(r);
    }
    async meetingWaveform(meetingId, buckets = 900) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/waveform?buckets=${buckets}`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async cutSegments(meetingId, segments) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/segments`, {
            method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ segments })
        });
        return this.handleResponse(r);
    }
    async meetingStats() {
        const r = await fetch(`${API_BASE_URL}/meetings/stats`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async ragStats() {
        const r = await fetch(`${API_BASE_URL}/rag/stats`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async textSearch(q, regex = false, caseSensitive = false) {
        const r = await fetch(`${API_BASE_URL}/rag/textsearch?q=${encodeURIComponent(q)}&regex=${regex}&case=${caseSensitive}`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }

    // ========================================================================
    // Templates
    // ========================================================================

    async listTemplates(lang = 'ru', speaker = false) {
        const r = await fetch(`${API_BASE_URL}/templates/?lang=${lang}&speaker=${speaker}`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async createTemplate(name, prompt) {
        const r = await fetch(`${API_BASE_URL}/templates/`, { method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ name, prompt }) });
        return this.handleResponse(r);
    }
    // Edit a saved template in place. Without this the web cabinet could only
    // create and delete, so "fix a typo in my prompt" meant delete + retype -
    // the desktop's Manage-templates dialog has always edited in place.
    async updateTemplate(id, name, prompt) {
        const r = await fetch(`${API_BASE_URL}/templates/${id}`, { method: 'PUT', headers: this.getHeaders(), body: JSON.stringify({ name, prompt }) });
        return this.handleResponse(r);
    }
    async deleteTemplate(id) {
        const r = await fetch(`${API_BASE_URL}/templates/${id}`, { method: 'DELETE', headers: this.getHeaders() });
        if (r.status !== 204) throw new Error('delete failed');
    }

    // ========================================================================
    // Speakers
    // ========================================================================

    async getSpeakers(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/speakers`, { headers: this.getHeaders() });
        return this.handleResponse(r);
    }
    async renameSpeakers(meetingId, nameMap) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/speakers/rename`, { method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ name_map: nameMap }) });
        return this.handleResponse(r);
    }
    async exportBySpeaker(meetingId) {
        const r = await fetch(`${API_BASE_URL}/meetings/${meetingId}/export-by-speaker`, { headers: this.getHeaders() });
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || 'export failed'); }
        await this._downloadBlob(r, `by_speaker.zip`);
    }
}

// Экспортируем глобальный экземпляр
window.api = new APIClient();

// Link web footers to the same package version used by the API and desktop
// application instead of maintaining an independent hard-coded number.
fetch('/api/info')
    .then((response) => response.ok ? response.json() : null)
    .then((info) => {
        if (!info || !info.version) return;
        document.querySelectorAll('[data-app-version]').forEach((node) => {
            node.textContent = info.version;
        });
    })
    .catch(() => {
        // Version display is non-critical; the rest of the UI stays usable.
    });
