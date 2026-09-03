// Behavioural test of the 401 policy: runs the REAL api.js in a sandbox.
const fs = require('fs');
const vm = require('vm');

const SRC = fs.readFileSync(process.argv[2], 'utf8');

function makeContext(pathname, response) {
    const state = { redirected: null, store: { token: 'existing' } };
    const location = {
        origin: 'http://127.0.0.1:8000',
        pathname,
        get href() { return 'http://127.0.0.1:8000' + pathname; },
        set href(value) { state.redirected = value; },
    };
    const localStorage = {
        getItem: (k) => (k in state.store ? state.store[k] : null),
        setItem: (k, v) => { state.store[k] = v; },
        removeItem: (k) => { delete state.store[k]; },
    };
    const ctx = {
        console,
        localStorage,
        window: { location, localStorage, URL: { createObjectURL: () => '', revokeObjectURL: () => {} } },
        document: { addEventListener: () => {}, querySelectorAll: () => [], createElement: () => ({ style: {}, click: () => {}, remove: () => {} }), body: { appendChild: () => {} } },
        fetch: async () => response,
        setTimeout,
    };
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    vm.runInContext(SRC, ctx, { filename: 'api.js' });
    return { api: ctx.window.api, state };
}

const unauthorized = {
    status: 401, ok: false,
    json: async () => ({ detail: 'Incorrect username or password' }),
};

(async () => {
    const out = [];

    // 1. A failed SIGN-IN must not navigate: that reload wiped the error message.
    {
        const { api, state } = makeContext('/', unauthorized);
        let err = null;
        try { await api.login('747', 'wrong'); } catch (e) { err = e; }
        out.push(['login_401_does_not_redirect', state.redirected === null, String(state.redirected)]);
        out.push(['login_401_throws_with_status', !!err && err.status === 401, err ? `${err.message} status=${err.status}` : 'no error']);
        out.push(['login_401_keeps_the_detail', !!err && /Incorrect username/.test(err.message), err ? err.message : '']);
    }

    // 2. An expired session on a real page MUST still bounce to the login page.
    {
        const { api, state } = makeContext('/dashboard.html', unauthorized);
        try { await api.getStatus(1); } catch (e) { /* expected */ }
        out.push(['expired_session_redirects_from_dashboard', state.redirected === '/', String(state.redirected)]);
        out.push(['expired_session_clears_the_token', localStorageEmptied(state), JSON.stringify(state.store)]);
    }

    // 3. On the login page itself, a 401 must never trigger a reload loop.
    {
        const { api, state } = makeContext('/index.html', unauthorized);
        try { await api.getStatus(1); } catch (e) { /* expected */ }
        out.push(['no_reload_loop_on_the_login_page', state.redirected === null, String(state.redirected)]);
    }

    function localStorageEmptied(state) { return !('token' in state.store); }

    console.log(JSON.stringify(out));
})();
