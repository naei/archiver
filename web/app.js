// The library's interface: talks to the server, polls it and applies every action. The rendering is in render.js.
let refreshTask = null;
let refreshAgain = false;
let version = 0;
let passStart = 0; // the library's latest change when it was last read from the start
let startOver = false; // the next page replaces the library instead of updating it
let forgetMissing = false; // archives left the library: once it is read, what is still shown of them is closed
let syncedWith = ""; // the language and unlock token the library was synced with
let pollTimer;
// While private archives are shown: the token that unlocks them. It lives only in this page, so a reload hides them.
let unlockToken = null;
const syncKey = () => `${currentLanguage}|${unlockToken || ""}`;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Language": currentLanguage,
      ...(unlockToken ? { "X-Private-Unlock": unlockToken } : {}), ...options.headers },
  });
  // The session expired or was closed elsewhere: log in again, then come back.
  if (response.status === 401) location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || t("api.error", { status: response.status }));
  return data;
}

async function saveNote() {
  const input = $("#note-input");
  const item = currentItem();
  if (!input || !item || input.value.trim() === (item.note || "")) return;
  $("#note-status").textContent = t("detail.noteSaving");
  try {
    const updated = await api(`/api/media/${item.id}`, { method: "PATCH", body: JSON.stringify({ note: input.value }) });
    if (!upsert(updated)) return;
    if ($("#note-status")) $("#note-status").textContent = t("detail.noteSaved");
  } catch (error) {
    if ($("#note-status")) $("#note-status").textContent = error.message;
  }
}

// The personal title of an archive, asked in a window opened from its menu or from the pencil next to its title.
let titleDialogId = null;

function openTitleDialog(id) {
  const item = state.items.find((other) => other.id === id);
  if (!item) return;
  titleDialogId = id;
  $("#title-dialog-text").textContent = t("titleDialog.text", { title: item.title });
  $("#title-dialog-input").value = item.custom_title || "";
  $("#title-dialog-input").placeholder = item.title;
  $("#title-dialog-error").hidden = true;
  $("#title-dialog-reset").hidden = !item.custom_title;
  $("#title-dialog").showModal();
  $("#title-dialog-input").select();
}

async function applyTitleDialog() {
  const item = state.items.find((other) => other.id === titleDialogId);
  if (!item) { $("#title-dialog").close(); return; }
  $("#title-dialog-confirm").disabled = true;
  try {
    const updated = await api(`/api/media/${item.id}`, { method: "PATCH", body: JSON.stringify({ custom_title: $("#title-dialog-input").value }) });
    if (!upsert(updated)) return;
    $("#title-dialog").close();
    render();
    // Updated in place: redrawing the open archive would interrupt its video or a note being typed.
    if (state.currentId === updated.id && $("#detail-title")) {
      detailSignature = detailSignatureOf(updated);
      $("#detail-title").textContent = titleOf(updated);
      $("#original-title").hidden = !updated.custom_title;
      $("#original-title span").textContent = updated.title;
    }
  } catch (error) {
    $("#title-dialog-error").textContent = error.message;
    $("#title-dialog-error").hidden = false;
  } finally {
    $("#title-dialog-confirm").disabled = false;
  }
}

// Submitting the form covers Enter, without acting on the Enter that confirms a composed character.
$("#title-dialog-form").addEventListener("submit", (event) => { event.preventDefault(); applyTitleDialog(); });
$("#title-dialog-cancel").addEventListener("click", () => $("#title-dialog").close());
$("#title-dialog-reset").addEventListener("click", () => { $("#title-dialog-input").value = ""; applyTitleDialog(); });

// After the visible library is complete, release references to archives it no longer contains.
function forgetMissingArchives() {
  const known = new Set(state.items.map((item) => item.id));
  for (const ids of [state.selected, articleCache, state.articleTexts]) {
    for (const id of [...ids.keys()]) if (!known.has(id)) ids.delete(id);
  }
  if ($("#detail-dialog").open && !known.has(state.currentId)) $("#detail-dialog").close();
  if (titleDialogId && !known.has(titleDialogId)) {
    $("#title-dialog").close();
    titleDialogId = null;
  }
  if (!$("#player").hidden && player.items.some((item) => !known.has(item.id))) closePlayer();
  state.tags = existingTags();
}

function refresh() {
  if (refreshTask) {
    refreshAgain = true;
    return refreshTask;
  }
  refreshTask = syncLibrary().finally(() => { refreshTask = null; schedule(); });
  return refreshTask;
}

async function syncLibrary() {
  try {
    do {
      refreshAgain = false;
      // Another language, or private archives just shown: the whole library is read again.
      if (syncedWith !== syncKey()) { version = 0; syncedWith = syncKey(); }
      let more;
      do {
        const key = syncKey();
        const token = unlockToken;
        const data = await api(`/api/media/changes?since=${version}&limit=100`);
        if (key !== syncKey()) { refreshAgain = true; break; }
        if (data.reset || startOver) { state.items = []; forgetMissing = true; }
        if (data.reset || startOver || version === 0) passStart = data.latest;
        startOver = false;
        const removed = new Set(data.removed);
        const changed = new Map(data.items.map((item) => [item.id, item]));
        state.items = state.items.filter((item) => !removed.has(item.id)).map((item) => {
          const update = changed.get(item.id);
          changed.delete(item.id);
          return update || item;
        });
        state.items.push(...changed.values());
        removed.forEach((id) => state.selected.delete(id));
        if (removed.size) forgetMissing = true;
        version = data.cursor;
        // An archive shown here may have become private. The server does not say which: all is read again.
        if (data.hidden_since != null && data.hidden_since >= passStart) { version = 0; startOver = true; }
        // The token expired (a while without requests, or a server restart): private archives are no longer sent.
        if (token && !data.unlocked) lockPrivate("expired");
        // A web page or a subtitled video just archived: its text joins the search inside articles and subtitles.
        if (state.fullText && data.items.some((item) => (isArticle(item) || item.details?.subtitles?.length) && item.status === "ready" && !state.articleTexts.has(item.id))) loadArticleTexts();
        const wasSyncing = state.syncing;
        more = data.has_more || startOver;
        state.syncing = more;
        state.syncTotal = data.total;
        const first = !state.loaded;
        state.loaded = true;
        if (!more && forgetMissing) {
          forgetMissing = false;
          forgetMissingArchives();
        }
        if (first || data.reset || startOver || data.items.length || removed.size || wasSyncing !== more) render();
        if (!more) showWanted();
        const item = currentItem();
        if ($("#detail-dialog").open && item) {
          const signature = detailSignatureOf(item);
          // Do not replace a playing video or an in-progress edit during polling.
          if (signature !== detailSignature && !["new-tag", "note-input"].includes(document.activeElement?.id)) renderDetail(item);
          if ($("#detail-status")) $("#detail-status").textContent = `${statusName(item.status)}${item.status === "downloading" ? ` — ${Math.round(item.progress)} %` : ""}`;
        }
      } while (more);
    } while (refreshAgain);
    $("#connection-warning").hidden = true;
  } catch {
    $("#connection-warning").hidden = false;
  }
}

// Every 2.5 s while archives are in progress, otherwise every 30 s; at once when the tab comes back.
function schedule() {
  clearTimeout(pollTimer);
  const busy = state.items.some((item) => activeStates.has(item.status));
  pollTimer = setTimeout(() => (document.hidden ? schedule() : refresh()), busy ? 2500 : 30000);
}

// The tags given with a link, shown as chips under the form so the form keeps its height: a comma, Enter or a
// suggestion turns what is typed into a chip; Backspace in the empty field removes the last one.
let addTags = [];

function renderAddTags() {
  $("#add-tag-chips").innerHTML = addTags.map((tag, index) =>
    `<span class="tag-chip"># ${escapeHTML(tag)}<button class="tag-remove" type="button" data-remove-add-tag="${index}" aria-label="${escapeHTML(t("detail.removeTag", { tag }))}">${icon("close")}</button></span>`).join("");
  $("#add-tag-chips").hidden = !addTags.length;
}

function addTypedTags(text) {
  text.split(",").map((tag) => tag.trim()).filter(Boolean).forEach((tag) => {
    if (!addTags.some((other) => fold(other) === fold(tag))) addTags.push(tag);
  });
  renderAddTags();
}

function clearAddTags() {
  addTags = [];
  $("#add-tags").value = "";
  renderAddTags();
}

$("#add-tags").addEventListener("keydown", (event) => {
  const input = event.target;
  if (event.defaultPrevented) return; // a suggestion was chosen
  if ((event.key === "," || event.key === "Enter") && input.value.trim()) {
    event.preventDefault();
    addTypedTags(input.value);
    input.value = "";
    showSuggestions(input);
  } else if (event.key === "Backspace" && !input.value && addTags.length) {
    addTags.pop();
    renderAddTags();
    showSuggestions(input);
  }
});
// Pasted text with commas becomes chips at once.
$("#add-tags").addEventListener("input", (event) => {
  const input = event.target;
  if (!input.value.includes(",")) return;
  const rest = input.value.slice(input.value.lastIndexOf(",") + 1);
  addTypedTags(input.value.slice(0, input.value.lastIndexOf(",")));
  input.value = rest.trimStart();
  showSuggestions(input);
});
$("#add-tag-chips").addEventListener("click", (event) => {
  const remove = event.target.closest("[data-remove-add-tag]");
  if (!remove) return;
  addTags.splice(Number(remove.dataset.removeAddTag), 1);
  renderAddTags();
  $("#add-tags").focus();
});

// Suggestions under a tag field ([data-tag-suggest]), once something is typed: the existing tags that match it,
// most used first, leaving out those the archive or the form already has.
const suggestion = { input: null, list: null, tags: [], active: -1 };

function suggestedTags(input) {
  const typing = fold(input.value.trim());
  if (!typing) return [];
  const skip = new Set();
  if (input.id === "new-tag") currentItem()?.tags.forEach((tag) => skip.add(fold(tagLabel(tag))));
  if (input.id === "add-tags") addTags.forEach((tag) => skip.add(fold(tag)));
  return [...tagCounts()]
    .map(([tag, count]) => ({ tag, count, label: tagLabel(tag), folded: fold(tagLabel(tag)) }))
    .filter(({ folded }) => !skip.has(folded) && folded !== typing && folded.includes(typing))
    .sort((a, b) => b.folded.startsWith(typing) - a.folded.startsWith(typing) || b.count - a.count || a.label.localeCompare(b.label, locale()))
    .slice(0, 8);
}

function showSuggestions(input) {
  const tags = suggestedTags(input);
  if (!tags.length) { hideSuggestions(); return; }
  if (suggestion.input !== input) hideSuggestions();
  let list = suggestion.list;
  if (!list) {
    list = document.createElement("ul");
    list.className = "tag-suggestions";
    list.id = `${input.id}-suggestions`;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", t("tags.heading"));
    // Choosing with the pointer keeps the focus in the field.
    list.addEventListener("mousedown", (event) => event.preventDefault());
    list.addEventListener("click", (event) => {
      const option = event.target.closest("[data-suggestion]");
      if (option) acceptSuggestion(Number(option.dataset.suggestion));
    });
    input.after(list);
    input.setAttribute("aria-controls", list.id);
  }
  Object.assign(suggestion, { input, list, tags, active: -1 });
  list.innerHTML = tags.map(({ label, count }, index) =>
    `<li role="option" id="${list.id}-${index}" data-suggestion="${index}" aria-selected="false"><span class="tag-name"># ${escapeHTML(label)}</span><span class="count">${count}</span></li>`).join("");
  input.setAttribute("aria-expanded", "true");
  input.removeAttribute("aria-activedescendant");
}

function hideSuggestions() {
  suggestion.list?.remove();
  suggestion.input?.setAttribute("aria-expanded", "false");
  suggestion.input?.removeAttribute("aria-activedescendant");
  Object.assign(suggestion, { input: null, list: null, tags: [], active: -1 });
}

function moveSuggestion(step) {
  // From the field (-1) through each suggestion and back to the field.
  const count = suggestion.tags.length;
  const next = suggestion.active + step;
  suggestion.active = next >= count ? -1 : next < -1 ? count - 1 : next;
  suggestion.list.querySelectorAll("[role=option]").forEach((option, index) => option.setAttribute("aria-selected", String(index === suggestion.active)));
  if (suggestion.active < 0) suggestion.input.removeAttribute("aria-activedescendant");
  else {
    const option = suggestion.list.children[suggestion.active];
    suggestion.input.setAttribute("aria-activedescendant", option.id);
    option.scrollIntoView({ block: "nearest" });
  }
}

function acceptSuggestion(index) {
  const { input, tags } = suggestion;
  const { label } = tags[index];
  if (input.id === "add-tags") {
    input.value = "";
    addTypedTags(label);
    showSuggestions(input);
  } else {
    input.value = label;
    hideSuggestions();
  }
  input.focus();
}

document.addEventListener("input", (event) => { if (event.target.matches?.("[data-tag-suggest]")) showSuggestions(event.target); });
document.addEventListener("focusout", (event) => { if (event.target === suggestion.input) hideSuggestions(); });
// Captured, so Escape closes the suggestions before it closes the dialog around them.
document.addEventListener("keydown", (event) => {
  if (event.target !== suggestion.input || !suggestion.list) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); moveSuggestion(event.key === "ArrowDown" ? 1 : -1); }
  else if (event.key === "Enter" && suggestion.active >= 0) { event.preventDefault(); acceptSuggestion(suggestion.active); }
  else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); hideSuggestions(); }
}, true);

// Archiving a link with its tags; a link already archived is only archived again once confirmed.
async function addLink(url, again = false, typedTags = []) {
  const result = await api("/api/media", { method: "POST", body: JSON.stringify({ url, again, tags: typedTags }) });
  // The server says nothing more of an archive that is private while private archives are hidden.
  if (result.private) { toast(t(result.created ? "add.private" : "add.known")); return; }
  const { item, created, restored, occurrences } = result;
  if (!upsert(item)) return;
  schedule();
  state.loaded = true;
  setView("all");
  if (created) toast(t(isPrivate(item) ? "add.private" : "add.queued"));
  else if (restored) { toast(t("add.restored")); openDetail(item.id); }
  else showDuplicate(url, item, occurrences, typedTags);
}

function showDuplicate(url, item, occurrences, tags) {
  state.duplicate = { url, id: item.id, tags };
  const busy = occurrences.some((other) => activeStates.has(other.status));
  const date = new Date(occurrences[0].created_at).toLocaleString(locale(), { dateStyle: "long", timeStyle: "short" });
  $("#duplicate-text").textContent = t(occurrences.length > 1 ? "duplicate.textMany" : "duplicate.text", { date, count: occurrences.length });
  $("#duplicate-hint").textContent = t(busy ? "duplicate.busy" : "duplicate.hint");
  $("#duplicate-again").hidden = busy;
  $("#duplicate-dialog").showModal();
  $("#duplicate-cancel").focus();
}

// A link that ends like a file is archived as that file, which says little about itself: said before archiving.
const FILE_LINK = /\.(jpe?g|png|gif|webp|avif|mp4|webm|mov|mkv|m4v|ogv|mp3|m4a|aac|ogg|opus|wav|flac|weba|pdf)$/i;

function showFileHint() {
  let path = "";
  try { path = new URL(/^https?:\/\//i.test($("#url").value.trim()) ? $("#url").value.trim() : `https://${$("#url").value.trim()}`).pathname; } catch { /* not a link yet */ }
  $("#url-hint").hidden = !FILE_LINK.test(path);
}

$("#url").addEventListener("input", showFileHint);

$("#archive-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#archive-button");
  button.disabled = true;
  $("#url-error").hidden = true;
  try {
    // What is still being typed counts too.
    await addLink($("#url").value.trim(), false, [...addTags, ...$("#add-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean)]);
    $("#url").value = "";
    showFileHint();
    clearAddTags();
  } catch (error) {
    $("#url-error").textContent = error.message;
    $("#url-error").hidden = false;
  } finally {
    button.disabled = false;
  }
});

$("#duplicate-cancel").addEventListener("click", () => $("#duplicate-dialog").close());
$("#duplicate-view").addEventListener("click", () => {
  $("#duplicate-dialog").close();
  openDetail(state.duplicate.id);
});
$("#duplicate-again").addEventListener("click", async () => {
  $("#duplicate-again").disabled = true;
  try {
    $("#duplicate-dialog").close();
    await addLink(state.duplicate.url, true, state.duplicate.tags);
  } catch (error) { toast(error.message); }
  finally { $("#duplicate-again").disabled = false; }
});

// An archive's tags replaced; returns the archive, or nothing when it left the library by becoming private.
async function patchTags(item, tags) {
  const updated = await api(`/api/media/${item.id}`, { method: "PATCH", body: JSON.stringify({ tags }) });
  if (isPrivate(updated) && !state.unlocked) {
    // Made private while private archives are hidden: it leaves the library at once.
    state.items = state.items.filter((other) => other.id !== updated.id);
    forgetMissingArchives();
    render();
    toast(t("private.moved"));
    return null;
  }
  upsert(updated);
  if ($("#detail-dialog").open && state.currentId === updated.id) renderDetailTags(updated);
  render();
  return updated;
}

async function saveTags(tags) {
  const item = currentItem();
  if (!item) return;
  detailFeedback("");
  await patchTags(item, tags);
}

async function retryArchive(id) {
  const item = await api(`/api/media/${id}/retry`, { method: "POST", body: "{}" });
  if (!upsert(item)) return;
  if ($("#detail-dialog").open && state.currentId === item.id) renderDetail(item);
  else toast(t("add.queued"));
  render();
  schedule();
}

$("#detail-content").addEventListener("submit", async (event) => {
  if (event.target.id !== "tag-form") return;
  event.preventDefault();
  const input = $("#new-tag");
  const tag = input.value.trim();
  if (!tag) return;
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    await saveTags([...currentItem().tags, tag]);
    input.value = "";
    input.focus();
  } catch (error) { detailFeedback(error.message); }
  finally { button.disabled = false; }
});

$("#detail-content").addEventListener("click", async (event) => {
  if (event.target.closest("[data-edit-title]")) { openTitleDialog(state.currentId); return; }
  const remove = event.target.closest("[data-remove-tag]");
  const retry = event.target.closest("#retry-button");
  if (remove) {
    remove.disabled = true;
    try { await saveTags(currentItem().tags.filter((tag) => tag !== remove.dataset.removeTag)); }
    catch (error) { detailFeedback(error.message); remove.disabled = false; }
  }
  if (retry) {
    retry.disabled = true;
    try {
      await retryArchive(state.currentId);
    } catch (error) { detailFeedback(error.message); retry.disabled = false; }
  }
  if (event.target.closest("#delete-button")) deleteArchives([state.currentId]);
  if (event.target.closest("#restore-button")) restoreArchives([state.currentId]);
});
$("#detail-content").addEventListener("focusout", (event) => { if (event.target.id === "note-input") saveNote(); });
$("#detail-content").addEventListener("keydown", (event) => {
  if (event.target.id === "note-input" && event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); saveNote(); }
});

async function mutateSelection(action, ids, extra = {}, progress = null) {
  const total = {};
  // Keep each request below the API body limit, including long archive identifiers; smaller ones when progress is shown.
  const size = progress ? 50 : 200;
  for (let offset = 0; offset < ids.length; offset += size) {
    progress?.(offset);
    const result = await api("/api/media/batch", { method: "POST", body: JSON.stringify({ action, ids: ids.slice(offset, offset + size), ...extra }) });
    Object.entries(result).forEach(([key, value]) => { total[key] = (total[key] || 0) + value; });
  }
  return total;
}

async function batch(action, ids) {
  const result = await mutateSelection(action, ids);
  // What is left of a selection stays selected; without any, selecting ends.
  ids.forEach((id) => state.selected.delete(id));
  if (!state.selected.size) state.selecting = false;
  $("#detail-dialog").close();
  await refresh();
  return result;
}

// Deleting archives, always after a confirmation: to the trash, with an undo; from the trash, for good.
function deleteArchives(ids) {
  confirmDelete(ids, !ids.some((id) => state.items.find((item) => item.id === id)?.deleted_at));
}

async function trashArchives(ids) {
  try {
    const result = await batch("delete", ids);
    toast(t("trash.moved", { count: result.deleted }) + (result.skipped ? ` ${t("select.skipped", { count: result.skipped })}` : ""),
      { label: t("trash.undo"), run: () => restoreArchives(ids, false) });
  } catch (error) {
    if ($("#detail-dialog").open) detailFeedback(error.message); else toast(error.message);
  }
}

async function restoreArchives(ids, announce = true) {
  try {
    const result = await batch("restore", ids);
    if (announce) toast(t("trash.restored", { count: result.restored }));
  } catch (error) { toast(error.message); }
}

// `ids` moved to the trash, or with `trash` false deleted for good; the whole trash emptied when `ids` is null.
function confirmDelete(ids, trash = false) {
  ids = ids?.filter((id) => Boolean(state.items.find((item) => item.id === id)?.deleted_at) !== trash) ?? null;
  if (ids && !ids.length) return;
  state.pendingDelete = ids;
  state.pendingTrash = trash;
  const count = ids ? ids.length : state.items.filter((item) => item.deleted_at).length;
  $("#delete-title").textContent = t(trash ? "trash.moveTitle" : ids ? "trash.confirmTitle" : "trash.emptyTitle", { count });
  $("#delete-text").textContent = t(trash ? "trash.moveText" : "trash.confirmText", { count, days: TRASH_DAYS });
  $("#confirm-delete").textContent = t(trash ? "trash.moveConfirm" : "action.delete");
  $("#delete-dialog").showModal();
  $("#cancel-delete").focus();
}

$("#cancel-delete").addEventListener("click", () => $("#delete-dialog").close());
$("#confirm-delete").addEventListener("click", async () => {
  const ids = state.pendingDelete;
  if (state.pendingTrash) {
    $("#delete-dialog").close();
    state.pendingDelete = null;
    state.pendingTrash = false;
    trashArchives(ids);
    return;
  }
  $("#confirm-delete").disabled = true;
  try {
    const result = ids ? await batch("purge", ids) : await api("/api/trash/empty", { method: "POST", body: "{}" });
    $("#delete-dialog").close();
    if (!ids) await refresh();
    toast(t("trash.deleted", { count: result.deleted }));
  } catch (error) {
    $("#delete-dialog").close();
    if ($("#detail-dialog").open) detailFeedback(error.message); else toast(error.message);
  } finally {
    $("#confirm-delete").disabled = false;
    state.pendingDelete = null;
  }
});
$("#empty-trash").addEventListener("click", () => confirmDelete(null));

// Selecting several archives: a card's box, Ctrl or ⌘ + click, or the Select button. Shift extends the selection from
// the last archive chosen; taking the last one out ends it.
let selectionAnchor = null;

function toggleSelected(id, forced, range = false) {
  const selected = forced ?? !state.selected.has(id);
  const shown = state.visible.slice(0, state.shown).map((item) => item.id);
  const from = range ? shown.indexOf(selectionAnchor) : -1;
  const to = shown.indexOf(id);
  const ids = from >= 0 && to >= 0 ? shown.slice(Math.min(from, to), Math.max(from, to) + 1) : [id];
  ids.forEach((other) => { if (selected) state.selected.add(other); else state.selected.delete(other); });
  selectionAnchor = id;
  state.selecting = state.selected.size > 0;
  render();
}

function setSelecting(selecting) {
  state.selecting = selecting;
  if (!selecting) { state.selected.clear(); selectionAnchor = null; }
  render();
}

// On a wide screen the selection bar takes the toolbar's place: focus follows, and comes back when it closes.
$("#toggle-select").addEventListener("click", () => {
  setSelecting(!state.selecting);
  if (state.selecting && !$("#toggle-select").offsetParent) $("#selection-close").focus();
});
$("#selection-close").addEventListener("click", () => { setSelecting(false); $("#toggle-select").focus(); });
$("#select-all").addEventListener("click", () => {
  visibleItems().forEach((item) => state.selected.add(item.id));
  render();
});
$("#library").addEventListener("click", (event) => {
  if (event.target.matches("[data-select]")) toggleSelected(event.target.dataset.select, event.shiftKey || event.target.checked, event.shiftKey);
});
$("#selection-delete").addEventListener("click", () => { if (state.selected.size) deleteArchives([...state.selected]); });
$("#selection-restore").addEventListener("click", () => { if (state.selected.size) restoreArchives([...state.selected]); });
let tagging = false;
$("#selection-tag-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#selection-tag");
  const button = event.currentTarget.querySelector("button");
  const tag = input.value.trim();
  if (!tag || !state.selected.size || tagging) return;
  const ids = [...state.selected];
  // Each archive's record is rewritten in the storage, which takes a while for many: the button says how far it is.
  tagging = true;
  input.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const result = await mutateSelection("tag", ids, { tag }, (done) => {
      button.disabled = true;
      button.textContent = t("select.tagging", { done, total: ids.length });
    });
    input.value = "";
    toast(t("select.tagged", { count: result.updated }));
    await refresh();
  } catch (error) { toast(error.message); } finally {
    tagging = false;
    input.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = t("select.addTag");
    renderSelection();
  }
});

// Renaming and deleting tags from the sidebar: the "…" button of a tag, a right click or a long press.
let tagMenuOpener = null;

function openTagMenu(tag, x, y) {
  if (tag === PRIVATE_TAG || tag === UNTAGGED) return;
  tagMenuOpener = document.activeElement;
  state.tagMenu = tag;
  document.querySelector(`[data-tag-menu="${CSS.escape(tag)}"]`)?.setAttribute("aria-expanded", "true");
  const menu = $("#tag-menu");
  menu.hidden = false;
  const { innerWidth, innerHeight } = window;
  menu.style.left = `${Math.min(x, innerWidth - menu.offsetWidth - 8)}px`;
  menu.style.top = `${Math.min(y, innerHeight - menu.offsetHeight - 8)}px`;
  menu.querySelector("button").focus();
}

function closeTagMenu(refocus = false) {
  if ($("#tag-menu").hidden) return;
  state.tagMenu = null;
  $("#tag-menu").hidden = true;
  document.querySelectorAll("[data-tag-menu]").forEach((button) => button.setAttribute("aria-expanded", "false"));
  if (refocus) tagMenuOpener?.focus();
}

document.addEventListener("click", (event) => { if (!event.target.closest("#tag-menu, [data-tag-menu]")) closeTagMenu(); });
$("#tag-nav").addEventListener("click", (event) => {
  const more = event.target.closest("[data-tag-menu]");
  if (!more) return;
  const rect = more.getBoundingClientRect();
  if (state.tagMenu === more.dataset.tagMenu) closeTagMenu(); else openTagMenu(more.dataset.tagMenu, rect.left, rect.bottom + 4);
});
// Arrows move between the menu's actions; leaving it with the keyboard closes it.
$("#tag-menu").addEventListener("keydown", (event) => {
  if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
  event.preventDefault();
  const items = [...$("#tag-menu").querySelectorAll("button")];
  items[(items.indexOf(document.activeElement) + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
});
$("#tag-menu").addEventListener("focusout", (event) => { if (event.relatedTarget && !$("#tag-menu").contains(event.relatedTarget)) closeTagMenu(); });
// A long press, as on a card: the click that ends it must not select the tag.
let pressTimer;
let tagPressed = false;
$("#tag-nav").addEventListener("contextmenu", (event) => {
  const button = event.target.closest("[data-tag]");
  if (!button) return;
  event.preventDefault();
  openTagMenu(button.dataset.tag, event.clientX, event.clientY);
});
$("#tag-nav").addEventListener("pointerdown", (event) => {
  tagPressed = false;
  const button = event.target.closest("[data-tag]");
  if (!button || event.pointerType === "mouse") return;
  pressTimer = setTimeout(() => {
    tagPressed = true;
    const rect = button.getBoundingClientRect();
    openTagMenu(button.dataset.tag, rect.left + 20, rect.bottom);
  }, 500);
});
["pointerup", "pointercancel", "pointerleave"].forEach((type) => $("#tag-nav").addEventListener(type, () => clearTimeout(pressTimer)));
$("#tag-nav").addEventListener("click", (event) => {
  if (!tagPressed) return;
  tagPressed = false;
  event.preventDefault();
  event.stopPropagation();
}, true);

$("#tag-menu").addEventListener("click", (event) => {
  const action = event.target.closest("[data-tag-action]")?.dataset.tagAction;
  const tag = state.tagMenu;
  closeTagMenu();
  if (!action || !tag) return;
  const count = tagCounts().get(tag) || 0;
  state.tagDialog = { action, tag };
  $("#tag-dialog-title").textContent = t(action === "rename" ? "tagDialog.renameTitle" : "tagDialog.deleteTitle", { tag });
  $("#tag-dialog-text").textContent = t(action === "rename" ? "tagDialog.renameText" : "tagDialog.deleteText", { count });
  $("#tag-dialog-form").hidden = action !== "rename";
  $("#tag-dialog-input").value = tag;
  $("#tag-dialog-error").hidden = true;
  $("#tag-dialog-cancel").textContent = t(action === "rename" ? "action.cancel" : "delete.cancel");
  $("#tag-dialog-confirm").textContent = t(action === "rename" ? "action.rename" : "tagDialog.deleteConfirm");
  $("#tag-dialog-confirm").classList.toggle("danger", action === "delete");
  $("#tag-dialog-confirm").classList.toggle("primary", action !== "delete");
  $("#tag-dialog").showModal();
  if (action === "rename") $("#tag-dialog-input").select();
});

async function applyTagDialog() {
  const { action, tag } = state.tagDialog || {};
  if (!action) return;
  $("#tag-dialog-confirm").disabled = true;
  try {
    const result = action === "rename"
      ? await api("/api/tags/rename", { method: "POST", body: JSON.stringify({ from: tag, to: $("#tag-dialog-input").value }) })
      : await api("/api/tags/delete", { method: "POST", body: JSON.stringify({ tag }) });
    $("#tag-dialog").close();
    const renamed = $("#tag-dialog-input").value.trim().toLowerCase();
    state.tags = [...new Set(state.tags.flatMap((name) => (name !== tag ? [name] : action === "rename" ? [renamed] : [])))];
    toast(t(action === "rename" ? "tagDialog.renamed" : "tagDialog.deleted", { count: result.updated }));
    await refresh();
    if (existingTags().length !== state.tags.length) setView(state.view, existingTags());
  } catch (error) {
    $("#tag-dialog-error").textContent = error.message;
    $("#tag-dialog-error").hidden = false;
  } finally {
    $("#tag-dialog-confirm").disabled = false;
  }
}

$("#tag-dialog-confirm").addEventListener("click", applyTagDialog);
$("#tag-dialog-form").addEventListener("submit", (event) => { event.preventDefault(); applyTagDialog(); });
$("#tag-dialog-cancel").addEventListener("click", () => $("#tag-dialog").close());

// An archive's menu: the "…" button of its card, a right click, a long press, or the keyboard's menu key. Opened on a
// card of a selection of several, it acts on the whole selection.
// It is a popover, in the top layer and closed by the browser on Escape or a press outside, anchored by CSS to an
// empty element moved to where it opens; without these in the browser, it is shown and placed from here.
const cardMenu = { id: null, ids: [], panel: "actions", x: 0, y: 0, opened: 0, opener: null, busy: false };
const cardMenuItems = () => cardMenu.ids.map((id) => state.items.find((item) => item.id === id)).filter(Boolean);
const popoverMenu = Object.hasOwn(HTMLElement.prototype, "popover");
const anchoredMenu = popoverMenu && CSS.supports("position-area: block-end");
if (popoverMenu) $("#card-menu").hidden = false; // a closed popover is not rendered
const cardMenuOpen = () => (popoverMenu ? $("#card-menu").matches(":popover-open") : !$("#card-menu").hidden);

function openCardMenu(id, x, y) {
  if (!state.items.some((item) => item.id === id)) return;
  closeTagMenu();
  const opener = cardMenuOpen() ? cardMenu.opener : document.activeElement;
  closeCardMenu();
  Object.assign(cardMenu, { id, x, y, opener, opened: performance.now(), ids: state.selected.has(id) && state.selected.size > 1 ? [...state.selected] : [id] });
  const anchor = $("#card-menu-anchor");
  anchor.style.left = `${x}px`;
  anchor.style.top = `${y}px`;
  if (popoverMenu) $("#card-menu").showPopover({ source: anchor }); else $("#card-menu").hidden = false;
  showCardPanel("actions");
}

// With `refocus`, focus goes back to where it was before the menu, or to its card when that is gone.
function closeCardMenu(refocus = false) {
  if (!cardMenuOpen()) return;
  if (popoverMenu) $("#card-menu").hidePopover(); else $("#card-menu").hidden = true;
  cardMenu.ids = [];
  if (!refocus) return;
  const target = cardMenu.opener?.isConnected && cardMenu.opener !== document.body ? cardMenu.opener
    : document.querySelector(`.card-open[data-open="${CSS.escape(cardMenu.id)}"]`);
  target?.focus({ preventScroll: true });
}

// The actions, or the tags to tick: the menu stays where it opened, within the window.
function showCardPanel(panel) {
  const menu = $("#card-menu");
  const items = cardMenuItems();
  if (!items.length) { closeCardMenu(); return; }
  cardMenu.panel = panel;
  if (panel === "tags") {
    menu.innerHTML = `<input id="card-menu-tag" class="card-menu-search" maxlength="40" autocomplete="off" placeholder="${escapeHTML(t("cardMenu.tagPlaceholder"))}" aria-label="${escapeHTML(t("cardMenu.tagPlaceholder"))}"><div class="card-menu-tags" id="card-menu-tags" role="menu" aria-label="${escapeHTML(t("tags.heading"))}"></div>`;
    renderCardTags();
  } else {
    const [item] = items;
    const many = items.length > 1;
    const trash = Boolean(item.deleted_at);
    const entry = (action, label, danger = false) => `<button role="menuitem" data-card-action="${action}"${danger ? ' class="danger-item"' : ""}>${escapeHTML(label)}</button>`;
    const separator = '<hr class="menu-separator">';
    const label = many ? t("cardMenu.selection") : t("cardMenu.actions", { title: titleOf(item) });
    menu.innerHTML = `${many ? `<p class="menu-label">${escapeHTML(t("select.count", { count: items.length }))}</p>` : ""}<div role="menu" aria-label="${escapeHTML(label)}">${[
      many ? "" : [
        entry("open", t("cardMenu.open")),
        entry("source", sourceLabel(item)),
        entry("copy", t("cardMenu.copy")), separator].join(""),
      many || trash ? "" : entry("rename", t("action.rename")),
      trash ? "" : entry("tags", t(many ? "detail.addTag" : "cardMenu.tags")),
      many ? entry("clear", t("cardMenu.clear")) : entry("select", t(state.selected.has(item.id) ? "cardMenu.deselect" : "select.start")),
      !many && !trash && item.status === "error" ? entry("retry", t("job.retry")) : "",
      separator,
      trash ? entry("restore", t("trash.restore")) : "",
      entry("delete", t(trash ? "trash.deleteForGood" : "action.delete"), true),
    ].join("")}</div>`;
  }
  if (!anchoredMenu) {
    menu.style.left = `${Math.max(8, Math.min(cardMenu.x, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(cardMenu.y, innerHeight - menu.offsetHeight - 8))}px`;
  }
  menu.querySelector("input, button").focus({ preventScroll: true });
}

// Every tag, ticked when the archive has it; what is typed narrows them, and can become a new tag. For a
// selection, a tag is ticked when all its archives have it, and can only be added.
function renderCardTags() {
  const items = cardMenuItems();
  if (!items.length) { closeCardMenu(); return; }
  const typed = $("#card-menu-tag").value.trim();
  const query = fold(typed);
  const rows = [...new Set([...tagCounts().keys(), ...items.flatMap((item) => item.tags)])]
    .map((tag) => ({ tag, label: tagLabel(tag), on: items.every((item) => item.tags.includes(tag)) }))
    .filter(({ label }) => fold(label).includes(query))
    .sort((a, b) => a.label.localeCompare(b.label, locale()));
  const create = typed && !rows.some(({ label }) => fold(label) === query);
  $("#card-menu-tags").innerHTML = rows.map(({ tag, label, on }) =>
    `<button role="menuitemcheckbox" aria-checked="${on}" data-card-tag="${escapeHTML(tag)}"${on && items.length > 1 ? " disabled" : ""}><span class="menu-check">${icon("check")}</span><span class="tag-name"># ${escapeHTML(label)}</span></button>`).join("")
    + (create ? `<button role="menuitem" data-card-tag-new>${icon("plus")}<span class="tag-name">${escapeHTML(t("cardMenu.create", { tag: typed }))}</span></button>` : "")
    || `<p class="menu-label">${t("tags.empty")}</p>`;
}

async function applyCardTag(tag, on) {
  const items = cardMenuItems();
  if (!tag || !items.length || cardMenu.busy) return;
  cardMenu.busy = true;
  try {
    if (items.length > 1) {
      const result = await mutateSelection("tag", items.map((item) => item.id), { tag });
      toast(t("select.tagged", { count: result.updated }));
      await refresh();
    } else {
      await patchTags(items[0], on ? [...items[0].tags, tag] : items[0].tags.filter((name) => name !== tag));
    }
  } catch (error) { toast(error.message); } finally { cardMenu.busy = false; }
  if (!cardMenuOpen() || cardMenu.panel !== "tags") return;
  // Ready for the next tag.
  $("#card-menu-tag").value = "";
  renderCardTags();
  $("#card-menu-tag")?.focus({ preventScroll: true });
}

// Whether the text reached the clipboard.
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Without a secure context (plain HTTP on a local network) the clipboard API is not there: the old way is left.
    const field = Object.assign(document.createElement("textarea"), { value: text });
    field.setAttribute("readonly", "");
    field.style.cssText = "position:fixed;inset-block-start:0;opacity:0";
    document.body.append(field);
    field.select();
    try { return document.execCommand("copy"); } catch { return false; } finally { field.remove(); }
  }
}

$("#card-menu").addEventListener("click", (event) => {
  const row = event.target.closest("[data-card-tag]");
  if (row) { applyCardTag(row.dataset.cardTag, row.getAttribute("aria-checked") !== "true"); return; }
  if (event.target.closest("[data-card-tag-new]")) { applyCardTag($("#card-menu-tag").value.trim(), true); return; }
  const action = event.target.closest("[data-card-action]")?.dataset.cardAction;
  if (!action) return;
  if (action === "tags") { showCardPanel("tags"); return; }
  const ids = [...cardMenu.ids];
  const [item] = cardMenuItems();
  closeCardMenu();
  if (!item) return;
  if (action === "open") openDetail(item.id);
  if (action === "rename") openTitleDialog(item.id);
  if (action === "source") window.open(sourceURL(item), "_blank", "noopener,noreferrer");
  if (action === "copy") copyText(sourceURL(item)).then((copied) => toast(t(copied ? "cardMenu.copied" : "cardMenu.copyFailed")));
  if (action === "select") toggleSelected(item.id);
  if (action === "clear") setSelecting(false);
  if (action === "retry") retryArchive(item.id).catch((error) => toast(error.message));
  if (action === "restore") restoreArchives(ids);
  if (action === "delete") deleteArchives(ids);
});
$("#card-menu").addEventListener("input", (event) => { if (event.target.id === "card-menu-tag") renderCardTags(); });
// Arrows, Home and End move between the field and the menu's entries; Enter in the field ticks the tag typed, or
// creates it.
$("#card-menu").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.id === "card-menu-tag") {
    event.preventDefault();
    const typed = event.target.value.trim();
    const row = [...document.querySelectorAll("#card-menu-tags [data-card-tag]")].find((button) => fold(tagLabel(button.dataset.cardTag)) === fold(typed));
    if (!row) applyCardTag(typed, true);
    else if (!row.disabled) applyCardTag(row.dataset.cardTag, row.getAttribute("aria-checked") !== "true");
    return;
  }
  // In the field, Home and End move its caret.
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || (["Home", "End"].includes(event.key) && event.target.id === "card-menu-tag")) return;
  event.preventDefault();
  const stops = [...$("#card-menu").querySelectorAll("input, button:not(:disabled)")];
  const next = event.key === "Home" ? 0 : event.key === "End" ? stops.length - 1
    : (stops.indexOf(document.activeElement) + (event.key === "ArrowDown" ? 1 : -1) + stops.length) % stops.length;
  stops[next].focus();
});
// Closed by the browser (Escape, a press outside): nothing is left to act on.
$("#card-menu").addEventListener("toggle", (event) => { if (event.newState === "closed") cardMenu.ids = []; });
$("#card-menu").addEventListener("focusout", (event) => { if (event.relatedTarget && !$("#card-menu").contains(event.relatedTarget)) closeCardMenu(); });
// A press outside closes the menu and does nothing else: the click it ends is not passed on to what is under it.
let cardMenuDismissed = false;
document.addEventListener("pointerdown", (event) => {
  cardMenuDismissed = event.button === 0 && cardMenuOpen() && !event.composedPath().includes($("#card-menu"));
  if (cardMenuDismissed) closeCardMenu();
}, true);
document.addEventListener("click", (event) => {
  if (!cardMenuDismissed) return;
  cardMenuDismissed = false;
  event.preventDefault();
  event.stopPropagation();
}, true);
// The list of actions does not follow the page, once a scroll under way when it opened has settled; the tag field
// stays, since a phone's keyboard scrolls the page.
window.addEventListener("scroll", () => { if (cardMenu.panel === "actions" && performance.now() - cardMenu.opened > 300) closeCardMenu(); }, { passive: true });

$("#library").addEventListener("click", (event) => {
  const more = event.target.closest("[data-card-menu]");
  if (!more) return;
  const rect = more.getBoundingClientRect();
  openCardMenu(more.dataset.cardMenu, rect.left, rect.bottom + 4);
});
$("#library").addEventListener("contextmenu", (event) => {
  const card = event.target.closest(".media-card");
  if (!card) return;
  event.preventDefault();
  const rect = card.getBoundingClientRect();
  // From the keyboard there is no pointer: the menu opens on the card.
  const pointed = event.clientX || event.clientY;
  openCardMenu(card.dataset.key, pointed ? event.clientX : rect.left + 24, pointed ? event.clientY : rect.top + 24);
});
// A long press, where the browser has no context menu for it; the click that ends it must not open the archive.
let cardPressTimer;
let cardPressed = false;
$("#library").addEventListener("pointerdown", (event) => {
  cardPressed = false;
  const card = event.target.closest(".media-card");
  if (!card || event.pointerType === "mouse") return;
  const { clientX, clientY } = event;
  cardPressTimer = setTimeout(() => { cardPressed = true; openCardMenu(card.dataset.key, clientX, clientY); }, 500);
});
["pointerup", "pointercancel", "pointerleave"].forEach((type) => $("#library").addEventListener(type, () => clearTimeout(cardPressTimer)));
$("#library").addEventListener("click", (event) => {
  if (!cardPressed) return;
  cardPressed = false;
  event.preventDefault();
  event.stopPropagation();
}, true);

async function loadSystem() {
  try {
    state.system = await api("/api/system");
    const missing = Object.entries(state.system.dependencies).filter(([, present]) => !present).map(([name]) => name);
    if (missing.length) {
      $("#system-warning").textContent = t("system.missing", { tools: missing.join(", ") });
      $("#system-warning").hidden = false;
    }
    renderSidebar();
    $("#app-version").textContent = `v${state.system.version}`;
  } catch { /* The library request displays the connection state. */ }
}

// Links shared to the app (Android share sheet, iPhone shortcut) arrive on /share.
function sharedLink() {
  if (location.pathname !== "/share") return null;
  const params = new URLSearchParams(location.search);
  // Apps put the link in "url", or inside the shared "text".
  for (const value of [params.get("url"), params.get("text"), params.get("title")]) {
    const match = value?.match(/https?:\/\/[^\s<>"']+/);
    if (match) return match[0];
  }
  return null;
}

function receiveShare() {
  const link = sharedLink();
  if (location.pathname === "/share") history.replaceState(null, "", "/");
  if (!link) return;
  // A page could open this address itself: archiving waits for an explicit confirmation.
  $("#url").value = link;
  showFileHint();
  $("#share-link").textContent = link;
  $("#share-banner").hidden = false;
  $("#confirm-share").focus();
}

function setupCapture() {
  const origin = location.origin;
  $("#shortcut-url").textContent = `${origin}/share?url=`;
  $("#capture-local").hidden = !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

$("#confirm-share").addEventListener("click", () => { $("#share-banner").hidden = true; $("#archive-form").requestSubmit(); });
$("#cancel-share").addEventListener("click", () => { $("#share-banner").hidden = true; $("#url").value = ""; showFileHint(); });
$("#status-toggle").addEventListener("click", () => {
  state.showEmptyStatuses = !state.showEmptyStatuses;
  try { localStorage.setItem("show-empty-statuses", state.showEmptyStatuses ? "1" : "0"); } catch { /* per-visit only */ }
  renderSidebar();
});
$("#open-capture-menu").addEventListener("click", () => $("#capture-dialog").showModal());

// Exporting the library, or only the archives with some tags; private archives only when asked for.
const exportPrivate = () => state.unlocked && $("#export-private").checked;
const exportable = () => kept().filter((item) => exportPrivate() || !isPrivate(item));

function exportSelection() {
  const byTags = document.querySelector('[name="export-scope"]:checked').value === "tags";
  const tags = new Set([...document.querySelectorAll("#export-tags [data-export-tag]:checked")].map((box) => box.dataset.exportTag));
  const untagged = Boolean($("#export-untagged")?.checked);
  const items = byTags ? exportable().filter((item) => (untagged && !item.tags.length) || item.tags.some((tag) => tags.has(tag))) : exportable();
  const params = new URLSearchParams();
  if (byTags) {
    tags.forEach((tag) => params.append("tag", tag));
    if (untagged) params.set("untagged", "1");
  }
  // A download cannot send headers: the token goes in its address.
  if (exportPrivate()) { params.set("private", "1"); params.set("unlock", unlockToken); }
  // The time is part of the address so a resumed download names the same export.
  params.set("at", new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16));
  return { items, byTags, empty: byTags && !tags.size && !untagged, params };
}

function renderExport() {
  const { items, byTags, empty, params } = exportSelection();
  const ready = items.filter((item) => item.status === "ready");
  $("#export-tags").hidden = !byTags;
  $("#export-summary").textContent = empty ? t("export.none") : t("export.summary", { count: ready.length, size: sizeLabel(ready.reduce((sum, item) => sum + item.storage_bytes, 0)) });
  $("#export-start").href = `/api/export?${params}`;
  $("#export-start").classList.toggle("disabled", empty || !ready.length);
  $("#export-start").setAttribute("aria-disabled", String(empty || !ready.length));
}

// The tags to choose from, among the archives that can be exported; the boxes already checked stay checked.
function renderExportTags() {
  const checked = new Set([...document.querySelectorAll("#export-tags [data-export-tag]:checked")].map((box) => box.dataset.exportTag));
  const untaggedChecked = Boolean($("#export-untagged")?.checked);
  const counts = new Map();
  const sizes = new Map();
  let untagged = 0, untaggedSize = 0;
  exportable().forEach((item) => {
    if (!item.tags.length) { untagged += 1; untaggedSize += item.storage_bytes; }
    item.tags.forEach((tag) => {
      counts.set(tag, (counts.get(tag) || 0) + 1);
      sizes.set(tag, (sizes.get(tag) || 0) + item.storage_bytes);
    });
  });
  const tags = [...counts].sort(([a], [b]) => (b === PRIVATE_TAG) - (a === PRIVATE_TAG) || a.localeCompare(b, locale()));
  $("#export-tags").innerHTML = tags.map(([tag, count]) =>
    `<label class="export-option"><input type="checkbox" data-export-tag="${escapeHTML(tag)}" ${checked.has(tag) ? "checked" : ""}><span class="tag-name">${tagChip(tag)}</span><span class="export-count">${count} · ${sizeLabel(sizes.get(tag))}</span></label>`
  ).join("") + (untagged ? `<label class="export-option"><input type="checkbox" id="export-untagged" ${untaggedChecked ? "checked" : ""}><span class="tag-name">${t("export.untagged")}</span><span class="export-count">${untagged} · ${sizeLabel(untaggedSize)}</span></label>` : "");
}

function openExport() {
  $("#export-private-option").hidden = !state.unlocked;
  $("#export-private").checked = false;
  $("#export-tags").innerHTML = "";
  renderExportTags();
  document.querySelector('[name="export-scope"][value="all"]').checked = true;
  renderExport();
  $("#export-dialog").showModal();
}

$("#open-export").addEventListener("click", openExport);
$("#export-dialog").addEventListener("change", (event) => {
  if (event.target.id === "export-private") renderExportTags();
  renderExport();
});
$("#export-cancel").addEventListener("click", () => $("#export-dialog").close());
$("#export-start").addEventListener("click", (event) => {
  if ($("#export-start").classList.contains("disabled")) { event.preventDefault(); return; }
  $("#export-dialog").close();
});
$("#close-capture").addEventListener("click", () => $("#capture-dialog").close());

// The articles' text, read from the server only while searching inside articles is on.
let articleTextsTask = null;
let articleTextsAgain = false;
loadArticleTexts = () => {
  if (!state.fullText) return;
  if (articleTextsTask) { articleTextsAgain = true; return; }
  articleTextsAgain = false;
  const key = syncKey();
  articleTextsTask = api("/api/articles/text").then(({ texts }) => {
    if (key !== syncKey()) return;
    const known = new Set(state.items.map((item) => item.id));
    state.articleTexts = new Map(Object.entries(texts).filter(([id]) => known.has(id)).map(([id, text]) => [id, fold(text)]));
    if (state.query) render();
  }).catch(() => {}).finally(() => {
    articleTextsTask = null;
    if (articleTextsAgain || key !== syncKey()) loadArticleTexts();
  });
};
loadArticleTexts();
$("#open-shortcuts").addEventListener("click", () => $("#shortcuts-dialog").showModal());
$("#close-shortcuts").addEventListener("click", () => $("#shortcuts-dialog").close());

// Changing the password closes every other session and hides private archives again.
$("#open-password").addEventListener("click", () => {
  $("#password-form").reset();
  $("#password-error").hidden = true;
  $("#password-dialog").showModal();
  $("#password-current").focus();
});
$("#password-cancel").addEventListener("click", () => $("#password-dialog").close());
$("#password-dialog").addEventListener("close", () => $("#password-form").reset());
$("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = (message) => { $("#password-error").textContent = message; $("#password-error").hidden = false; };
  if ($("#password-new").value !== $("#password-repeat").value) { error(t("auth.mismatch")); $("#password-repeat").select(); return; }
  $("#password-submit").disabled = true;
  try {
    await api("/api/account/password", { method: "POST", body: JSON.stringify({ current: $("#password-current").value, password: $("#password-new").value }) });
    $("#password-dialog").close();
    lockPrivate("silent");
    toast(t("password.changed"));
  } catch (failure) {
    error(failure.message);
  } finally {
    $("#password-submit").disabled = false;
  }
});
$("#open-about").addEventListener("click", () => $("#about-dialog").showModal());
$("#close-about").addEventListener("click", () => $("#about-dialog").close());
// Private archives: shown for the account's password typed again, hidden again by the lock or a reload.
archiveURL = (item, path) => `/api/media/${item.id}/${path}${unlockToken && isPrivate(item) ? `?unlock=${encodeURIComponent(unlockToken)}` : ""}`;

async function unlockPrivate(password) {
  const { token } = await api("/api/private/unlock", { method: "POST", body: JSON.stringify({ password }) });
  unlockToken = token;
  state.unlocked = true;
  lastActivity = Date.now();
  renderPrivateToggle();
  render();
  toast(t("private.shown"));
  await refresh();
  loadArticleTexts();
}

// `reason`: "manual" from the lock, "idle" after a while without activity, "expired" when the server no longer
// accepts the token, "silent" when the server already revoked it and something else is said.
function lockPrivate(reason = "manual") {
  const token = unlockToken;
  if (!token) return;
  unlockToken = null;
  state.unlocked = false;
  // The library is already synced without them: there is nothing to read again.
  syncedWith = syncKey();
  if (reason === "manual" || reason === "idle") api("/api/private/lock", { method: "POST", body: "{}", headers: { "X-Private-Unlock": token } }).catch(() => {});
  state.items = state.items.filter((item) => !isPrivate(item));
  forgetMissingArchives();
  if ($("#export-dialog").open) $("#export-dialog").close();
  renderPrivateToggle();
  render();
  if (reason !== "silent") toast(t(reason === "manual" ? "private.hidden" : "private.expired"));
}

// Private archives are hidden again after ten minutes without activity (mirrors auth.UNLOCK_SECONDS). Watching
// counts as activity: a video playing, or the player going through photos by itself.
const PRIVATE_IDLE = 10 * 60 * 1000;
let lastActivity = Date.now();
["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"].forEach((type) =>
  addEventListener(type, () => { lastActivity = Date.now(); }, { capture: true, passive: true }));
const watching = () => [...document.querySelectorAll("video")].some((video) => !video.paused && !video.ended)
  || (!$("#player").hidden && player.autoplay);
setInterval(() => {
  if (!unlockToken) return;
  if (watching()) {
    lastActivity = Date.now();
    // A hidden tab does not poll: keep the token alive on the server while a video plays in it.
    if (document.hidden) refresh();
  } else if (Date.now() - lastActivity >= PRIVATE_IDLE) {
    lockPrivate("idle");
  }
}, 15000);

$("#private-toggle").addEventListener("click", () => {
  if (state.unlocked) { lockPrivate(); return; }
  $("#private-error").hidden = true;
  $("#private-password").value = "";
  $("#private-dialog").showModal();
  $("#private-password").focus();
});
$("#private-cancel").addEventListener("click", () => $("#private-dialog").close());
$("#private-dialog").addEventListener("close", () => { $("#private-password").value = ""; });
$("#private-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#private-confirm");
  button.disabled = true;
  $("#private-error").hidden = true;
  try {
    await unlockPrivate($("#private-password").value);
    $("#private-dialog").close();
  } catch (error) {
    $("#private-error").textContent = error.message;
    $("#private-error").hidden = false;
    $("#private-password").select();
  } finally {
    button.disabled = false;
  }
});
// Closing or reloading the page revokes its token on the server too; the page cannot add headers by then.
addEventListener("pagehide", () => {
  if (unlockToken) navigator.sendBeacon("/api/private/lock", new Blob([JSON.stringify({ token: unlockToken })], { type: "application/json" }));
});
// A page restored from the browser's history cache had its token revoked when it was left.
addEventListener("pageshow", (event) => { if (event.persisted) lockPrivate(); });

$("#logout").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* the login page follows either way */ }
  location.replace("/login");
});

applyTranslations();
renderSettings();
applyTheme(document.documentElement.dataset.theme || "dark");
renderPrivateToggle();
setupCapture();
receiveShare();
applyAddress();
refresh();
loadSystem();
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

document.addEventListener("languagechange", () => { renderPrivateToggle(); renderAddTags(); refresh(); });
