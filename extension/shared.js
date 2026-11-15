// Shared by the popup and the settings page: texts, saved connection, calls to the server.
const $ = (selector) => document.querySelector(selector);
const t = (key, ...values) => chrome.i18n.getMessage(key, values);

// Fill the page's texts in the browser's language.
function localize() {
  document.documentElement.lang = chrome.i18n.getUILanguage();
  for (const element of document.querySelectorAll("[data-t]")) element.textContent = t(element.dataset.t);
}

// The server's address and the session token it gave at login; never the password.
const connection = () => chrome.storage.local.get(["server", "token"]);

// A JSON request to the server; it throws when the server cannot be reached. The browser's own Archiver session
// is left out: the extension has its own.
async function call(server, path, { token, data } = {}) {
  const response = await fetch(server + path, {
    method: data ? "POST" : "GET",
    credentials: "omit",
    headers: {
      "Content-Type": "application/json",
      "X-Language": chrome.i18n.getUILanguage().startsWith("fr") ? "fr" : "en",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    // Not Archiver, or a proxy's error page: the status alone is used.
  }
  return { status: response.status, body };
}
