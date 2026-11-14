// Settings page: log in once to the user's Archiver, or disconnect.
async function render() {
  const { server, token } = await connection();
  const connected = Boolean(server && token);
  $("#connected").hidden = !connected;
  $("#form").hidden = connected;
  if (connected) $("#connected-text").textContent = t("connectedText", new URL(server).host);
  else if (server) $("#server").value = server;
}

function fail(message) {
  $("#error").textContent = message;
  $("#error").hidden = false;
  $("#submit").disabled = false;
  $("#submit").textContent = t("connect");
}

// The server's origin from what was typed; "archiver.example.com" alone means https.
function origin(value) {
  try {
    const url = new URL(/^https?:\/\//i.test(value.trim()) ? value.trim() : "https://" + value.trim());
    return url.hostname ? url.origin : null;
  } catch {
    return null;
  }
}

// What Chrome is asked to let the extension reach: that server only. Its patterns name no port.
const pattern = (server) => `${new URL(server).protocol}//${new URL(server).hostname}/*`;

$("#form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#error").hidden = true;
  const server = origin($("#server").value);
  if (!server) return fail(t("invalidAddress"));
  // Asked first, while Chrome still ties the request to the click; only this address is asked for.
  const granted = await chrome.permissions.request({ origins: [pattern(server)] }).catch(() => false);
  if (!granted) return fail(t("permissionRefused"));
  $("#submit").disabled = true;
  $("#submit").textContent = t("connecting");
  let answer;
  try {
    answer = await call(server, "/api/auth/login",
      { data: { username: $("#username").value, password: $("#password").value, token: true } });
  } catch {
    return fail(t("unreachable"));
  }
  if (answer.status !== 200 || !answer.body.token) return fail(answer.body.error || t("unreachable"));
  await chrome.storage.local.set({ server, token: answer.body.token });
  $("#password").value = "";
  $("#submit").disabled = false;
  $("#submit").textContent = t("connect");
  render();
});

$("#disconnect").addEventListener("click", async () => {
  const { server, token } = await connection();
  // Close the session on the server too; it is forgotten here even if the server cannot be reached.
  await call(server, "/api/auth/logout", { token, data: {} }).catch(() => {});
  await chrome.storage.local.remove("token");
  await chrome.permissions.remove({ origins: [pattern(server)] }).catch(() => {});
  render();
});

localize();
render();
