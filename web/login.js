// Login page. On first launch, when no account exists yet, it creates the single account instead.
const $ = (selector) => document.querySelector(selector);
let mode = "login";

// Where to go once signed in: a path on this site only, never another origin.
function nextPage() {
  const next = new URLSearchParams(location.search).get("next") || "/";
  const page = next.startsWith("/") && !next.startsWith("//") && !next.includes("\\") ? next : "/";
  // The view asked for before logging in (#tag=…) stays with the address through the redirection.
  return page.includes("#") ? page : page + location.hash;
}

function render() {
  const setup = mode === "setup";
  $("#auth-title").textContent = t(setup ? "auth.setupTitle" : "auth.loginTitle");
  $("#auth-submit").textContent = t(setup ? "auth.create" : "auth.login");
  $("#auth-text").hidden = !setup;
  $("#password-hint").hidden = !setup;
  $("#confirm-field").hidden = !setup;
  $("#confirm").required = setup;
  $("#password").autocomplete = setup ? "new-password" : "current-password";
}

function showError(message) {
  $("#auth-error").textContent = message;
  $("#auth-error").hidden = false;
}

async function start() {
  try {
    const response = await fetch("/api/auth", { headers: { "X-Language": currentLanguage } });
    const state = await response.json();
    if (state.signed_in) return location.replace(nextPage());
    mode = state.configured ? "login" : "setup";
    render();
    $("#auth-form").hidden = false;
    $("#username").focus();
  } catch {
    $("#auth-form").before($("#auth-error"));
    showError(t("auth.unreachable"));
  }
}

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#auth-error").hidden = true;
  if (mode === "setup" && $("#password").value !== $("#confirm").value) return showError(t("auth.mismatch"));
  const body = { username: $("#username").value, password: $("#password").value };
  $("#auth-submit").disabled = true;
  try {
    const response = await fetch(`/api/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Language": currentLanguage },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) return location.replace(nextPage());
    // The account was created or deleted in the meantime: show the matching form.
    if (response.status === 409) await start();
    showError(data.error || t("api.error", { status: response.status }));
  } catch {
    showError(t("auth.unreachable"));
  } finally {
    $("#auth-submit").disabled = false;
  }
});

$("#language").value = currentLanguage;
$("#language").addEventListener("change", (event) => { setLanguage(event.target.value); render(); });
applyTranslations();
start();
