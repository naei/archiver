// The popup opens on a click on the extension's icon and sends the tab's address at once.
function show(message, { state = "", page = "", connect = false } = {}) {
  document.body.dataset.state = state;
  $("#message").textContent = message;
  $("#page").textContent = page;
  $("#page").hidden = !page;
  $("#connect").hidden = !connect;
  $("#open").hidden = connect || state === "busy";
  $("#settings").hidden = connect || state === "busy";
}

async function archive() {
  const { server, token } = await connection();
  if (!server || !token) return show(t("notConnected"), { connect: true });
  $("#open").onclick = () => chrome.tabs.create({ url: server });
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:\/\//.test(tab.url || "")) return show(t("unsupported"), { state: "error" });
  show(t("sending"), { state: "busy" });
  let answer;
  try {
    answer = await call(server, "/api/media", { token, data: { url: tab.url } });
  } catch {
    return show(t("unreachable"), { state: "error" });
  }
  const { status, body } = answer;
  if (status === 401) {
    await chrome.storage.local.remove("token");
    return show(t("expired"), { connect: true });
  }
  if (status !== 200 && status !== 201) return show(body.error || t("failed"), { state: "error" });
  show(t(body.created ? "added" : body.restored ? "restored" : "already"), { state: "done", page: tab.title || tab.url });
}

localize();
$("#connect").onclick = $("#settings").onclick = () => chrome.runtime.openOptionsPage();
archive();
