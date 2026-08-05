/**
 * Authentication page logic
 */

document.addEventListener('DOMContentLoaded', () => {
    // Применяем переводы
    i18n.updatePage();

    // Настройка переключателей
    const langToggle = document.getElementById('langToggle');
    const themeToggle = document.getElementById('themeToggle');
    const langIcon = document.getElementById('langIcon');
    const themeIcon = document.getElementById('themeIcon');

    // Устанавливаем начальные значения
    langIcon.textContent = i18n.getCurrentLanguage().toUpperCase();
    themeIcon.textContent = themeManager.getCurrentTheme() === 'light' ? '🌙' : '☀️';

    langToggle.addEventListener('click', () => {
        const newLang = i18n.getCurrentLanguage() === 'en' ? 'ru' : 'en';
        i18n.setLanguage(newLang);
        langIcon.textContent = newLang.toUpperCase();
    });

    themeToggle.addEventListener('click', () => {
        const newTheme = themeManager.toggleTheme();
        themeIcon.textContent = newTheme === 'light' ? '🌙' : '☀️';
    });

    // Если уже авторизован, редирект на дашборд
    if (api.isAuthenticated()) {
        window.location.href = '/dashboard.html';
        return;
    }

    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const showRegisterLink = document.getElementById('showRegister');
    const showLoginLink = document.getElementById('showLogin');
    const loginFormElement = document.getElementById('loginFormElement');
    const registerFormElement = document.getElementById('registerFormElement');
    const errorMessage = document.getElementById('errorMessage');

    // Переключение между формами
    showRegisterLink.addEventListener('click', (e) => {
        e.preventDefault();
        loginForm.style.display = 'none';
        registerForm.style.display = 'block';
        errorMessage.style.display = 'none';
    });

    showLoginLink.addEventListener('click', (e) => {
        e.preventDefault();
        registerForm.style.display = 'none';
        loginForm.style.display = 'block';
        errorMessage.style.display = 'none';
    });

    // Обработка входа
    loginFormElement.addEventListener('submit', async (e) => {
        e.preventDefault();

        const username = document.getElementById('loginUsername').value;
        const password = document.getElementById('loginPassword').value;

        try {
            await api.login(username, password);
            window.location.href = '/dashboard.html';
        } catch (error) {
            // The server answers 401 in English; say it in the user's language.
            showError(error.status === 401
                ? i18n.t('auth.invalidCredentials') : error.message);
        }
    });

    // Обработка регистрации
    registerFormElement.addEventListener('submit', async (e) => {
        e.preventDefault();

        const username = document.getElementById('registerUsername').value;
        const email = document.getElementById('registerEmail').value;
        const password = document.getElementById('registerPassword').value;
        const passwordConfirm = document.getElementById('registerPasswordConfirm').value;

        if (password !== passwordConfirm) {
            showError(i18n.t('auth.passwordMismatch'));
            return;
        }

        try {
            await api.register(username, email, password);
            // После регистрации автоматически логинимся
            await api.login(username, password);
            window.location.href = '/dashboard.html';
        } catch (error) {
            showError(i18n.serverMessage(error.message));
        }
    });

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.style.display = 'block';
    }
});
