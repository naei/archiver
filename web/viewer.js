// The offline viewer: the library read from the catalog embedded in this page, with the files in the folders next to it.
// This file is inlined after i18n.js and render.js by archiver/viewer.py; it is never served on its own.
const catalog = JSON.parse($("#catalog").textContent);

// An archive's files are in its own folder, named after it: next to this page, or in the export's media folder.
fileURL = (item, kind, index = null) => {
  const fixed = { info: "original.info.json", description: "description.txt", article: "article.html" };
  const name = fixed[kind] || (kind === "subtitles" ? item.details?.subtitles?.[index]?.file : index === null ? item[kind] : item.attachments[index]?.[kind]);
  return `${catalog.folder || ""}${encodeURIComponent(item.id)}/${encodeURIComponent(name || "")}`;
};

const library = catalog.items.filter((item) => item.status === "ready");
state.loaded = true;

// The articles' text and the videos' subtitles are in the catalog; in an article, tags become spaces, so the words of adjacent blocks stay apart.
loadArticleTexts = () => {
  if (state.articleTexts.size) return;
  library.filter((item) => item.article_html).forEach((item) => {
    const text = new DOMParser().parseFromString(item.article_html.replace(/<[^>]*>/g, " "), "text/html").body.textContent;
    state.articleTexts.set(item.id, fold(text.split(/\s+/).join(" ")));
  });
  library.filter((item) => item.subtitle_text).forEach((item) => state.articleTexts.set(item.id, fold(item.subtitle_text)));
};
if (state.fullText) loadArticleTexts();

// Private archives start hidden. A page opened from a file cannot check a password: the lock only keeps them out of
// sight, it does not protect them.
function showPrivate(shown) {
  state.unlocked = shown;
  state.items = shown ? library : library.filter((item) => !isPrivate(item));
  if (!shown) {
    if (state.currentId && !state.items.some((item) => item.id === state.currentId)) $("#detail-dialog").close();
    if (!$("#player").hidden && player.items.some(isPrivate)) closePlayer();
    state.tags = existingTags();
  }
  renderPrivateToggle();
  render();
}

// The first rendering is left to applyAddress, below, which reads the page's address before anything replaces it.
state.items = library.filter((item) => !isPrivate(item));
renderPrivateToggle();
$("#private-toggle").addEventListener("click", () => {
  showPrivate(!state.unlocked);
  toast(t(state.unlocked ? "private.shown" : "private.hidden"));
});

function viewerTitle() {
  const date = catalog.exported_at ? dateLabel(catalog.exported_at) : "";
  document.title = t("viewer.title", { date });
  $("#viewer-date").textContent = t("viewer.date", { date });
}

document.addEventListener("languagechange", () => { viewerTitle(); renderPrivateToggle(); });
applyTranslations();
renderSettings();
applyTheme(document.documentElement.dataset.theme || "dark");
viewerTitle();
applyAddress();
