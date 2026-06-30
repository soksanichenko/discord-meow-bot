function setLang(lang) {
    localStorage.setItem('lang', lang);
    document.documentElement.lang = lang;
    render(lang);
}

function detectLang() {
    const supported = ['en', 'uk', 'ru'];
    for (const locale of navigator.languages || [navigator.language]) {
        const tag = locale.split('-')[0].toLowerCase();
        if (supported.includes(tag)) return tag;
    }
    return 'en';
}

(function init() {
    const saved = localStorage.getItem('lang') || detectLang();
    document.getElementById('lang-select').value = saved;
    setLang(saved);
})();
