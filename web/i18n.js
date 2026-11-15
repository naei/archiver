// Interface strings. Each language is a file of web/locales, named after its code (fr.json): the server puts them
// all in the table below when it sends this file, English first. To add a language, copy en.json under the new
// code and translate its values, starting with the language's own name; texts it lacks show in English.
// The choice is kept in localStorage.
const TRANSLATIONS = /*__LOCALES__*/{};

function detectLanguage() {
  try {
    const saved = localStorage.getItem("language");
    if (saved && TRANSLATIONS[saved]) return saved;
  } catch { /* storage unavailable */ }
  return "en";
}

let currentLanguage = detectLanguage();

// The language menus list every language there is, under its own name.
document.querySelectorAll("[data-languages]").forEach((list) => {
  list.insertAdjacentHTML("beforeend", Object.entries(TRANSLATIONS).map(([code, texts]) => {
    const name = String(texts["language.name"] || code).replace(/[&<>"]/g, "");
    return list.tagName === "SELECT" ? `<option value="${code}">${name}</option>`
      : `<button role="menuitemradio" type="button" data-language="${code}" aria-checked="false"><span class="menu-check" data-icon="check"></span>${name}</button>`;
  }).join(""));
});

// t("key", {name: value}) fills {name} placeholders; "(s)" marks are resolved from a `count`-like number,
// by the language's own rule: plural from 2 in French, for anything but 1 in English ("0 archives").
function t(key, params = {}) {
  let text = TRANSLATIONS[currentLanguage]?.[key] ?? TRANSLATIONS.en?.[key] ?? key;
  for (const [name, value] of Object.entries(params)) text = text.split(`{${name}}`).join(String(value));
  const number = params.count;
  const plural = number !== undefined && new Intl.PluralRules(locale()).select(Number(number)) !== "one";
  if (number !== undefined) text = text.replace(/\(s\)/g, plural ? "s" : "");
  return text;
}

function applyTranslations() {
  document.documentElement.lang = currentLanguage;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
}

function setLanguage(language) {
  if (!TRANSLATIONS[language]) return;
  currentLanguage = language;
  try { localStorage.setItem("language", language); } catch { /* per-visit only */ }
  applyTranslations();
}

function locale() {
  return TRANSLATIONS[currentLanguage]?.["language.locale"] || currentLanguage;
}
