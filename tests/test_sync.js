// Exercise the interface's actual functions with controlled API pages and browser state.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
// The function runs from its declaration to its closing brace, the first one at the start of a line.
const start = source.indexOf("async function syncLibrary()");
const end = source.indexOf("\n}\n", start);
assert.ok(start >= 0 && end > start, "syncLibrary was not found in web/app.js");
const sync = source.slice(start, end + 2);
const item = (id) => ({ id, tags: [], status: "ready" });
const page = (values = {}) => ({ items: [], removed: [], cursor: 11, latest: 11,
  hidden_since: null, has_more: false, total: 0, unlocked: false, ...values });

function browser(pages, currentId = "hidden") {
  const detail = { open: true, close() { this.open = false; } };
  const title = { open: true, close() { this.open = false; } };
  const playerElement = { hidden: false };
  const warning = { hidden: true };
  const requests = [];
  const context = {
    state: { items: [item("hidden"), item("kept")], currentId,
      selected: new Set(["hidden", "kept"]), articleTexts: new Map([["hidden", "text"], ["kept", "text"]]),
      loaded: true, syncing: false, fullText: false },
    articleCache: new Map([["hidden", "html"], ["kept", "html"]]),
    player: { items: [item(currentId)] },
    titleDialogId: currentId, existingTags: () => [],
    version: 10, passStart: 10, startOver: false, forgetMissing: false, refreshAgain: false,
    syncedWith: "same", syncKey: () => "same", unlockToken: null,
    api: async (url) => {
      requests.push(url);
      assert.ok(pages.length, "Unexpected API request");
      const next = pages.shift();
      return typeof next === "function" ? next() : next;
    },
    $: (selector) => ({ "#detail-dialog": detail, "#title-dialog": title, "#player": playerElement, "#connection-warning": warning }[selector]),
    render() {}, showWanted() {},
    currentItem: () => context.state.items.find((entry) => entry.id === context.state.currentId),
    detailSignatureOf: () => "", detailSignature: "", document: { activeElement: null },
    closePlayer() { playerElement.hidden = true; },
  };
  vm.createContext(context);
  vm.runInContext(declaration(source, "function forgetMissingArchives()") + "\n" + sync, context);
  return { context, detail, title, playerElement, warning, requests,
    async run() {
      await vm.runInContext("syncLibrary()", context);
      assert.equal(warning.hidden, true, "Synchronization failed");
      assert.equal(pages.length, 0);
    } };
}

test("an archive made private closes its views and clears its caches after a full reload", async () => {
  const client = browser([
    page({ hidden_since: 10 }),
    page({ items: [item("kept")], total: 1, hidden_since: 10 }),
  ]);
  await client.run();
  assert.equal(client.detail.open, false);
  assert.equal(client.title.open, false);
  assert.equal(client.context.titleDialogId, null);
  assert.equal(client.playerElement.hidden, true);
  for (const collection of [client.context.state.selected, client.context.articleCache, client.context.state.articleTexts]) {
    assert.equal(collection.has("hidden"), false);
    assert.equal(collection.has("kept"), true);
  }
  assert.deepEqual(client.requests, ["/api/media/changes?since=10&limit=100", "/api/media/changes?since=0&limit=100"]);
});

test("pagination does not close an authorized archive that arrives on a later page", async () => {
  const client = browser([
    page({ hidden_since: 10 }),
    page({ cursor: 11, latest: 12, hidden_since: 10, has_more: true, total: 1 }),
    () => {
      assert.equal(client.detail.open, true);
      assert.equal(client.title.open, true);
      assert.equal(client.context.articleCache.has("kept"), true);
      return page({ cursor: 12, latest: 12, items: [item("kept")], total: 1 });
    },
  ], "kept");
  await client.run();
  assert.equal(client.detail.open, true);
  assert.equal(client.title.open, true);
  assert.equal(client.playerElement.hidden, false);
  assert.equal(client.context.articleCache.has("hidden"), false);
});

test("a server reset also removes stale views and cached content", async () => {
  const client = browser([page({ reset: true, items: [item("kept")], total: 1 })]);
  await client.run();
  assert.equal(client.detail.open, false);
  assert.equal(client.context.articleCache.has("hidden"), false);
});

const rendering = fs.readFileSync(path.join(__dirname, "../web/render.js"), "utf8");
function declaration(text, signature) {
  const start = text.indexOf(signature);
  const end = text.indexOf("\n}\n", start);
  assert.ok(start >= 0 && end > start, `${signature} was not found`);
  return text.slice(start, end + 2);
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function editing(privateArchive = true) {
  const archive = { ...item("edited"), note: "before", tags: privateArchive ? ["private"] : [] };
  const elements = {
    "#note-input": { value: "saved note" }, "#note-status": {},
    "#detail-dialog": { open: true, close() { this.open = false; } },
    "#title-dialog": { open: true, close() { this.open = false; } }, "#export-dialog": { open: false }, "#player": { hidden: true },
    "#article-body": { isConnected: true, innerHTML: "", querySelectorAll: () => [] },
  };
  const context = {
    state: { items: [archive], currentId: archive.id, unlocked: true, selected: new Set([archive.id]),
      articleTexts: new Map([[archive.id, "cached text"]]), fullText: true, query: "" },
    articleCache: new Map([[archive.id, "cached HTML"]]),
    unlockToken: "initial-token", titleDialogId: archive.id, syncedWith: "", currentLanguage: "en", readOnly: false,
    $: (selector) => elements[selector], isPrivate: (entry) => entry.tags.includes("private"),
    syncKey: () => context.unlockToken || "", currentItem: () => context.state.items.find((entry) => entry.id === archive.id),
    fileURL: () => `/article?unlock=${context.unlockToken || ""}`,
    existingTags: () => [], renderPrivateToggle() {}, render() {}, toast() {}, t: (key) => key, fold: (text) => text,
  };
  vm.createContext(context);
  vm.runInContext([
    declaration(source, "async function api("), declaration(source, "async function saveNote()"),
    declaration(source, "function forgetMissingArchives()"), declaration(source, "async function patchTags("),
    declaration(source, "async function retryArchive("),
    declaration(source, "function lockPrivate("), declaration(rendering, "function upsert("),
    declaration(rendering, "async function loadArticle("),
  ].join("\n"), context);
  return { context, archive, elements };
}

for (const privateArchive of [true, false]) {
  test(`a pending ${privateArchive ? "private" : "public"} note is saved even when private archives are locked`, async () => {
    const { context, archive, elements } = editing(privateArchive);
    const reply = deferred();
    let saved;
    context.fetch = async (url, options) => {
      if (url.endsWith("/lock")) return { ok: true, json: async () => ({}) };
      assert.equal(options.method, "PATCH");
      assert.equal(options.headers["X-Private-Unlock"], "initial-token");
      assert.equal(options.signal, undefined, "Saving must not be cancelled on lock");
      saved = { ...archive, ...JSON.parse(options.body) };
      await reply.promise;
      return { ok: true, json: async () => saved };
    };
    const saving = vm.runInContext("saveNote()", context);
    vm.runInContext("lockPrivate()", context);
    reply.resolve();
    await saving;
    assert.equal(saved.note, "saved note");
    assert.equal(context.state.unlocked, false);
    assert.equal(context.state.items.length, privateArchive ? 0 : 1);
    assert.equal(context.state.articleTexts.has(archive.id), !privateArchive);
    if (privateArchive) {
      assert.equal(elements["#detail-dialog"].open, false);
      assert.equal(elements["#title-dialog"].open, false);
      assert.equal(context.articleCache.has(archive.id), false);
      // The saved version becomes available again on the next authorized synchronization.
      context.state.unlocked = true;
      context.saved = saved;
      assert.equal(vm.runInContext("upsert(saved)", context), true);
    }
    assert.equal(context.state.items[0].note, "saved note");
  });
}

test("a private article response cannot refill the cache after locking", async () => {
  const { context, archive, elements } = editing();
  const body = deferred();
  context.articleCache.clear();
  context.api = async () => ({});
  context.fetch = async () => ({ ok: true, text: () => body.promise });
  context.archive = archive;
  const loading = vm.runInContext("loadArticle(archive)", context);
  await Promise.resolve();
  vm.runInContext("lockPrivate()", context);
  body.resolve("<p>Private article</p>");
  await loading;
  assert.equal(context.articleCache.has(archive.id), false);
  assert.equal(elements["#article-body"].innerHTML, "");
});

test("making an archive private locally clears the same views and caches as a resync", async () => {
  const { context, archive, elements } = editing(false);
  context.state.unlocked = false;
  context.archive = archive;
  context.api = async () => ({ ...archive, tags: ["private"] });
  elements["#player"].hidden = false;
  context.player = { items: [archive] };
  context.closePlayer = () => { elements["#player"].hidden = true; };
  await vm.runInContext('patchTags(archive, ["private"])', context);
  assert.equal(context.state.items.length, 0);
  assert.equal(context.state.selected.size, 0);
  assert.equal(context.articleCache.size, 0);
  assert.equal(context.state.articleTexts.size, 0);
  assert.equal(elements["#detail-dialog"].open, false);
  assert.equal(elements["#title-dialog"].open, false);
  assert.equal(elements["#player"].hidden, true);
});

test("retry refreshes its open detail but does not replace another archive opened meanwhile", async () => {
  for (const switchArchive of [false, true]) {
    const { context, archive } = editing(false);
    const reply = deferred();
    const rendered = [];
    context.api = async () => reply.promise;
    context.renderDetail = (entry) => rendered.push(entry.id);
    context.schedule = () => {};
    const retrying = vm.runInContext('retryArchive("edited")', context);
    if (switchArchive) context.state.currentId = "other";
    reply.resolve({ ...archive, status: "queued" });
    await retrying;
    assert.equal(context.state.items[0].status, "queued");
    assert.deepEqual(rendered, switchArchive ? [] : ["edited"]);
  }
});

for (const offline of [false, true]) {
  test(`articles remain readable ${offline ? "in the offline viewer" : "while unlocked"}`, async () => {
    const { context, archive, elements } = editing();
    context.articleCache.clear();
    context.readOnly = offline;
    context.archive = { ...archive, article_html: "<p>Article</p>" };
    context.fetch = async () => {
      assert.equal(offline, false);
      return { ok: true, text: async () => "<p>Article</p>" };
    };
    await vm.runInContext("loadArticle(archive)", context);
    assert.equal(elements["#article-body"].innerHTML, "<p>Article</p>");
  });
}

test("a search response from before locking is discarded and public texts are fetched again", async () => {
  const { context, archive } = editing();
  const reply = deferred();
  const publicItem = item("public");
  context.state.items.push(publicItem);
  let calls = 0;
  context.api = async (url) => {
    if (url.endsWith("/lock")) return {};
    calls += 1;
    if (calls === 1) return reply.promise;
    return { texts: { public: "Public article" } };
  };
  const start = source.indexOf("let articleTextsTask = null;");
  const end = source.indexOf("\nloadArticleTexts();", start);
  vm.runInContext(source.slice(start, end), context);
  vm.runInContext("loadArticleTexts()", context);
  const loading = vm.runInContext("articleTextsTask", context);
  vm.runInContext("lockPrivate()", context);
  reply.resolve({ texts: { [archive.id]: "Private text", public: "Public article" } });
  await loading;
  await vm.runInContext("articleTextsTask", context);
  assert.equal(calls, 2);
  assert.equal(context.state.articleTexts.has(archive.id), false);
  assert.equal(context.state.articleTexts.get("public"), "Public article");
});

test("text loading requested during an earlier request is retried for newly arrived archives", async () => {
  const { context } = editing(false);
  const reply = deferred();
  let calls = 0;
  context.api = async () => {
    calls += 1;
    return calls === 1 ? reply.promise : { texts: { edited: "First article", later: "New article" } };
  };
  const start = source.indexOf("let articleTextsTask = null;");
  vm.runInContext(source.slice(start, source.indexOf("\nloadArticleTexts();", start)), context);
  vm.runInContext("loadArticleTexts()", context);
  const loading = vm.runInContext("articleTextsTask", context);
  context.state.items.push(item("later"));
  vm.runInContext("loadArticleTexts()", context);
  reply.resolve({ texts: { edited: "First article" } });
  await loading;
  await vm.runInContext("articleTextsTask", context);
  assert.equal(calls, 2);
  assert.equal(context.state.articleTexts.get("later"), "New article");
});
