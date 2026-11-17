// Rendering of the library, shared by the application (app.js) and the offline viewer (viewer.js).
// The host script supplies the library and file URLs; article loading uses those URLs when needed.
const $ = (selector) => document.querySelector(selector);
const icons = {
  library: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  drive: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 14h18m-5 3h2m-6 0h.01"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 3h.01"/>',
  tag: '<path d="M3 3h8l10 10-8 8L3 11z"/><circle cx="7.5" cy="7.5" r="1"/>',
  link: '<path d="m10 13 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 3 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 1)"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  youtube: '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3z" fill="currentColor" stroke="none"/>',
  x: '<path d="m4 3 12 18h4L8 3zM4 21l6.5-8M20 3l-6.5 8"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  play: '<path d="m9 5 11 7-11 7z" fill="currentColor" stroke="none"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  check: '<path d="m5 12 5 5L20 7"/>',
  instagram: '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.3" cy="6.7" r=".6" fill="currentColor"/>',
  previous: '<path d="m15 5-7 7 7 7"/>',
  next: '<path d="m9 5 7 7-7 7"/>',
  up: '<path d="m5 15 7-7 7 7"/>',
  down: '<path d="m5 9 7 7 7-7"/>',
  external: '<path d="M14 3h7v7m0-7L10 14m0-10H4v16h16v-6"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  audio: '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
  edit: '<path d="M4 20h4L19.5 8.5a2.8 2.8 0 0 0-4-4L4 16zm9.5-13.5 4 4"/>',
  file: '<path d="M13 3H5v18h14V9zm0 0v6h6M8 13h8m-8 4h6"/>',
  tiktok: '<path d="M14 3v11.5a3.5 3.5 0 1 1-3.5-3.5M14 3c.4 2.6 2.2 4.4 5 4.6"/>',
  facebook: '<path d="M15 3h-2.5A3.5 3.5 0 0 0 9 6.5V21m-3-11h8"/>',
  bilibili: '<rect x="3" y="6" width="18" height="14" rx="3"/><path d="m7.5 3 3 3m6-3-3 3M9 12v2m6-2v2"/>',
  bluesky: '<path d="M12 10.5C10.5 7.5 7 4.5 4.5 4.5c-2 0-2 2-1.5 4.5.4 2 1.5 3.5 4 3.5-2.5.5-3.5 2-2 4s4.5 1.5 7-3c2.5 4.5 5.5 5 7 3s.5-3.5-2-4c2.5 0 3.6-1.5 4-3.5.5-2.5.5-4.5-1.5-4.5-2.5 0-6 3-7.5 6Z"/>',
  reddit: '<ellipse cx="12" cy="14.5" rx="8" ry="6"/><path d="m12 8.5 1-5 4 1M9 14v.01M15 14v.01M9.5 17c1.5 1 3.5 1 5 0"/><circle cx="18.5" cy="4.5" r="1.25"/>',
  mastodon: '<path d="M4 9a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v3a5 5 0 0 1-5 5H9.5c0 1.6 1.2 2.4 4.5 2-1.5 1.2-4 1.5-6 1-2.5-.6-4-2.5-4-5.5Z"/><path d="M8.5 13V9.75a1.75 1.75 0 0 1 3.5 0V11m0-1.25a1.75 1.75 0 0 1 3.5 0V13"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3"/>',
  archive: '<path d="M4 9h16v11H4zM3 4h18v5H3zM10 13h4"/>',
  phone: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  card: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h3"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  logout: '<path d="M9 4H5v16h4M14 8l4 4-4 4M18 12H9"/>',
  filter: '<path d="M4 5h16l-6 7.5V19l-4-2v-4.5z"/>',
  restore: '<path d="M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  more: '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3"/>',
  autoplay: '<path d="M4 6v12l8-6zM13 6v12l8-6z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 20c0-3.3 3.6-5 8-5s8 1.7 8 5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  unlock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });

const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const activeStates = new Set(["queued", "downloading", "processing", "uploading"]);
const platformIcons = { X: "x", Web: "globe", YouTube: "youtube", Instagram: "instagram", TikTok: "tiktok", Facebook: "facebook", Bilibili: "bilibili", Bluesky: "bluesky", Mastodon: "mastodon", Reddit: "reddit" };
// Single-video platforms archived through yt-dlp, without post data.
const videoPlatforms = new Set(["YouTube", "Facebook", "Bilibili"]);
// The reserved tag of private archives (mirrors store.PRIVATE): stored under one name, shown translated.
const PRIVATE_TAG = "private";
const isPrivate = (item) => item.tags.includes(PRIVATE_TAG);
// Stands, among the chosen tags, for the archives that have none. It is not a tag: no archive carries it, and no
// tag can have this name, since tags are stored in lower case.
const UNTAGGED = "Untagged";
const tagLabel = (tag) => (tag === PRIVATE_TAG ? t("tags.private") : tag === UNTAGGED ? t("tags.none") : tag);
// Among the tags its name is discreet; as a page title or a filter, it has to say what it is on its own.
const tagTitle = (tag) => (tag === UNTAGGED ? t("tags.untagged") : `# ${tagLabel(tag)}`);
const tagChip = (tag) => `# ${escapeHTML(tagLabel(tag))}`;

const state = {
  items: [], view: "all", tags: [], query: "", tagQuery: "", sort: storedValue("sort") || "newest",
  platform: "", dateField: "created_at", from: "", to: "", shown: 0, visible: [], listKey: "",
  currentId: null, currentAttachment: 0, loaded: false, syncing: false, syncTotal: 0, system: null, unlocked: false,
  pendingDelete: null, pendingTrash: false, selecting: false, selected: new Set(), selectionView: null, tagMenu: null, tagDialog: null,
  showEmptyStatuses: storedValue("show-empty-statuses") === "1",
  layout: storedValue("layout") === "list" ? "list" : "grid",
  // Searching inside the text of articles and of videos' subtitles, when asked: folded texts by archive, filled by `loadArticleTexts`.
  fullText: storedValue("search-text") === "1", articleTexts: new Map(),
};
// Set by the application (read from the server) and the viewer (read from its catalog).
let loadArticleTexts = () => {};
let toastTimer;
let detailSignature = "";
const PAGE = 60; // cards added to the grid at a time, as the end of the list comes near
const TRASH_DAYS = 30; // mirrors store.TRASH_DAYS
// The viewer shows a library that cannot be changed.
const readOnly = document.body.dataset.readonly === "1";

const statusName = (status) => t(`status.${status}`);
const fold = (text) => text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

function sizeLabel(bytes) {
  const units = t("size.units").split(",");
  if (!bytes) return `0 ${units[2]}`;
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** unit).toLocaleString(locale(), { maximumFractionDigits: unit > 1 ? 1 : 0 })} ${units[unit]}`;
}

function durationLabel(seconds) {
  if (seconds == null) return "";
  const n = Math.floor(seconds);
  const pad = (value) => String(value).padStart(2, "0");
  return n >= 3600 ? `${Math.floor(n / 3600)}:${pad(Math.floor(n / 60) % 60)}:${pad(n % 60)}` : `${Math.floor(n / 60)}:${pad(n % 60)}`;
}

// Formatters are slow to build and every card needs two: one is kept per language and shape.
const dateFormats = {};
function dateLabel(date, short = false) {
  if (!date) return t("date.unknown");
  const value = new Date(/^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00` : date);
  const year = !short || value.getFullYear() !== new Date().getFullYear();
  const format = dateFormats[`${locale()}/${short}/${year}`] ||= new Intl.DateTimeFormat(locale(), { day: "numeric", month: short ? "short" : "long", ...(year ? { year: "numeric" } : {}) });
  return format.format(value);
}

// The day of a date in the viewer's time zone (YYYY-MM-DD), as date inputs give it.
function localDay(date) {
  if (!date) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(date)) return date;
  const value = new Date(date);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

// Links in a text that is already escaped become clickable.
function linkify(escaped) {
  return escaped.replace(/https?:\/\/(?:[^\s<&]|&amp;)+/g, (match) => {
    const url = match.replace(/[).,;:!?»]+$/, "");
    return `<a class="text-link" href="${url}" target="_blank" rel="noreferrer">${url}</a>${match.slice(url.length)}`;
  });
}

// A message, with an optional action such as "Undo".
function toast(message, action = null) {
  const box = $("#toast");
  box.innerHTML = `<span>${escapeHTML(message)}</span>${action ? `<button class="toast-action" type="button">${escapeHTML(action.label)}</button>` : ""}`;
  if (action) box.querySelector("button").addEventListener("click", () => { box.hidden = true; action.run(); });
  box.hidden = false;
  clearTimeout(toastTimer);
  // It stays while the keyboard is on its action.
  const hide = () => { if (box.contains(document.activeElement)) toastTimer = setTimeout(hide, 2000); else box.hidden = true; };
  toastTimer = setTimeout(hide, action ? 8000 : 4500);
}

// An archive's address in the API; the application adds the unlock token of private archives to it.
let archiveURL = (item, path) => `/api/media/${item.id}/${path}`;
// Where an archive's files are: the API by default; the viewer points at the folders next to it.
let fileURL = (item, kind, index = null) => archiveURL(item, `file/${kind}${index === null ? "" : `/${index}`}`);

function siteName(item) {
  if (item.details?.site) return item.details.site;
  try { return new URL(item.source_url).hostname.replace(/^www\./, ""); } catch { return item.platform; }
}

// A web page archived as an article; a link straight to a file (image, video, sound, PDF) is shown as that medium.
const isArticle = (item) => item.platform === "Web" && item.details?.kind !== "file";

// The title shown everywhere: the personal one when there is one, else the archive's own.
const titleOf = (item) => item.custom_title || item.title;
// What the open archive shows, to redraw it only when that changed.
const detailSignatureOf = (item) => `${item.id}/${item.status}/${item.error}/${titleOf(item)}`;
// The title of the open archive, with the pencil that opens the window to give it a personal one.
const titleRow = (item) => `<div class="title-row"><h2 id="detail-title">${escapeHTML(titleOf(item))}</h2>${readOnly || item.deleted_at ? "" : `<button class="icon-button title-edit" type="button" data-edit-title aria-label="${escapeHTML(t("detail.editTitle"))}" title="${escapeHTML(t("detail.editTitle"))}">${icon("edit")}</button>`}</div>`;
// The archive's own title, under a personal one.
const originalTitle = (item) => `<p class="detail-original-title" id="original-title" ${item.custom_title ? "" : "hidden"}>${t("detail.originalTitle")} <span>${escapeHTML(item.title)}</span></p>`;

function platformName(platform) {
  return platform === "Web" ? t("card.web") : platform;
}

// The original of an archive: a web page's own address when it states one, and what its link is called.
const sourceURL = (item) => item.details?.url || item.source_url;
const sourceLabel = (item) => item.platform === "Web" ? t("detail.viewSource") : t("detail.viewOn", { platform: escapeHTML(item.platform) });

function attachments(item) {
  if (item.attachments?.length) return item.attachments;
  // A video job shows its metadata while it downloads; posts may have no media.
  return videoPlatforms.has(item.platform) ? [{ ...item, type: "video" }] : [];
}

// Sounds without a cover and PDFs archived without a page preview have no thumbnail.
const hasThumbnail = (item, medium) => Boolean(item.attachments?.length ? medium.thumbnail : item.thumbnail);

function upsert(item) {
  // A mutation may finish after private archives were locked. Its write succeeded on the server,
  // but its response must not bring a hidden archive back into the interface.
  if (!readOnly && isPrivate(item) && !state.unlocked) return false;
  const index = state.items.findIndex((existing) => existing.id === item.id);
  if (index < 0) state.items.unshift(item);
  else state.items[index] = item;
  return true;
}

const currentItem = () => state.items.find((item) => item.id === state.currentId);
// The library, without the archives in the trash.
const kept = () => state.items.filter((item) => !item.deleted_at);
const filterCount = () => [state.platform, state.from || state.to, state.fullText].filter(Boolean).length;

// The text a search looks into, folded once per archive: an archive that changes is a new object.
let searchTexts = new WeakMap();
function searchText(item) {
  let text = searchTexts.get(item);
  if (text === undefined) {
    text = fold(`${titleOf(item)} ${item.author || ""} ${item.description || ""} ${item.note || ""} ${item.tags.map(tagLabel).join(" ")}`);
    searchTexts.set(item, text);
  }
  return text;
}

function visibleItems() {
  const query = fold(state.query);
  return state.items.filter((item) => {
    if (state.view === "trash" ? !item.deleted_at : item.deleted_at) return false;
    if (state.platform && item.platform !== state.platform) return false;
    if (state.from || state.to) {
      const day = localDay(item[state.dateField]);
      if (!day || (state.from && day < state.from) || (state.to && day > state.to)) return false;
    }
    if (state.view === "active" && !activeStates.has(item.status)) return false;
    if (state.view === "error" && item.status !== "error" && item.integrity !== "damaged") return false;
    if (state.view === "gone" && item.availability !== "gone") return false;
    // Tags add up: an archive shows when it has any of the chosen tags.
    if (state.tags.length && !(item.tags.length ? item.tags.some((tag) => state.tags.includes(tag)) : state.tags.includes(UNTAGGED))) return false;
    if (!query || searchText(item).includes(query)) return true;
    return Boolean(query) && state.fullText && (state.articleTexts.get(item.id) || "").includes(query);
  }).sort((a, b) => {
    if (state.sort === "title") return titleOf(a).localeCompare(titleOf(b), locale());
    if (state.sort === "title-desc") return titleOf(b).localeCompare(titleOf(a), locale());
    if (state.sort === "oldest") return a.created_at.localeCompare(b.created_at);
    if (state.sort.startsWith("published")) {
      // Archives without a publication date come last, whichever the direction.
      if (!a.published_at || !b.published_at) return Boolean(b.published_at) - Boolean(a.published_at);
      const order = a.published_at.localeCompare(b.published_at);
      return state.sort === "published" ? -order : order;
    }
    return b.created_at.localeCompare(a.created_at);
  });
}

// How many archives each tag has; with `untagged`, also how many have none, under UNTAGGED.
function tagCounts(untagged = false) {
  const tags = new Map();
  let none = 0;
  kept().forEach((item) => {
    if (!item.tags.length) none += 1;
    item.tags.forEach((tag) => tags.set(tag, (tags.get(tag) || 0) + 1));
  });
  // Without any tag yet, every archive is untagged and the entry would only repeat the library. While it is chosen,
  // the entry stays even at zero, so that it can be clicked again to leave it.
  if (untagged && tags.size && (none || state.tags.includes(UNTAGGED))) tags.set(UNTAGGED, none);
  // The private tag is there as soon as private archives are shown, even before any archive has it: it is how
  // an archive is made private.
  if (!readOnly && state.unlocked && !tags.has(PRIVATE_TAG)) tags.set(PRIVATE_TAG, 0);
  return tags;
}

// The chosen tags that still exist, after archives or tags changed.
function existingTags() {
  const counts = tagCounts(true);
  return state.tags.filter((tag) => counts.has(tag));
}

// Keep unchanged cards and tag rows in place, preserving focus, images and their state.
function reconcile(container, html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const existing = new Map([...container.children].map((element, index) => [element.dataset.key || String(index), element]));
  const active = document.activeElement;
  const ownedFocus = container.contains(active);
  const identity = ["data-open", "data-tag", "data-tag-menu", "data-select", "data-card-menu"]
    .find((name) => active.hasAttribute?.(name));
  const selector = identity ? `[${identity}="${CSS.escape(active.getAttribute(identity))}"]` : null;
  const cardKey = active.closest?.(".media-card")?.dataset.key;
  const kept = new Set();
  [...template.content.children].forEach((next, index) => {
    const previous = existing.get(next.dataset.key || String(index));
    const markup = next.outerHTML;
    const node = previous && previous.outerHTML === markup ? previous : next;
    kept.add(node);
    if (container.children[index] !== node) container.insertBefore(node, container.children[index] || null);
  });
  [...container.children].forEach((node) => { if (!kept.has(node)) node.remove(); });
  if (ownedFocus && document.activeElement !== active) {
    const target = active.isConnected ? active : (selector && container.querySelector(selector))
      || (cardKey && container.querySelector(`[data-open="${CSS.escape(cardKey)}"]`))
      || container.querySelector("button, input") || container;
    target.focus({ preventScroll: true });
  }
}

function renderSidebar() {
  const library = kept();
  $("#all-count").textContent = library.length;
  const counts = {
    active: library.filter((item) => activeStates.has(item.status)).length,
    error: library.filter((item) => item.status === "error" || item.integrity === "damaged").length,
    gone: library.filter((item) => item.availability === "gone").length,
  };
  const trash = $('[data-view="trash"]');
  if (trash) {
    const count = state.items.length - library.length;
    $("#trash-count").textContent = count;
    trash.hidden = !count && state.view !== "trash";
    trash.classList.toggle("active", state.view === "trash");
    trash.setAttribute("aria-pressed", String(state.view === "trash"));
  }
  Object.entries(counts).forEach(([view, count]) => { if ($(`#${view}-count`)) $(`#${view}-count`).textContent = count; });
  const all = $('[data-view="all"]');
  all.classList.toggle("active", state.view === "all" && !state.tags.length);
  all.setAttribute("aria-current", state.view === "all" && !state.tags.length ? "page" : "false");
  // Empty statuses stay out of the way unless asked for, except the one being viewed. So does Gone:
  // its archives are already kept and badged on their cards, and nothing needs doing about them.
  let hiddenStatuses = 0;
  let collapsible = 0;
  // With tags chosen, the heading offers to take them all off at once.
  $("#tags-clear").hidden = !state.tags.length;
  $("#tags-clear").textContent = t("tags.clear", { count: state.tags.length });
  $("#tags-icon").hidden = Boolean(state.tags.length);
  const toggle = $("#status-toggle");
  const statuses = document.querySelectorAll("[data-status]");
  statuses.forEach((button) => {
    const selected = button.dataset.view === state.view;
    const secondary = !selected && (!counts[button.dataset.view] || (toggle && button.dataset.view === "gone"));
    button.hidden = secondary && !state.showEmptyStatuses;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
    if (secondary) collapsible += 1;
    if (button.hidden) hiddenStatuses += 1;
  });
  const heading = $(".status-heading");
  if (heading && !toggle) heading.hidden = hiddenStatuses === statuses.length;
  if (toggle) {
    toggle.hidden = !collapsible;
    toggle.textContent = state.showEmptyStatuses ? t("statuses.hideEmpty") : t("statuses.showEmpty", { count: collapsible });
    toggle.setAttribute("aria-pressed", String(state.showEmptyStatuses));
  }
  const tags = tagCounts(true);
  $("#tag-search-field").hidden = tags.size < 8;
  const query = fold(state.tagQuery);
  // Archives without a tag come first, then the Private tag; neither can be renamed or deleted.
  const fixed = (tag) => (tag === UNTAGGED ? 2 : tag === PRIVATE_TAG ? 1 : 0);
  const shown = [...tags].filter(([tag]) => fold(tagLabel(tag)).includes(query))
    .sort(([a], [b]) => fixed(b) - fixed(a) || a.localeCompare(b, locale()));
  const menu = Boolean($("#tag-menu")); // the viewer cannot change tags
  reconcile($("#tag-nav"), shown.length ? shown.map(([tag, count]) => `<div class="tag-row${tag === PRIVATE_TAG ? " private-tag" : ""}" data-key="${escapeHTML(tag)}">
    <button class="nav-button ${state.tags.includes(tag) ? "active" : ""}" data-tag="${escapeHTML(tag)}" aria-pressed="${state.tags.includes(tag)}"><span class="tag-hash">${tag === PRIVATE_TAG ? icon("lock") : "#"}</span><span class="tag-name">${escapeHTML(tagLabel(tag))}</span><span class="count">${count}</span></button>
    ${menu && !fixed(tag) ? `<button class="tag-more" data-tag-menu="${escapeHTML(tag)}" aria-haspopup="menu" aria-expanded="${state.tagMenu === tag}" aria-label="${escapeHTML(t("tagMenu.open", { tag }))}">${icon("more")}</button>` : ""}</div>`
  ).join("") : `<p class="sidebar-hint">${t(tags.size ? "tags.noMatch" : "tags.empty")}</p>`);
  const total = state.items.reduce((sum, item) => sum + item.storage_bytes, 0);
  $("#storage-size").textContent = sizeLabel(total);
}

// The lock: closed while private archives are hidden.
function renderPrivateToggle() {
  const toggle = $("#private-toggle");
  if (!toggle) return;
  const label = t(state.unlocked ? "private.hide" : "private.show");
  toggle.innerHTML = icon(state.unlocked ? "unlock" : "lock");
  toggle.setAttribute("aria-pressed", String(state.unlocked));
  toggle.setAttribute("aria-label", label);
  toggle.title = label;
}

function card(item) {
  const ready = item.status === "ready";
  const web = isArticle(item);
  const media = web ? [] : attachments(item);
  const kind = media[0]?.type;
  const video = kind === "video";
  const mark = video ? "play" : kind === "audio" ? "audio" : kind === "pdf" ? "file" : "image";
  const posts = item.posts?.length || 0;
  const badge = web ? (item.details?.minutes ? t("card.readMinutes", { minutes: item.details.minutes }) : t("card.article"))
    : posts > 1 ? t("card.thread", { count: posts }) : media.length > 1 ? t("card.media", { count: media.length })
    : video ? durationLabel(item.duration) : kind === "audio" ? (item.duration ? durationLabel(item.duration) : t("card.audio"))
    : kind === "pdf" ? "PDF" : media.length ? t("card.photo") : "";
  const preview = web && item.thumbnail ? `<img src="${fileURL(item, "thumbnail")}" alt="" loading="lazy">`
    : !media.length ? `<div class="text-preview"><p>${escapeHTML(item.description || titleOf(item))}</p></div>`
    : media.length > 1 ? `<div class="mosaic count-${Math.min(media.length, 4)}">${media.slice(0, 4).map((asset, i) => `<span class="mosaic-tile"><img src="${fileURL(item, "thumbnail", i)}" alt="" loading="lazy">${asset.type === "video" ? `<span class="tile-play">${icon("play")}</span>` : ""}</span>`).join("")}</div>`
    : hasThumbnail(item, media[0]) ? `<img src="${fileURL(item, "thumbnail")}" alt="" loading="lazy">`
    : `<div class="file-preview">${icon(mark)}</div>`;
  const selected = state.selected.has(item.id);
  // A post without media shows its text as the preview: the grid does not repeat it as a title.
  const textOnly = ready && !web && !media.length;
  return `<article data-key="${item.id}" class="media-card ${selected ? "selected" : ""} ${textOnly ? "text-only" : ""}">
    ${readOnly ? "" : `<div class="card-actions"><button class="card-more" type="button" data-card-menu="${item.id}" aria-haspopup="menu" aria-label="${escapeHTML(t("cardMenu.actions", { title: titleOf(item) }))}">${icon("more")}</button><label class="select-box"><input type="checkbox" data-select="${item.id}" ${selected ? "checked" : ""} ${state.selecting ? "" : 'tabindex="-1"'} aria-label="${escapeHTML(t("card.select", { title: titleOf(item) }))}"></label></div>`}
    <button class="card-open" data-open="${item.id}" aria-describedby="card-state-${item.id} card-meta-${item.id}" aria-label="${escapeHTML(t(ready && video ? "card.play" : "card.open", { title: titleOf(item) }))}">
      <div class="thumbnail">
        ${item.deleted_at ? `<span id="card-state-${item.id}" class="state-badge">${t("card.trashLeft", { count: trashDaysLeft(item) })}</span>`
          : item.availability === "gone" ? `<span id="card-state-${item.id}" class="state-badge">${t("card.gone")}</span>` : item.integrity === "damaged" ? `<span id="card-state-${item.id}" class="state-badge">${t("card.damaged")}</span>`
          : warningsOf(item).length ? `<span id="card-state-${item.id}" class="state-badge incomplete">${t("card.incomplete")}</span>` : ""}
        ${ready ? `${preview}${media.length ? `<span class="play-overlay"><span>${icon(mark)}</span></span>` : ""}${badge ? `<span class="duration">${badge}</span>` : ""}` :
          `<div id="card-state-${item.id}" class="job-status ${item.status === "error" ? "error" : ""}">${icon(item.status === "error" ? "alert" : item.status === "queued" ? "clock" : "download")}<span>${statusName(item.status)}${item.status === "downloading" ? ` · ${Math.round(item.progress)} %` : ""}</span>${item.status === "downloading" ? `<progress class="job-progress" max="100" value="${Number(item.progress)}" aria-label="${t("card.progress")}"></progress>` : ""}</div>`}
      </div>
      <h3 class="card-title">${escapeHTML(titleOf(item))}</h3>
      <div class="card-meta" id="card-meta-${item.id}"><span class="platform-icon platform-${item.platform.toLowerCase()}" title="${escapeHTML(platformName(item.platform))}">${icon(platformIcons[item.platform])}<span class="sr-only">${escapeHTML(platformName(item.platform))}</span></span><span class="author">${escapeHTML(item.platform === "Web" ? siteName(item) : item.author || item.platform)}</span>${cardDate(item)}</div>
    </button>
    <div class="card-tags">${item.tags.map((tag) => `<button class="tag-chip" data-tag="${escapeHTML(tag)}">${tagChip(tag)}</button>`).join("")}</div>
  </article>`;
}

// The publication date, or the date added when the publication is unknown; the tooltip says which.
function cardDate(item) {
  const date = item.published_at || item.created_at;
  const label = t(item.published_at ? "date.published" : "date.added");
  return `<time datetime="${escapeHTML(date)}" title="${escapeHTML(`${label} · ${dateLabel(date)}`)}">${dateLabel(date, true)}</time>`;
}

function trashDaysLeft(item) {
  return Math.max(0, Math.ceil(TRASH_DAYS - (Date.now() - new Date(item.deleted_at)) / 86400000));
}

function storedValue(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function renderSelection() {
  if (!$("#selection-bar")) return;
  document.body.classList.toggle("selecting", state.selecting);
  $("#selection-bar").hidden = !state.selecting;
  $("#toggle-select").querySelector("span:last-child").textContent = t(state.selecting ? "select.stop" : "select.start");
  $("#toggle-select").setAttribute("aria-pressed", String(state.selecting));
  $("#toggle-select").disabled = !state.loaded || state.syncing;
  $("#selection-count").textContent = t("select.count", { count: state.selected.size });
  const trash = state.view === "trash";
  $("#selection-delete").disabled = !state.selected.size;
  $("#selection-delete").textContent = t(trash ? "trash.deleteForGood" : "action.delete");
  $("#selection-restore").hidden = !trash;
  $("#selection-restore").disabled = !state.selected.size;
  $("#selection-tag-form").hidden = trash;
  const tagButton = $("#selection-tag-form").querySelector("button");
  tagButton.disabled = !state.selected.size || tagButton.getAttribute("aria-busy") === "true";
}

function render() {
  renderSidebar();
  // The scripts that include this file listen to "libraryrender" for what follows the view and is their own.
  document.dispatchEvent(new Event("libraryrender"));
  // One title says where we are: a status, tags, or the whole library; a status and tags together show the tags as pills.
  $("#page-title").textContent = state.view === "all" && state.tags.length ? state.tags.map(tagTitle).join(", ") : t(`nav.${state.view}`);
  const pills = [
    ...state.tags.map((tag) => [`tag:${tag}`, tagTitle(tag)]),
    state.platform && ["platform", platformName(state.platform)],
    (state.from || state.to) && ["period", t(state.dateField === "published_at" ? "filter.publishedRange" : "filter.addedRange",
      { range: [state.from, state.to].map((day) => (day ? dateLabel(day) : "…")).join(" – ") })],
    state.fullText && ["text", t("filter.textPill")],
  ].filter(Boolean);
  $("#active-filter").hidden = !pills.length;
  reconcile($("#active-filter"), pills.map(([kind, label]) =>
    `<button data-key="${escapeHTML(kind)}" class="filter-pill" data-clear="${escapeHTML(kind)}">${escapeHTML(label)} ${icon("close")}<span class="sr-only">${t("filter.remove")}</span></button>`).join(""));
  $("#filter-count").textContent = filterCount() || "";
  // A filter in use stays visible on its button, even with the panel closed.
  $("#toggle-filters").classList.toggle("active", filterCount() > 0);
  $("#library").classList.toggle("list", state.layout === "list");
  const toggle = $("#toggle-layout");
  toggle.innerHTML = icon(state.layout === "list" ? "library" : "list");
  toggle.setAttribute("aria-label", t(state.layout === "list" ? "layout.grid" : "layout.list"));
  toggle.title = toggle.getAttribute("aria-label");
  if ($("#trash-note")) $("#trash-note").hidden = state.view !== "trash";
  if ($("#empty-trash")) $("#empty-trash").hidden = state.view !== "trash" || !state.items.some((item) => item.deleted_at);
  renderSelection();
  if ($("#open-export")) $("#open-export").disabled = !state.loaded || state.syncing;
  $("#open-player").disabled = !state.loaded || state.syncing;
  if (!state.loaded) {
    $("#library").innerHTML = `<p class="loading">${t("loading")}</p>`;
    return;
  }
  renderPlatformFilter();
  const items = visibleItems();
  const listKey = JSON.stringify([state.view, state.tags, state.query, state.sort, state.platform, state.dateField, state.from, state.to]);
  if (listKey !== state.listKey) state.shown = PAGE; // another list starts at its top
  state.listKey = listKey;
  // A selection belongs to its view: one made in the trash must never reach the library's actions.
  if (state.view !== state.selectionView) {
    state.selected.clear();
    renderSelection();
  }
  state.selectionView = state.view;
  state.visible = items;
  $("#result-count").textContent = state.syncing ? t("collection.syncing", { loaded: state.items.length, total: state.syncTotal }) : t("collection.count", { count: items.length });
  $("#library").setAttribute("aria-busy", String(state.syncing));
  reconcile($("#library"), items.slice(0, state.shown).map(card).join(""));
  showMore();
  if (state.wanted === undefined) syncAddress();
  $("#empty").hidden = items.length > 0 || state.syncing;
  $("#open-player").hidden = !items.some((item) => item.status === "ready" && attachments(item).length);
  if (!items.length) {
    const isFirst = !readOnly && state.items.length === 0 && !state.query && !state.tags.length && state.view === "all";
    const filtered = state.query || state.tags.length || filterCount();
    const kind = isFirst ? "first" : filtered ? "search" : ["active", "error", "gone", "trash"].includes(state.view) ? state.view : "default";
    $("#empty-title").textContent = t(`empty.${kind}Title`);
    $("#empty-description").textContent = kind === "default" ? "" : t(`empty.${kind}Text`);
    $("#empty-action").innerHTML = isFirst ? `${icon("plus")}${t("empty.firstAction")}` : t(filtered ? "filter.clear" : "empty.action");
    $("#empty-action").hidden = readOnly && !filtered && state.view === "all";  // the viewer has nothing to offer there
  }
}

// Adds cards while the end of the grid is near the screen, a page at a time.
function showMore() {
  const more = $("#library-more");
  while (state.shown < state.visible.length && more.getBoundingClientRect().top < innerHeight + 1200) {
    $("#library").insertAdjacentHTML("beforeend", state.visible.slice(state.shown, state.shown + PAGE).map(card).join(""));
    state.shown += PAGE;
  }
}

new IntersectionObserver((entries) => { if (entries[0].isIntersecting) showMore(); }, { rootMargin: "1200px" }).observe($("#library-more"));
addEventListener("resize", showMore);

// Thumbnails of some of the archive's media (a thread post's, a quoted post's) that open them large.
function mediaThumbs(item, media, indexes) {
  return `<div class="thread-media">${indexes.map((i) => `<button class="thread-thumb" data-attachment="${i}" aria-label="${escapeHTML(t("detail.showMedia", { index: i + 1, count: media.length }))}"><img src="${fileURL(item, "thumbnail", i)}" alt="" loading="lazy">${media[i]?.type === "video" ? `<span>${icon("play")}</span>` : ""}</button>`).join("")}</div>`;
}

// An address read from another server is followed only when it is a web page's.
const webURL = (url) => (/^https?:\/\//i.test(url || "") ? url : "#");

// A post shown with the archived one: the post it quotes, or the post of someone else it answers.
function besidePost(item, media, post, kind, label, link) {
  return `<blockquote class="post-quote ${kind}">${label ? `<p class="quote-label">${label}</p>` : ""}<p class="quote-author">${escapeHTML(post.author || "")}${post.handle ? ` <span>@${escapeHTML(post.handle)}</span>` : ""}</p>${post.text ? `<p class="thread-text">${linkify(escapeHTML(post.text))}</p>` : ""}${post.media?.length ? mediaThumbs(item, media, post.media) : ""}<a class="text-link" href="${escapeHTML(webURL(post.url))}" target="_blank" rel="noreferrer">${link}</a></blockquote>`;
}

function postsSection(item, media) {
  const posts = item.posts || [];
  const many = posts.length > 1;
  if (!posts.length || (!many && !posts[0].text && !posts[0].quote && !posts[0].reply_to)) return "";
  return `<section class="post-thread"><h3>${many ? t("card.thread", { count: posts.length }) : t("detail.postText")}</h3>
    <ol class="thread">${posts.map((post, n) => `<li class="thread-post">
      ${many ? `<div class="thread-meta"><span class="thread-number">${n + 1}/${posts.length}</span>${post.published_at ? `<time datetime="${escapeHTML(post.published_at)}">${dateLabel(post.published_at)}</time>` : ""}<a class="text-link" href="${escapeHTML(webURL(post.url))}" target="_blank" rel="noreferrer">${t("detail.viewOn", { platform: escapeHTML(item.platform) })}</a></div>` : ""}
      ${post.reply_to ? besidePost(item, media, post.reply_to, "post-reply", t("detail.replyTo"), t("detail.replied")) : ""}
      ${post.text ? `<p class="thread-text">${linkify(escapeHTML(post.text))}</p>` : ""}
      ${post.quote ? besidePost(item, media, post.quote, "", "", t("detail.quoted")) : ""}
      ${many && post.media?.length ? mediaThumbs(item, media, post.media) : ""}
    </li>`).join("")}</ol></section>`;
}

function availabilityLabel(item) {
  if (item.availability === "gone") return `<span class="state gone">${t("availability.gone", { since: item.gone_since ? t("availability.since", { date: dateLabel(item.gone_since) }) : "" })}</span>`;
  if (item.availability === "online") return t("availability.online", { date: dateLabel(item.availability_checked_at) });
  if (item.availability === "unknown") return t("availability.unknown", { date: dateLabel(item.availability_checked_at) });
  return t("availability.pending");
}

function integrityLabel(item) {
  if (item.integrity === "damaged") return `<span class="state gone">${t("integrity.damaged")}</span>`;
  return item.integrity === "ok" && item.integrity_checked_at ? t("integrity.ok", { date: dateLabel(item.integrity_checked_at) }) : t("integrity.pending");
}

function checkRows(item) {
  // An export carries no check dates (they change every few seconds): only a problem found is worth showing.
  const availability = !readOnly || item.availability === "gone";
  const integrity = !readOnly || item.integrity === "damaged";
  return `${availability ? `<div><dt>${t("detail.availability")}</dt><dd>${availabilityLabel(item)}</dd></div>` : ""}${integrity ? `<div><dt>${t("detail.integrity")}</dt><dd>${integrityLabel(item)}</dd></div>` : ""}`;
}

function tagsAndNote(item) {
  if (readOnly) {
    return `${item.tags.length ? `<p class="detail-tags-label">${t("tags.heading")}</p><div id="detail-tags" class="detail-tags"></div>` : ""}
    ${item.note ? `<p class="detail-tags-label note-label">${t("detail.note")}</p><p class="note-text">${escapeHTML(item.note)}</p>` : ""}
    <p id="detail-feedback" class="inline-error" role="status" hidden></p>`;
  }
  return `<p class="detail-tags-label">${t("tags.heading")}</p>
    <div id="detail-tags" class="detail-tags"></div>
    <form id="tag-form" class="tag-form"><label class="sr-only" for="new-tag">${t("detail.addTag")}</label><input id="new-tag" maxlength="40" role="combobox" aria-autocomplete="list" aria-expanded="false" data-tag-suggest placeholder="${escapeHTML(t("detail.addTagPlaceholder"))}" autocomplete="off"><button class="button secondary" type="submit">${t("detail.add")}</button></form>
    <label class="detail-tags-label note-label" for="note-input">${t("detail.note")}</label>
    <textarea id="note-input" class="note-input" maxlength="5000" rows="3" placeholder="${escapeHTML(t("detail.notePlaceholder"))}">${escapeHTML(item.note || "")}</textarea>
    <p class="note-status" id="note-status" role="status"></p>
    <p id="detail-feedback" class="inline-error" role="status" hidden></p>`;
}

// Downloads: the original file as the one button, every other file as a plain link, in the same order everywhere.
function downloads(item, original = "") {
  const link = (href, label, extra = "") => `<a class="text-link" href="${href}"${extra}>${label}</a>`;
  const text = !videoPlatforms.has(item.platform) && item.details?.kind !== "file";
  return `<div class="detail-links">${original}<span class="detail-files">
    ${text ? link(fileURL(item, "description"), t("detail.text")) : ""}
    ${readOnly ? "" : link(escapeHTML(archiveURL(item, "metadata")), t("detail.metadata"), ` download="${item.id}.json"`)}
    ${link(fileURL(item, "info"), t("detail.fullMetadata"))}
    ${readOnly ? "" : link(escapeHTML(archiveURL(item, "export")), t("export.one"))}</span></div>`;
}

function bottomLinks(item, href, label) {
  const actions = readOnly || activeStates.has(item.status) ? ""
    : item.deleted_at ? `<button class="text-link" id="restore-button">${icon("restore")}${t("trash.restore")}</button><button class="delete-button" id="delete-button">${icon("trash")}${t("trash.deleteForGood")}</button>`
    : `<button class="delete-button" id="delete-button">${icon("trash")}${t("detail.delete")}</button>`;
  return `${item.deleted_at ? `<p class="trash-banner">${t("trash.detail", { count: trashDaysLeft(item) })}</p>` : ""}
    <div class="detail-bottom"><a class="text-link" href="${escapeHTML(href)}" target="_blank" rel="noreferrer">${icon("external")}${label}</a><span class="detail-actions">${actions}</span></div>`;
}

function retryNote(item) {
  // A failed archive is retried automatically a few times before it is left to the user.
  if (item.retry_at) {
    const when = new Date(item.retry_at);
    const today = when.toDateString() === new Date().toDateString();
    return t("job.autoRetry", { when: when.toLocaleString(locale(), today ? { timeStyle: "short" } : { dateStyle: "medium", timeStyle: "short" }) });
  }
  return item.retries ? t("job.autoRetryDone") : "";
}

// What a ready archive lacks, as the server noted it while making it (see WARNINGS in downloader.py): it is kept
// all the same, and shown as incomplete.
const warningsOf = (item) => item.details?.warnings || [];
function warningsBlock(item) {
  const kinds = warningsOf(item);
  if (!kinds.length) return "";
  return `<div class="notice detail-warning">${icon("alert")}<div><p class="detail-warning-title">${t("warning.title")}</p>
      <ul>${kinds.map((kind) => `<li>${escapeHTML(t(`warning.${kind}`, { platform: platformName(item.platform) }))}</li>`).join("")}</ul>
      ${readOnly ? "" : `<p>${t("warning.hint")}</p>`}</div></div>`;
}

function jobBlock(item) {
  // The server's messages say what happened and what to do; raw tool output stays folded.
  const readable = item.error && item.error.length <= 400 && !item.error.includes("\n");
  return `<div class="detail-job ${item.status === "error" ? "error" : ""}">${icon(item.status === "error" ? "alert" : "download")}<p id="detail-status">${statusName(item.status)}</p>
      ${item.status === "error" ? `<p>${readable ? escapeHTML(item.error) : t("job.errorText")}</p>${retryNote(item) ? `<p class="retry-note">${escapeHTML(retryNote(item))}</p>` : ""}${readable ? "" : `<details><summary>${t("job.details")}</summary><pre class="error-details">${escapeHTML(item.error)}</pre></details>`}<button class="button secondary" id="retry-button">${icon("download")}${t("job.retry")}</button>` : `<p>${t("job.running")}</p>`}</div>`;
}

const articleCache = new Map();

async function loadArticle(item) {
  const target = $("#article-body");
  try {
    // The article's images point at the API: in a folder they are the archive's own files, and a private
    // archive's images need the unlock token. Each one is marked with its place among the archive's media.
    const images = (html) => html.replace(/"\/api\/media\/([^/"]+)\/file\/playback\/(\d+)"/g,
      (match, id, index) => id === item.id ? `"${escapeHTML(fileURL(item, "playback", Number(index)))}" data-zoom="${Number(index)}"` : match);
    if (readOnly) {
      articleCache.set(item.id, images(item.article_html || ""));
    } else if (!articleCache.has(item.id)) {
      const url = fileURL(item, "article");
      const response = await fetch(url);
      if (!response.ok) throw new Error(response.statusText);
      const html = await response.text();
      // A lock changes the private media URL; a resync can also remove the archive while it loads.
      if (url !== fileURL(item, "article") || !state.items.some((entry) => entry.id === item.id)) return;
      articleCache.set(item.id, images(html));
    }
    // The article is built server-side from a fixed set of tags, with local images only.
    if (!target.isConnected) return;
    target.innerHTML = articleCache.get(item.id);
    // An image is a button that shows it large, as the media of a thread are.
    target.querySelectorAll("img[data-zoom]").forEach((image) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "article-zoom";
      button.dataset.zoom = image.dataset.zoom;
      button.setAttribute("aria-label", t("detail.showMedia", { index: Number(image.dataset.zoom) + 1, count: attachments(item).length }));
      image.replaceWith(button);
      button.append(image);
    });
  } catch {
    if (target.isConnected) target.innerHTML = `<p class="inline-error">${t("detail.articleError")}</p>`;
  }
}

function renderArticle(item) {
  const ready = item.status === "ready";
  const details = item.details || {};
  const byline = [item.author, item.published_at ? dateLabel(item.published_at) : null,
    details.modified ? t("detail.edited", { date: dateLabel(details.modified) }) : null].filter(Boolean).map(escapeHTML).join(" · ");
  detailSignature = detailSignatureOf(item);
  $("#detail-content").innerHTML = `
    ${ready ? "" : jobBlock(item)}
    <div class="detail-body reader">
      <article class="article-view">
        <p class="article-site">${icon("globe")}${escapeHTML(siteName(item))}</p>
        ${titleRow(item)}
        ${originalTitle(item)}
        ${byline ? `<p class="detail-author">${byline}</p>` : ""}
        ${details.via === "feed" ? `<p class="article-note">${t("detail.feed", { excerpt: details.excerpt ? t("detail.feedExcerpt") : "" })}</p>` : ""}
        ${ready ? `<div class="article-body" id="article-body"><p class="loading">${t("loading")}</p></div>` : ""}
      </article>
      ${warningsBlock(item)}
      <dl class="detail-metadata">
        <div><dt>${t("date.published")}</dt><dd>${dateLabel(item.published_at)}</dd></div>
        <div><dt>${t("date.added")}</dt><dd>${dateLabel(item.created_at)}</dd></div>
        ${ready ? checkRows(item) : ""}
        ${details.words ? `<div><dt>${t("detail.reading")}</dt><dd>${t("detail.readingValue", { minutes: details.minutes, words: details.words.toLocaleString(locale()) })}</dd></div>` : ""}
        ${ready ? `<div><dt>${t("detail.images")}</dt><dd>${item.attachments.length}</dd></div>` : ""}
      </dl>
      ${tagsAndNote(item)}
      ${ready ? downloads(item) : ""}
      ${bottomLinks(item, details.url || item.source_url, t("detail.viewSource"))}
    </div>`;
  renderDetailTags(item);
  if (ready) loadArticle(item);
}

function descriptionSection(item) {
  if (!item.description) return "";
  // Long video descriptions (links, chapters…) start folded to keep the details in view.
  const long = item.description.length > 600 || item.description.split("\n").length > 8;
  return `<section class="post-thread"><h3>${t("detail.description")}</h3>
    <p class="thread-text description-text ${long ? "clamped" : ""}" id="description-text">${linkify(escapeHTML(item.description))}</p>
    ${long ? `<button class="text-link description-toggle" data-toggle-description aria-expanded="false" aria-controls="description-text">${t("detail.showDescription")}</button>` : ""}</section>`;
}

// A video's subtitle tracks, kept next to it: only the archive of a single video has some.
const subtitlesOf = (item) => item.details?.subtitles || [];

// A track's language as browsers know it: a platform may have added a mark of its own to it ("en-nP7-2PuUl7o").
function subtitleLanguage(track) {
  const code = String(track.language || "");
  try { return Intl.getCanonicalLocales(code)[0] || code; } catch { return code.split("-")[0]; }
}

function subtitleLabel(track) {
  let name = subtitleLanguage(track);
  try { name = new Intl.DisplayNames([locale()], { type: "language" }).of(name) || name; } catch {}
  return track.automatic ? t("subtitles.automatic", { language: name }) : name;
}

// Subtitles are kept as the platform gives them, with the place it sets for each line (automatic transcripts
// start every line at the left edge): shown here, they are centred at the bottom like any subtitles.
document.addEventListener("load", (event) => {
  if (event.target.tagName !== "TRACK") return;
  for (const cue of event.target.track?.cues || []) {
    cue.align = "center";
    cue.position = "auto";
    cue.line = "auto";
    cue.size = 100;
  }
}, true);

function subtitleTracks(item) {
  return subtitlesOf(item).map((track, i) => `<track kind="subtitles" src="${fileURL(item, "subtitles", i)}" srclang="${escapeHTML(subtitleLanguage(track))}" label="${escapeHTML(subtitleLabel(track))}">`).join("");
}

// A PDF is read in place where the browser has a reader that works inside a page, which is on computers. Phones and
// tablets show its first page instead and open it in their own reader, as does a browser without one.
function pdfStage(item, index, selected) {
  const url = fileURL(item, "playback", index);
  if (navigator.pdfViewerEnabled && !matchMedia("(pointer: coarse)").matches) {
    return `<div class="document-reader"><iframe src="${url}" title="${escapeHTML(titleOf(item))}"></iframe><a class="text-link" href="${url}" target="_blank" rel="noopener">${icon("external")}${t("detail.openPdf")}</a></div>`;
  }
  return `<a class="document-wrap" href="${url}" target="_blank" rel="noopener">${hasThumbnail(item, selected) ? `<img src="${fileURL(item, "thumbnail", index)}" alt="">` : `<span class="file-mark">${icon("file")}</span>`}<span class="button secondary">${icon("external")}${t("detail.openPdf")}</span></a>`;
}

function mediaStage(item, media, index, selected) {
  const view = selected.type === "image"
    ? `<div class="image-wrap"><img src="${fileURL(item, "playback", index)}" alt="${escapeHTML(selected.alt_text || t("detail.photoAlt", { index: index + 1 }))}"></div>`
    : selected.type === "pdf"
    ? pdfStage(item, index, selected)
    // A sound plays in a video element too: it shows the cover and shares the video shortcuts.
    : `<div class="video-wrap ${selected.type === "audio" ? "audio" : ""}">${hasThumbnail(item, selected) ? "" : `<span class="file-mark">${icon("audio")}</span>`}<video controls playsinline preload="metadata" ${hasThumbnail(item, selected) ? `poster="${fileURL(item, "thumbnail", index)}"` : ""} src="${fileURL(item, "playback", index)}">${subtitleTracks(item)}</video></div>`;
  const arrows = media.length > 1 ? `<button class="media-arrow previous" data-step="-1" aria-label="${t("detail.previous")}" ${index === 0 ? "disabled" : ""}>${icon("previous")}</button><button class="media-arrow next" data-step="1" aria-label="${t("detail.next")}" ${index === media.length - 1 ? "disabled" : ""}>${icon("next")}</button>` : "";
  const gallery = media.length > 1 ? `<div class="gallery-nav" role="group" aria-label="${t("detail.mediaOf")}">${media.map((asset, i) => `<button class="gallery-button ${i === index ? "selected" : ""}" data-attachment="${i}" aria-label="${escapeHTML(t("detail.showMedia", { index: i + 1, count: media.length }))}" aria-pressed="${i === index}"><img src="${fileURL(item, "thumbnail", i)}" alt="">${asset.type === "video" ? `<span>${icon("play")}</span>` : ""}</button>`).join("")}<span class="gallery-count">${index + 1} / ${media.length}</span></div>` : "";
  return `<div class="media-stage${media.length > 1 ? " several" : ""}">${view}${arrows}</div>${gallery}`;
}

function renderDetail(item) {
  if (!item) return;
  if (isArticle(item)) return renderArticle(item);
  const ready = item.status === "ready";
  const media = attachments(item);
  state.currentAttachment = Math.max(0, Math.min(state.currentAttachment, media.length - 1));
  const index = state.currentAttachment;
  const selected = media[index];
  $("#detail-content video")?.pause();
  detailSignature = detailSignatureOf(item);
  const byline = item.platform === "Web" ? [item.author, siteName(item)].filter(Boolean).join(" · ")
    : item.author ? t("detail.author", { author: item.author, platform: platformName(item.platform) })
    : activeStates.has(item.status) ? t("detail.loadingAuthor") : platformName(item.platform);
  $("#detail-content").innerHTML = `
    ${ready ? (selected ? mediaStage(item, media, index, selected) : "") : jobBlock(item)}
    <div class="detail-body">
      <div class="detail-heading"><div>${titleRow(item)}${originalTitle(item)}<p class="detail-author">${escapeHTML(byline)}</p></div></div>
      ${postsSection(item, media) || descriptionSection(item)}
      ${warningsBlock(item)}
      <dl class="detail-metadata">
        <div><dt>${t("date.published")}</dt><dd>${dateLabel(item.published_at)}</dd></div>
        <div><dt>${t("date.added")}</dt><dd>${dateLabel(item.created_at)}</dd></div>
        ${ready ? checkRows(item) : ""}
        ${selected ? `${["audio", "pdf"].includes(selected.type) ? "" : `<div><dt>${t("detail.quality")}${media.length > 1 ? ` (${index + 1}/${media.length})` : ""}</dt><dd>${selected.height ? `${selected.width || "?"} × ${selected.height}` : t("detail.analyzing")}</dd></div>`}
        <div><dt>${t("detail.file")}</dt><dd>${selected.size ? sizeLabel(selected.size) : t("detail.preparing")}${selected.duration ? ` / ${durationLabel(selected.duration)}` : ""}</dd></div>` : ""}
        ${subtitlesOf(item).length ? `<div><dt>${t("detail.subtitles")}</dt><dd>${subtitlesOf(item).map((track, i) => `<a class="text-link" href="${fileURL(item, "subtitles", i)}" download="${escapeHTML(`${item.id}-${track.file}`)}">${escapeHTML(subtitleLabel(track))}</a>`).join(", ")}</dd></div>` : ""}
      </dl>
      ${tagsAndNote(item)}
      ${ready ? `${downloads(item, selected ? `<a class="button secondary" href="${fileURL(item, "original", index)}">${icon("download")}${t("detail.original")}${media.length > 1 ? ` (${index + 1}/${media.length})` : ""}</a>` : "")}${selected && selected.original !== selected.playback ? `<p class="playback-note">${t("detail.playbackNote")}</p>` : ""}` : ""}
      ${bottomLinks(item, sourceURL(item), sourceLabel(item))}
    </div>`;
  renderDetailTags(item);
  const video = $("#detail-content video");
  if (video) video.addEventListener("error", () => detailFeedback(t("detail.videoError")));
}

function renderDetailTags(item) {
  if (!$("#detail-tags")) return;
  $("#detail-tags").innerHTML = item.tags.map((tag) => readOnly ? `<button class="tag-chip" data-tag="${escapeHTML(tag)}" data-close-detail>${tagChip(tag)}</button>` :
    `<span class="tag-chip">${tagChip(tag)}<button class="tag-remove" data-remove-tag="${escapeHTML(tag)}" aria-label="${escapeHTML(t("detail.removeTag", { tag: tagLabel(tag) }))}">${icon("close")}</button></span>`
  ).join("");
}

function detailFeedback(message) {
  const feedback = $("#detail-feedback");
  if (feedback) { feedback.textContent = message; feedback.hidden = !message; }
}

function playSelected() {
  // Called right after a click or key press, so browsers allow playback with sound.
  $("#detail-content video")?.play().catch(() => {});
}

function openDetail(id) {
  state.currentId = id;
  state.currentAttachment = 0;
  renderDetail(currentItem());
  if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
  // Focus on the content rather than the close button: Space pauses a video or scrolls, it does not close.
  $("#detail-content").focus({ preventScroll: true });
  playSelected();
  detailPushed = syncAddress() === "push";
}

// The address holds what is shown (view, tag, search, filters, open archive) after a "#", so back, reload
// and bookmarks come back to it, in the application and in an exported folder alike.
let address = location.hash.length > 1 ? location.hash : "";
let detailPushed = false; // opening the archive added a history entry, that closing it goes back from

function addressOf() {
  const params = new URLSearchParams();
  if (state.view !== "all") params.set("view", state.view);
  state.tags.forEach((tag) => params.append("tag", tag));
  if (state.query) params.set("q", state.query);
  if (state.sort !== "newest") params.set("sort", state.sort);
  if (state.platform) params.set("platform", state.platform);
  if (state.from || state.to) params.set("date", state.dateField === "published_at" ? "published" : "added");
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  if (state.currentId) params.set("archive", state.currentId);
  const text = params.toString();
  return text ? `#${text}` : "";
}

// Another view, tag or archive is a new history entry; typing a search or setting a filter replaces it.
function syncAddress(mode = "auto") {
  const next = addressOf();
  if (next === address) return null;
  const place = (hash) => ["view", "tag", "archive"].map((key) => new URLSearchParams(hash.slice(1)).getAll(key).join(",")).join("|");
  const method = mode === "auto" && place(next) !== place(address) ? "push" : "replace";
  address = next;
  // A page opened from a disk may refuse to change its address: it then simply stays as it was.
  try { history[`${method}State`](null, "", next || location.pathname + location.search); } catch { /* see above */ }
  return method;
}

function applyAddress() {
  const params = new URLSearchParams(location.hash.slice(1));
  const day = (value) => (/^\d{4}-\d{2}-\d{2}$/.test(value || "") ? value : "");
  address = location.hash.length > 1 ? location.hash : "";
  state.view = ["active", "error", "gone", "trash"].includes(params.get("view")) ? params.get("view") : "all";
  state.tags = [...new Set(params.getAll("tag").filter(Boolean))];
  state.query = params.get("q") || "";
  const sort = params.get("sort") || storedValue("sort");
  state.sort = ["newest", "oldest", "published", "published-oldest", "title", "title-desc"].includes(sort) ? sort : "newest";
  $("#sort").value = state.sort;
  $("#search").value = state.query;
  state.platform = params.get("platform") || "";
  state.dateField = params.get("date") === "published" ? "published_at" : "created_at";
  state.from = day(params.get("from"));
  state.to = day(params.get("to"));
  syncFilters();
  state.wanted = params.get("archive"); // opened once the library is there
  detailPushed = false;
  render();
  showWanted();
}

function showWanted() {
  if (!state.loaded || state.syncing || state.wanted === undefined) return;
  const id = state.wanted;
  state.wanted = undefined;
  if (id && state.items.some((item) => item.id === id)) {
    if (state.currentId !== id) openDetail(id);
  } else if ($("#detail-dialog").open) {
    $("#detail-dialog").close();
  }
  syncAddress("replace");
}

$(".skip-link")?.addEventListener("click", (event) => {
  event.preventDefault();
  const main = $("main");
  main.focus({ preventScroll: true });
  main.scrollIntoView({ block: "start" });
});

addEventListener("popstate", applyAddress);

function setView(view, tags = []) {
  state.view = view;
  state.tags = tags;
  state.query = "";
  $("#search").value = "";
  render();
}

// On a phone, the sidebar is a drawer: the page behind is out of reach while it is open, and choosing a place closes it.
function setMenu(open) {
  const sidebar = $(".sidebar");
  if (sidebar.classList.contains("open") === open) return;
  sidebar.classList.toggle("open", open);
  $("#sidebar-backdrop").hidden = !open;
  $("#open-menu").setAttribute("aria-expanded", String(open));
  ["main", ".mobile-bar"].forEach((selector) => { $(selector).inert = open; });
  if (open) sidebar.querySelector(".nav-button:not([hidden])").focus();
  else if (sidebar.contains(document.activeElement)) $("#open-menu").focus();
}

$("#open-menu").addEventListener("click", () => setMenu(true));
$("#sidebar-backdrop").addEventListener("click", () => setMenu(false));
// Tags add up, so choosing one leaves the drawer open for the next.
$(".sidebar").addEventListener("click", (event) => { if (event.target.closest('[data-view], #settings-menu [role="menuitem"]')) setMenu(false); });
matchMedia("(min-width: 781px)").addEventListener("change", () => setMenu(false));

// Navigation, tags and cards.
document.addEventListener("click", (event) => {
  const view = event.target.closest("[data-view]");
  const tag = event.target.closest("[data-tag]");
  const cardButton = event.target.closest("[data-open]");
  // A status and tags combine, and tags add up; clicking a chosen one again removes it.
  if (view) setView(view.dataset.view === state.view ? "all" : view.dataset.view, view.dataset.view === "all" ? [] : state.tags);
  if (tag) setView(state.view, state.tags.includes(tag.dataset.tag) ? state.tags.filter((name) => name !== tag.dataset.tag) : [...state.tags, tag.dataset.tag]);
  if (cardButton) {
    // While selecting, or with Ctrl or ⌘, a click on a card selects it; Shift extends the selection up to it.
    const selects = !readOnly && cardButton.classList.contains("card-open") && (state.selecting || event.metaKey || event.ctrlKey);
    if (selects) toggleSelected(cardButton.dataset.open, event.shiftKey ? true : undefined, event.shiftKey);
    else openDetail(cardButton.dataset.open);
  }
  const pill = event.target.closest("[data-clear]");
  if (pill) clearFilter(pill.dataset.clear);
  if (tag && tag.hasAttribute("data-close-detail")) $("#detail-dialog").close();
});

function setViewOptions(open, refocus = false) {
  $("#view-options").classList.toggle("open", open);
  $("#toggle-view-options").setAttribute("aria-expanded", String(open));
  if (refocus) $("#toggle-view-options").focus();
}
$("#toggle-view-options").addEventListener("click", () => setViewOptions(!$("#view-options").classList.contains("open")));
document.addEventListener("click", (event) => {
  if (!event.target.closest("#view-options, #toggle-view-options")) setViewOptions(false);
});
$("#view-options").addEventListener("focusout", (event) => {
  if (event.relatedTarget && !event.relatedTarget.closest("#view-options, #toggle-view-options")) setViewOptions(false);
});

// Filters by platform and by period, shown under the toolbar.
function clearFilter(kind) {
  if (kind.startsWith("tag:")) state.tags = state.tags.filter((tag) => tag !== kind.slice(4));
  if (kind === "platform" || kind === "all") state.platform = "";
  if (kind === "period" || kind === "all") { state.from = ""; state.to = ""; }
  if (kind === "text" || kind === "all") setFullText(false);
  syncFilters();
  render();
}

// Remembered on this device: reading every article's text is only worth it for those who search them.
function setFullText(on) {
  state.fullText = on;
  try { localStorage.setItem("search-text", on ? "1" : "0"); } catch { /* per-visit only */ }
  if (on) loadArticleTexts();
}

// The platform filter offers the platforms the library holds, and the one filtered on if its archives are gone.
function renderPlatformFilter() {
  const select = $("#filter-platform");
  const names = [...new Set(state.items.map((item) => item.platform))];
  if (state.platform && !names.includes(state.platform)) names.push(state.platform);
  names.sort((a, b) => (a === "Web") - (b === "Web") || a.localeCompare(b));
  if (select.dataset.platforms === names.join("\n")) return;
  select.dataset.platforms = names.join("\n");
  [...select.options].slice(1).forEach((option) => option.remove());
  // The web's own entry keeps its text key, so a change of language renames it with the rest of the page.
  select.insertAdjacentHTML("beforeend", names.map((name) => `<option value="${escapeHTML(name)}"${name === "Web" ? ' data-i18n="card.web"' : ""}>${escapeHTML(platformName(name))}</option>`).join(""));
  select.value = state.platform;
}

function syncFilters() {
  renderPlatformFilter();
  $("#search-text").checked = state.fullText;
  $("#filter-platform").value = state.platform;
  $("#filter-date-field").value = state.dateField;
  $("#filter-from").value = state.from;
  $("#filter-to").value = state.to;
}

$("#toggle-filters").addEventListener("click", () => {
  const panel = $("#filter-panel");
  panel.hidden = !panel.hidden;
  $("#toggle-filters").setAttribute("aria-expanded", String(!panel.hidden));
});
$("#filter-panel").addEventListener("change", () => {
  state.platform = $("#filter-platform").value;
  state.dateField = $("#filter-date-field").value;
  // A period entered backwards is understood the right way round.
  [state.from, state.to] = [$("#filter-from").value, $("#filter-to").value].sort((a, b) => (a && b ? a.localeCompare(b) : 0));
  syncFilters();
  render();
});
$("#filter-clear").addEventListener("click", () => clearFilter("all"));
$("#tags-clear").addEventListener("click", () => { state.tags = []; render(); });
$("#search-text").addEventListener("change", (event) => {
  event.stopPropagation();
  setFullText(event.target.checked);
  render();
});
// Grid of thumbnails, or a compact list; remembered on this device.
$("#toggle-layout").addEventListener("click", () => {
  state.layout = state.layout === "list" ? "grid" : "list";
  try { localStorage.setItem("layout", state.layout); } catch { /* per-visit only */ }
  render();
});
$("#search").addEventListener("input", (event) => { state.query = event.target.value; render(); });
$("#tag-search").addEventListener("input", (event) => { state.tagQuery = event.target.value; renderSidebar(); });
$("#sort").addEventListener("change", (event) => {
  state.sort = event.target.value;
  try { localStorage.setItem("sort", state.sort); } catch { /* per-visit only */ }
  render();
});
$("#empty-action").addEventListener("click", () => {
  if (state.items.length === 0 && !state.query && !state.tags.length && state.view === "all") $("#url")?.focus();
  else { state.platform = ""; state.from = ""; state.to = ""; setFullText(false); syncFilters(); setView("all"); }
});

// Buttons open and close their dialog with the commandfor and command attributes; browsers without them get this.
if (!("commandForElement" in HTMLButtonElement.prototype)) {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button[commandfor]");
    const dialog = button && document.getElementById(button.getAttribute("commandfor"));
    if (!dialog) return;
    if (button.getAttribute("command") === "show-modal" && !dialog.open) dialog.showModal();
    else if (button.getAttribute("command") === "close") dialog.close();
  });
}

// Detail dialog.
$("#detail-dialog").addEventListener("close", () => {
  const video = $("#detail-content video");
  if (video) { video.pause(); video.removeAttribute("src"); video.load(); }
  state.currentId = null;
  // Closing what was opened from the library goes back to it, rather than adding yet another entry.
  if (detailPushed) { detailPushed = false; address = addressOf(); history.back(); } else if (state.wanted === undefined) syncAddress("replace");
});
// A click on the backdrop, outside a dialog's box, closes it; a drag that starts inside (selecting text) does not.
// The closedby attribute asks the browser for it; this is for those that do not know the attribute.
if (!("closedBy" in HTMLDialogElement.prototype)) document.querySelectorAll("dialog").forEach((dialog) => {
  const outside = (event) => {
    const rect = dialog.getBoundingClientRect();
    return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
  };
  let pressedOutside = false;
  dialog.addEventListener("pointerdown", (event) => { pressedOutside = outside(event); });
  dialog.addEventListener("click", (event) => {
    if (pressedOutside && outside(event)) dialog.close();
    pressedOutside = false;
  });
});

function focusArrow(step) {
  // Keep focus on an arrow, even when the one in this direction becomes disabled at the end.
  ($(`[data-step="${step}"]:not(:disabled)`) || $("[data-step]:not(:disabled)"))?.focus({ preventScroll: true });
}

function showAttachment(index) {
  const item = currentItem();
  if (!item || item.status !== "ready" || isArticle(item)) return false;
  const count = attachments(item).length;
  if (index < 0 || index >= count || index === state.currentAttachment) return false;
  state.currentAttachment = index;
  renderDetail(item);
  playSelected();
  return true;
}

// A medium of a thread, opened from its post: shown large over the archive, which stays where it was being read.
function openMedium(index) {
  const item = currentItem();
  const media = item ? attachments(item) : [];
  const medium = media[index];
  if (!medium) return;
  $("#detail-content .media-stage video")?.pause();
  $("#media-content").innerHTML = medium.type === "image"
    ? `<img src="${fileURL(item, "playback", index)}" alt="${escapeHTML(medium.alt_text || t("detail.photoAlt", { index: index + 1 }))}">`
    : `<video controls playsinline ${hasThumbnail(item, medium) ? `poster="${fileURL(item, "thumbnail", index)}"` : ""} src="${fileURL(item, "playback", index)}">${subtitleTracks(item)}</video>`;
  $("#media-dialog").setAttribute("aria-label", t("detail.showMedia", { index: index + 1, count: media.length }));
  $("#media-dialog").showModal();
  // Called right after a click, so browsers allow playback with sound.
  $("#media-content video")?.play().catch(() => {});
}

// A click anywhere but on a video's controls closes it.
$("#media-dialog").addEventListener("click", (event) => { if (!event.target.closest("video")) $("#media-dialog").close(); });
$("#media-dialog").addEventListener("close", () => { $("#media-content").innerHTML = ""; });

$("#detail-content").addEventListener("click", (event) => {
  const gallery = event.target.closest("[data-attachment]");
  const zoom = event.target.closest(".article-zoom");
  if (zoom) openMedium(Number(zoom.dataset.zoom));
  else if (gallery?.classList.contains("thread-thumb")) openMedium(Number(gallery.dataset.attachment));
  else if (gallery && showAttachment(Number(gallery.dataset.attachment))) {
    $(`.gallery-button[data-attachment="${state.currentAttachment}"]`)?.focus({ preventScroll: true });
  }
  const arrow = event.target.closest("[data-step]");
  if (arrow && showAttachment(state.currentAttachment + Number(arrow.dataset.step))) focusArrow(arrow.dataset.step);
  const toggle = event.target.closest("[data-toggle-description]");
  if (toggle) {
    const expanded = $("#description-text").classList.toggle("clamped") === false;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.textContent = t(expanded ? "detail.hideDescription" : "detail.showDescription");
  }
});

// Full-screen player: one archive per screen, swiped or scrolled vertically.
const player = { items: [], index: 0, observer: null, rendered: 0, opener: null, timer: null,
  autoplay: storedValue("autoplay") === "1", info: storedValue("player-info") === "1", fill: storedValue("player-fill") === "1" };
const PLAYER_BATCH = 20; // slides added at a time, a few slides before the last one is reached
const PHOTO_SECONDS = 5; // how long autoplay shows a photo

// Autoplay: at the end of a video, or after a few seconds on a photo, the next medium of the post, then the next post.
function armAutoplay() {
  clearTimeout(player.timer);
  const video = playerVideo();
  if (video) {
    video.loop = !player.autoplay;
    video.onended = player.autoplay ? playerNext : null;
  } else if (player.autoplay) {
    player.timer = setTimeout(playerNext, PHOTO_SECONDS * 1000);
  }
}

function playerNext() {
  const holder = $("#player-track").children[player.index]?.querySelector(".player-media");
  if (holder && Number(holder.dataset.attachment) < attachments(player.items[player.index]).length - 1) playerSideStep(1);
  else if (player.index < player.items.length - 1) playerStep(1);
}

// The information panel: open or closed for every medium alike, until changed.
function renderPlayerInfo() {
  const panel = $("#player-info");
  $("#player").classList.toggle("with-info", player.info);
  $("#player-info-toggle").setAttribute("aria-pressed", String(player.info));
  $("#player-info-toggle").classList.toggle("on", player.info);
  panel.hidden = !player.info;
  const item = player.items[player.index];
  if (!player.info || !item) return;
  const text = (item.posts?.length ? item.posts.map((post) => post.text).filter(Boolean).join("\n\n") : item.description) || "";
  panel.innerHTML = `<h2 class="player-info-title">${escapeHTML(titleOf(item))}</h2>
    <p class="player-info-author">${escapeHTML(item.author ? t("detail.author", { author: item.author, platform: platformName(item.platform) }) : platformName(item.platform))}</p>
    <dl class="player-info-dates"><div><dt>${t("date.published")}</dt><dd>${dateLabel(item.published_at)}</dd></div><div><dt>${t("date.added")}</dt><dd>${dateLabel(item.created_at)}</dd></div></dl>
    ${text ? `<p class="player-info-text">${linkify(escapeHTML(text))}</p>` : ""}
    ${item.tags.length ? `<div class="player-info-tags">${item.tags.map((tag) => `<span class="tag-chip">${tagChip(tag)}</span>`).join("")}</div>` : ""}
    ${item.note ? `<p class="player-info-label">${t("detail.note")}</p><p class="player-info-text">${escapeHTML(item.note)}</p>` : ""}
    <a class="text-link player-info-source" href="${escapeHTML(sourceURL(item))}" target="_blank" rel="noreferrer">${icon("external")}${sourceLabel(item)}</a>`;
  panel.scrollTop = 0;
}

function togglePlayerInfo() {
  player.info = !player.info;
  try { localStorage.setItem("player-info", player.info ? "1" : "0"); } catch { /* per-visit only */ }
  renderPlayerInfo();
}

function renderAutoplay() {
  $("#player-autoplay").setAttribute("aria-pressed", String(player.autoplay));
  $("#player-autoplay").classList.toggle("on", player.autoplay);
}

// Photos at their own size, or enlarged to the screen as videos are.
function renderFill() {
  $("#player").classList.toggle("fill", player.fill);
  $("#player-fill").setAttribute("aria-pressed", String(player.fill));
  $("#player-fill").classList.toggle("on", player.fill);
}

function playerItems() {
  return visibleItems().filter((item) => item.status === "ready" && !isArticle(item) && attachments(item).length && attachments(item)[0].type !== "pdf");
}

function playerSlide(item, position) {
  const media = attachments(item);
  return `<section class="player-slide" data-position="${position}" data-id="${item.id}">
    <div class="player-media" data-attachment="0">${playerMedium(item, 0)}</div>
    ${media.length > 1 ? `<button class="media-arrow previous player-side" data-side="-1" aria-label="${t("detail.previous")}">${icon("previous")}</button><button class="media-arrow next player-side" data-side="1" aria-label="${t("detail.next")}">${icon("next")}</button><span class="player-dots">${media.map((_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("")}</span>` : ""}
  </section>`;
}

function playerMedium(item, index) {
  const medium = attachments(item)[index];
  if (medium.type === "image") return `<img src="${fileURL(item, "playback", index)}" alt="${escapeHTML(medium.alt_text || "")}" loading="lazy">`;
  return `${hasThumbnail(item, medium) ? "" : `<span class="file-mark">${icon("audio")}</span>`}<video playsinline preload="metadata" loop ${hasThumbnail(item, medium) ? `poster="${fileURL(item, "thumbnail", index)}"` : ""} data-src="${fileURL(item, "playback", index)}"></video>
    <span class="player-paused" aria-hidden="true">${icon("play")}</span>
    <input class="player-progress" type="range" min="0" max="1000" value="0" aria-label="${escapeHTML(t("player.progress"))}">`;
}

const playerVideo = () => $("#player-track").children[player.index]?.querySelector(".player-media video");

function playerTogglePause() {
  const video = playerVideo();
  if (video) { if (video.paused) video.play().catch(() => {}); else video.pause(); }
}

// Moves the current video by `seconds`; false when the current medium is not a video.
function playerSeek(seconds) {
  const video = playerVideo();
  if (!video?.duration) return false;
  video.currentTime = Math.min(Math.max(video.currentTime + seconds, 0), video.duration);
  return true;
}

// The progress bar follows the current video every frame, except while it is being dragged.
function tickProgress() {
  if ($("#player").hidden) return;
  const video = playerVideo();
  const bar = video?.parentElement.querySelector(".player-progress");
  if (bar && video.duration && !player.seeking) {
    bar.value = Math.round((video.currentTime / video.duration) * 1000);
    bar.style.setProperty("--progress", `${(video.currentTime / video.duration) * 100}%`);
    const text = `${durationLabel(video.currentTime)} / ${durationLabel(video.duration)}`;
    if (bar.getAttribute("aria-valuetext") !== text) bar.setAttribute("aria-valuetext", text);
  }
  requestAnimationFrame(tickProgress);
}

function playerCaption(item) {
  $("#player-caption").innerHTML = `<span class="player-platform">${icon(platformIcons[item.platform])}</span><span class="player-author">${escapeHTML(item.author || (item.platform === "Web" ? siteName(item) : item.platform))}</span><span class="player-title">${escapeHTML(titleOf(item))}</span>`;
  $("#player-position").textContent = t("player.mediaOf", { index: player.index + 1, count: player.items.length });
  $("#player-up").disabled = player.index === 0;
  $("#player-down").disabled = player.index === player.items.length - 1;
}

function addSlides(count) {
  const track = $("#player-track");
  const end = Math.min(player.items.length, player.rendered + count);
  track.insertAdjacentHTML("beforeend", player.items.slice(player.rendered, end).map((item, i) => playerSlide(item, player.rendered + i)).join(""));
  [...track.children].slice(player.rendered).forEach((slide) => player.observer.observe(slide));
  player.rendered = end;
}

function activateSlide(position) {
  player.index = position;
  if (position >= player.rendered - 3) addSlides(PLAYER_BATCH);
  const item = player.items[position];
  document.querySelectorAll(".player-slide").forEach((slide) => {
    const video = slide.querySelector("video");
    if (!video) return;
    const active = Number(slide.dataset.position) === position;
    if (active) {
      if (!video.src) video.src = video.dataset.src;
      video.play().catch(() => {});
    } else {
      video.pause();
    }
  });
  // Keep only neighbouring videos loaded.
  document.querySelectorAll(".player-slide video").forEach((video) => {
    if (Math.abs(Number(video.closest(".player-slide").dataset.position) - position) > 1 && video.src) { video.removeAttribute("src"); video.load(); }
  });
  playerCaption(item);
  renderPlayerInfo();
  armAutoplay();
}

function openPlayer() {
  player.items = playerItems();
  if (!player.items.length) { toast(t("player.empty")); return; }
  const track = $("#player-track");
  track.innerHTML = "";
  player.observer?.disconnect();
  player.observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) {
      const position = Number(visible.target.dataset.position);
      if (position !== player.index) activateSlide(position);
    }
  }, { root: track, threshold: 0.6 });
  player.rendered = 0;
  addSlides(PLAYER_BATCH);
  // A modal view: the page behind cannot be reached until it closes, and focus then returns where it was.
  player.opener = document.activeElement;
  [...document.body.children].forEach((element) => { if (element.id !== "player") element.inert = true; });
  renderAutoplay();
  renderFill();
  $("#player").hidden = false;
  document.body.classList.add("player-open");
  track.children[0].scrollIntoView({ block: "start" });
  activateSlide(0);
  $("#player-close").focus();
  requestAnimationFrame(tickProgress);
}

function closePlayer() {
  clearTimeout(player.timer);
  [...document.body.children].forEach((element) => { element.inert = false; });
  player.observer?.disconnect();
  document.querySelectorAll(".player-slide video").forEach((video) => { video.pause(); video.removeAttribute("src"); video.load(); });
  $("#player-track").innerHTML = "";
  $("#player").hidden = true;
  document.body.classList.remove("player-open");
  player.opener?.focus();
}

function playerStep(step) {
  const target = player.index + step;
  if (target < 0 || target >= player.items.length) return;
  $("#player-track").children[target].scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  activateSlide(target);
}

function playerSideStep(step) {
  const slide = $("#player-track").children[player.index];
  const item = player.items[player.index];
  const count = attachments(item).length;
  const holder = slide.querySelector(".player-media");
  const target = Number(holder.dataset.attachment) + step;
  if (target < 0 || target >= count) return;
  holder.querySelector("video")?.pause();
  holder.dataset.attachment = target;
  holder.innerHTML = playerMedium(item, target);
  const video = holder.querySelector("video");
  if (video) { video.src = video.dataset.src; video.play().catch(() => {}); }
  slide.querySelectorAll(".player-dots i").forEach((dot, i) => dot.classList.toggle("on", i === target));
  armAutoplay();
}

$("#open-player").addEventListener("click", () => openPlayer());
$("#player-close").addEventListener("click", closePlayer);
$("#player-info-toggle").addEventListener("click", togglePlayerInfo);
$("#player-autoplay").addEventListener("click", () => {
  player.autoplay = !player.autoplay;
  try { localStorage.setItem("autoplay", player.autoplay ? "1" : "0"); } catch { /* per-visit only */ }
  renderAutoplay();
  armAutoplay();
});
$("#player-fill").addEventListener("click", () => {
  player.fill = !player.fill;
  try { localStorage.setItem("player-fill", player.fill ? "1" : "0"); } catch { /* per-visit only */ }
  renderFill();
});
$("#player-up").addEventListener("click", () => playerStep(-1));
$("#player-down").addEventListener("click", () => playerStep(1));
$("#player-track").addEventListener("click", (event) => {
  const side = event.target.closest("[data-side]");
  if (side) { playerSideStep(Number(side.dataset.side)); return; }
  if (event.target.closest("video")) playerTogglePause();
});
// The bar moves the video, by a click, a drag or the keyboard; a paused video shows a play sign.
$("#player-track").addEventListener("input", (event) => {
  const video = event.target.matches(".player-progress") && event.target.parentElement.querySelector("video");
  if (video?.duration) video.currentTime = (event.target.value / 1000) * video.duration;
});
$("#player-track").addEventListener("pointerdown", (event) => { if (event.target.matches(".player-progress")) player.seeking = true; });
addEventListener("pointerup", () => { player.seeking = false; });
["play", "pause"].forEach((type) => $("#player-track").addEventListener(type, (event) => {
  event.target.closest(".player-media")?.classList.toggle("paused", type === "pause" && !event.target.ended);
}, true));
// Horizontal swipes move between the media of one post.
let swipeStart = null;
$("#player-track").addEventListener("touchstart", (event) => { swipeStart = { x: event.touches[0].clientX, y: event.touches[0].clientY }; }, { passive: true });
$("#player-track").addEventListener("touchend", (event) => {
  if (!swipeStart) return;
  const dx = event.changedTouches[0].clientX - swipeStart.x;
  const dy = event.changedTouches[0].clientY - swipeStart.y;
  swipeStart = null;
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) playerSideStep(dx < 0 ? 1 : -1);
}, { passive: true });

// Keyboard.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if ($("#view-options").classList.contains("open")) { setViewOptions(false, true); return; }
    if ($("#tag-menu") && !$("#tag-menu").hidden) { closeTagMenu(true); return; }
    if ($("#card-menu") && cardMenuOpen()) { closeCardMenu(true); return; }
    if (!$("#settings-menu").hidden) { setSettings(false, true); return; }
    // Close the topmost dialog ourselves: native Escape handling is lost when focus leaves it.
    const dialog = ["#media-dialog", "#title-dialog", "#delete-dialog", "#tag-dialog", "#private-dialog", "#account-dialog", "#shortcuts-dialog", "#capture-dialog", "#about-dialog", "#export-dialog", "#duplicate-dialog", "#detail-dialog"].map((selector) => $(selector)).find((element) => element?.open);
    if (dialog) { event.preventDefault(); dialog.close(); return; }
    if ($(".sidebar").classList.contains("open")) { setMenu(false); return; }
    if (!$("#player").hidden) { closePlayer(); return; }
    if (state.selecting) setSelecting(false);
    return;
  }
  const typing = event.target.closest("input:not([type=range]),textarea,select");
  // Player: ↑ ↓ change posts, ← → move a video by 5 s (or change medium on a photo, or with Shift), Space pauses,
  // I shows or hides the information.
  if (!$("#player").hidden && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
    const keys = { ArrowUp: () => playerStep(-1), ArrowDown: () => playerStep(1),
      ArrowLeft: () => (event.shiftKey || !playerSeek(-5)) && playerSideStep(-1),
      ArrowRight: () => (event.shiftKey || !playerSeek(5)) && playerSideStep(1),
      " ": playerTogglePause, i: togglePlayerInfo, I: togglePlayerInfo };
    if (keys[event.key]) { event.preventDefault(); keys[event.key](); }
    return;
  }
  // An archive's view, as in the player: on a video, ← → move it by 5 s and Space pauses; Shift + ← →, or ← → on a
  // photo, change medium. Text fields keep their keys, and a focused video its own controls.
  const detailOnTop = $("#detail-dialog").open && !document.querySelector("dialog[open]:not(#detail-dialog)");
  if (detailOnTop && !event.metaKey && !event.ctrlKey && !event.altKey && !event.target.closest("input,textarea,select,video")) {
    const video = $("#detail-content .media-stage video");
    // Space still presses a focused button or link.
    if (event.key === " " && video && !event.target.closest("a,summary,button")) {
      event.preventDefault();
      if (video.paused) video.play().catch(() => {}); else video.pause();
      return;
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const step = event.key === "ArrowLeft" ? -1 : 1;
      if (video?.duration && !event.shiftKey) {
        event.preventDefault();
        video.currentTime = Math.min(Math.max(video.currentTime + step * 5, 0), video.duration);
        return;
      }
      const hadFocus = $("#detail-content").contains(event.target);
      if (showAttachment(state.currentAttachment + step)) {
        event.preventDefault();
        if (hadFocus) focusArrow(step);
      }
      return;
    }
  }
  if (event.metaKey || event.ctrlKey || event.altKey || typing || document.querySelector("dialog[open]")) return;
  if (event.key.toLowerCase() === "n" && $("#url")) { event.preventDefault(); $("#url").focus(); }
  if (event.key === "/") { event.preventDefault(); $("#search").focus(); }
  if (event.key === "?" && $("#shortcuts-dialog")) { event.preventDefault(); $("#shortcuts-dialog").showModal(); }
});

// Preferences: language and theme.
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('meta[name="color-scheme"]').content = theme;
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => { meta.content = theme === "dark" ? "#14161c" : "#fafbfc"; });
  // An icon alone: its name is for screen readers and the tooltip.
  const label = t(theme === "dark" ? "theme.light" : "theme.dark");
  $("#theme-toggle").setAttribute("aria-label", label);
  $("#theme-toggle").title = label;
  $("#theme-toggle").querySelector("span").innerHTML = icon(theme === "dark" ? "sun" : "moon");
}

$("#theme-toggle").addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("theme", theme); } catch { /* per-visit only */ }
  applyTheme(theme);
});
// The settings menu, behind the gear: the language, and in the application the export and About.
function renderSettings() {
  document.querySelectorAll("[data-language]").forEach((item) => item.setAttribute("aria-checked", String(item.dataset.language === currentLanguage)));
}

function setSettings(open, refocus = false) {
  const menu = $("#settings-menu");
  if (menu.hidden === !open) return;
  menu.hidden = !open;
  $("#settings-toggle").setAttribute("aria-expanded", String(open));
  if (open) menu.querySelector("button:not(:disabled)").focus();
  else if (refocus) $("#settings-toggle").focus();
}

// The scripts that include this file listen to "languagechange" for what is their own.
function changeLanguage(language) {
  if (language === currentLanguage) return;
  setLanguage(language);
  searchTexts = new WeakMap(); // tag names are translated
  applyTheme(document.documentElement.dataset.theme);
  renderSettings();
  render();
  if ($("#detail-dialog").open) renderDetail(currentItem());
  document.dispatchEvent(new Event("languagechange"));
}

$("#settings-toggle").addEventListener("click", () => setSettings($("#settings-menu").hidden));
document.addEventListener("click", (event) => { if (!event.target.closest("#settings-menu, #settings-toggle")) setSettings(false); });
// Captured before the item's own action, so a dialog it opens gives focus back to the gear when it closes.
$("#settings-menu").addEventListener("click", (event) => {
  const item = event.target.closest("button");
  if (!item) return;
  setSettings(false, true);
  if (item.dataset.language) changeLanguage(item.dataset.language);
}, true);
$("#settings-menu").addEventListener("keydown", (event) => {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const items = [...$("#settings-menu").querySelectorAll("button:not(:disabled)")];
  if (event.key === "Home" || event.key === "End") { items.at(event.key === "Home" ? 0 : -1).focus(); return; }
  items[(items.indexOf(document.activeElement) + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
});
$("#settings-menu").addEventListener("focusout", (event) => {
  if (event.relatedTarget && !event.relatedTarget.closest("#settings-menu, #settings-toggle")) setSettings(false);
});
