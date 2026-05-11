(function() {
    const storageKey = 'musicStoreTheme';
    const root = document.documentElement;

    function preferredTheme() {
        const saved = localStorage.getItem(storageKey);
        if (saved === 'light' || saved === 'dark') return saved;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    function applyTheme(theme) {
        root.dataset.theme = theme;
        localStorage.setItem(storageKey, theme);

        const toggle = document.getElementById('theme-toggle');
        if (toggle) {
            toggle.setAttribute('aria-label', theme === 'dark' ? 'Включить светлую тему' : 'Включить темную тему');
            toggle.setAttribute('title', theme === 'dark' ? 'Светлая тема' : 'Темная тема');
        }
    }

    applyTheme(preferredTheme());

    document.addEventListener('DOMContentLoaded', function() {
        const toggle = document.getElementById('theme-toggle');
        if (!toggle) return;

        toggle.addEventListener('click', function() {
            applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark');
        });
    });
})();
