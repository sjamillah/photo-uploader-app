/* Progressive enhancement. The page works without this file: the form posts,
   the server renders, the search button navigates. Everything here is an
   upgrade on top of that. */

const body = document.body;
const cfg = {
  maxBytes: Number(body.dataset.maxBytes),
  maxDescription: Number(body.dataset.maxDescription),
};

let nextCursor = body.dataset.nextCursor || null;
let currentQuery = body.dataset.query || "";
let loading = false;

document.documentElement.classList.add("js");

const $ = (id) => document.getElementById(id);
const grid = $("grid");
const empty = $("empty");

/* --------------------------------------------------------------- ownership
   There are no accounts. Uploading returns a one-time token that lives in
   this browser, and deleting requires presenting it back. */
const TOKENS = "darkroom.tokens";

function readTokens() {
  try { return JSON.parse(localStorage.getItem(TOKENS) || "{}"); }
  catch { return {}; }          // private mode, cleared storage, blocked cookies
}
function rememberToken(id, token) {
  try {
    const all = readTokens();
    all[id] = token;
    localStorage.setItem(TOKENS, JSON.stringify(all));
  } catch { /* the delete button simply will not appear next visit */ }
}
function forgetToken(id) {
  try { const all = readTokens(); delete all[id]; localStorage.setItem(TOKENS, JSON.stringify(all)); }
  catch { /* nothing to do */ }
}

function markOwned(scope = document) {
  const mine = readTokens();
  scope.querySelectorAll("[data-delete]").forEach((button) => {
    if (mine[button.dataset.delete]) button.hidden = false;
  });
}

/* ---------------------------------------------------------------- toasts */
function toast(message, kind = "ok") {
  const node = document.createElement("div");
  node.className = `toast toast--${kind}`;
  node.textContent = message;
  $("toasts").append(node);
  setTimeout(() => node.remove(), 4500);
}

/* ------------------------------------------------------------------ time */
const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const UNITS = [["year", 31536000], ["month", 2592000], ["week", 604800],
               ["day", 86400], ["hour", 3600], ["minute", 60]];

function relativeTime(iso) {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  for (const [unit, size] of UNITS) {
    if (seconds >= size) return RELATIVE.format(-Math.floor(seconds / size), unit);
  }
  return "just now";
}

function humaniseTimes(scope = document) {
  scope.querySelectorAll("time[datetime]").forEach((node) => {
    node.title = new Date(node.dateTime).toLocaleString();
    node.textContent = relativeTime(node.dateTime);
  });
}

/* ------------------------------------------------------------ card build */
function buildCard(photo) {
  const article = document.createElement("article");
  article.className = "card is-new";
  article.dataset.id = photo.id;
  article.innerHTML = `
    <button class="card__open" type="button"
            data-full="${photo.fullUrl}" data-width="${photo.width}"
            data-height="${photo.height}" data-created="${photo.createdAt}"></button>
    <div class="card__meta">
      <p class="card__caption"></p>
      <time datetime="${photo.createdAt}"></time>
    </div>
    <button class="card__delete" type="button" data-delete="${photo.id}"
            title="Delete this photo" aria-label="Delete this photo" hidden>&times;</button>`;

  // textContent, never innerHTML: descriptions are untrusted input and this
  // is the one place XSS would get in.
  const caption = photo.description || "No description";
  article.querySelector(".card__caption").textContent = caption;
  article.querySelector(".card__open").dataset.description = photo.description || "";

  const image = new Image();
  image.src = photo.thumbUrl;
  image.alt = photo.description || "Untitled photograph";
  image.width = photo.width;
  image.height = photo.height;
  image.loading = "lazy";
  image.decoding = "async";
  article.querySelector(".card__open").append(image);

  humaniseTimes(article);
  return article;
}

/* ------------------------------------------------------------- composer */
const form = $("composer");
const fileInput = $("photo");
const dropzone = $("dropzone");
const preview = $("preview");
const thumbs = $("thumbs");
const thumbsCount = $("thumbs-count");
const description = $("description");
const counter = $("counter-value");
const submit = $("submit");
const progress = $("progress");
const progressBar = $("progress-bar");

const MAX_PHOTOS = 8;

// The selection lives here, not in the input, because a file input cannot have
// one entry removed. The input is kept in step so the form still works with
// JavaScript off, where it posts the first photograph.
let chosen = [];

description.addEventListener("input", () => {
  counter.textContent = description.value.length;
  counter.parentElement.classList.toggle(
    "is-close", description.value.length > cfg.maxDescription - 40);
});

function syncInput() {
  const transfer = new DataTransfer();
  chosen.forEach((file) => transfer.items.add(file));
  fileInput.files = transfer.files;
}

function renderThumbs() {
  thumbs.textContent = "";

  chosen.forEach((file, index) => {
    const tile = document.createElement("div");
    tile.className = "thumb";

    const image = new Image();
    image.src = URL.createObjectURL(file);
    image.alt = file.name;
    // The object URL is only needed until the browser has decoded it.
    image.addEventListener("load", () => URL.revokeObjectURL(image.src));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "thumb__remove";
    remove.title = `Remove ${file.name}`;
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      chosen.splice(index, 1);
      syncInput();
      renderThumbs();
    });

    tile.append(image, remove);
    thumbs.append(tile);
  });

  if (chosen.length && chosen.length < MAX_PHOTOS) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "thumb thumb--add";
    add.title = "Add another photograph";
    add.setAttribute("aria-label", "Add another photograph");
    add.textContent = "+";
    add.addEventListener("click", () => fileInput.click());
    thumbs.append(add);
  }

  const empty = chosen.length === 0;
  preview.hidden = empty;
  dropzone.querySelector(".dropzone__idle").hidden = !empty;
  thumbsCount.textContent = empty
    ? ""
    : `${chosen.length} of ${MAX_PHOTOS} selected`;
}

function accept(files) {
  const room = MAX_PHOTOS - chosen.length;
  if (room <= 0) {
    toast(`That is ${MAX_PHOTOS} photographs, the most one post takes.`, "error");
    return;
  }

  let rejected = 0;
  [...files]
    .filter((file) => file.type.startsWith("image/"))
    // Same name and size twice is the same photograph picked twice.
    .filter((file) => !chosen.some((had) => had.name === file.name && had.size === file.size))
    .slice(0, room)
    .forEach((file) => {
      if (file.size > cfg.maxBytes) { rejected += 1; return; }
      chosen.push(file);
    });

  if (rejected) {
    toast(`${rejected} skipped, larger than ${Math.round(cfg.maxBytes / 1048576)} MB.`, "error");
  }
  syncInput();
  renderThumbs();
}

function clearChosen() {
  chosen = [];
  syncInput();
  renderThumbs();
}

// The input reports everything picked this time round; accept() decides what
// survives, then syncInput writes the survivors back.
fileInput.addEventListener("change", () => {
  const picked = [...fileInput.files];
  fileInput.value = "";
  accept(picked);
});

["dragenter", "dragover"].forEach((event) =>
  dropzone.addEventListener(event, (e) => {
    e.preventDefault();
    dropzone.classList.add("is-hot");
  }));

["dragleave", "drop"].forEach((event) =>
  dropzone.addEventListener(event, (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-hot");
  }));

dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) accept(e.dataTransfer.files);
});

// Paste an image straight from the clipboard - a screenshot, usually.
document.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (!files.length) return;
  const before = chosen.length;
  accept(files);
  if (chosen.length > before) toast("Pasted image ready to post.");
});

// XHR rather than fetch: fetch still cannot report upload progress, which
// matters on a 10 MB photo over a slow connection. One request per photograph,
// because the API takes one at a time; the description goes with each.
function postOne(file, index, total) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const data = new FormData();
    data.append("photo", file);
    data.append("description", description.value);

    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      const done = (index + event.loaded / event.total) / total;
      progressBar.style.width = `${done * 100}%`;
    });

    request.addEventListener("load", () => {
      let payload = {};
      try { payload = JSON.parse(request.responseText); } catch { /* non-JSON error page */ }
      if (request.status === 201 && payload.photo) resolve(payload);
      else reject(new Error(payload.error || "Upload failed. Please try again."));
    });
    request.addEventListener("error", () => reject(new Error("Network error. Please try again.")));

    request.open("POST", form.action);
    request.setRequestHeader("Accept", "application/json");
    request.setRequestHeader("X-Requested-With", "fetch");
    request.send(data);
  });
}

form.addEventListener("submit", async (e) => {
  if (!chosen.length) return;         // let the browser show its own message
  e.preventDefault();

  const queue = [...chosen];
  const total = queue.length;
  let posted = 0;

  submit.disabled = true;
  progress.hidden = false;

  for (const [index, file] of queue.entries()) {
    submit.textContent = total > 1 ? `Posting ${index + 1} of ${total}...` : "Posting...";
    try {
      const payload = await postOne(file, index, total);
      rememberToken(payload.photo.id, payload.manageToken);
      const card = buildCard(payload.photo);
      grid.prepend(card);
      markOwned(card);
      empty.hidden = true;
      $("photo-count").textContent = Number($("photo-count").textContent) + 1;
      posted += 1;
      // Drop it from the selection as it lands, so a failure part way through
      // leaves exactly the photographs that still need posting.
      chosen = chosen.filter((pending) => pending !== file);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  syncInput();
  renderThumbs();

  if (posted) {
    toast(posted === 1 ? "Posted to the wall." : `Posted ${posted} photographs.`);
    if (!chosen.length) {
      description.value = "";
      counter.textContent = "0";
    }
  }
  resetComposer();
});

function resetComposer() {
  submit.disabled = false;
  submit.textContent = "Post to the wall";
  progress.hidden = true;
  progressBar.style.width = "0";
}

/* ---------------------------------------------------------------- delete */
grid.addEventListener("click", async (e) => {
  const button = e.target.closest("[data-delete]");
  if (!button) return;

  const id = button.dataset.delete;
  const token = readTokens()[id];
  if (!token) return;
  if (!confirm("Delete this photo? This cannot be undone.")) return;

  const response = await fetch(`/api/photos/${id}`, {
    method: "DELETE",
    headers: { "X-Manage-Token": token },
  });

  if (response.status === 204) {
    button.closest(".card").remove();
    forgetToken(id);
    $("photo-count").textContent = Math.max(0, Number($("photo-count").textContent) - 1);
    if (!grid.children.length) empty.hidden = false;
    toast("Photo deleted.");
  } else {
    toast("Could not delete that photo.", "error");
  }
});

/* ---------------------------------------------------------------- search */
let searchTimer;
const searchInput = $("q");

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => runSearch(searchInput.value.trim()), 250);
});

document.querySelector(".search").addEventListener("submit", (e) => {
  e.preventDefault();                 // no full page load once JS is running
  runSearch(searchInput.value.trim());
});

async function runSearch(query) {
  currentQuery = query;
  nextCursor = null;
  grid.innerHTML = "";
  showSkeletons(6);

  // Keep the URL shareable without adding a history entry per keystroke.
  const url = new URL(location.href);
  query ? url.searchParams.set("q", query) : url.searchParams.delete("q");
  history.replaceState(null, "", url);

  await loadPage({ replace: true });
}

function showSkeletons(count) {
  for (let i = 0; i < count; i += 1) {
    const node = document.createElement("div");
    node.className = "skeleton skeleton__image";
    node.dataset.skeleton = "1";
    grid.append(node);
  }
}

/* ------------------------------------------------------- infinite scroll */
async function loadPage({ replace = false } = {}) {
  if (loading) return;
  loading = true;

  const params = new URLSearchParams();
  if (currentQuery) params.set("q", currentQuery);
  if (nextCursor && !replace) params.set("cursor", nextCursor);

  try {
    const response = await fetch(`/api/photos?${params}`,
                                { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(response.statusText);
    const payload = await response.json();

    grid.querySelectorAll("[data-skeleton]").forEach((node) => node.remove());
    payload.photos.forEach((photo) => grid.append(buildCard(photo)));
    markOwned(grid);
    nextCursor = payload.nextCursor;

    const nothingHere = !grid.children.length;
    empty.hidden = !nothingHere;
    if (nothingHere) {
      $("empty-lead").textContent = currentQuery ? "Nothing matches that" : "The wall is empty";
      $("empty-hint").textContent = currentQuery
        ? "Try a different word, or clear the search."
        : "Post the first photograph.";
    }
  } catch {
    grid.querySelectorAll("[data-skeleton]").forEach((node) => node.remove());
    toast("Could not load more photos.", "error");
  } finally {
    loading = false;
  }
}

new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting && nextCursor) loadPage();
}, { rootMargin: "600px" }).observe($("sentinel"));

/* -------------------------------------------------------------- lightbox */
const lightbox = $("lightbox");
const lightboxImage = $("lightbox-image");
const lightboxCaption = $("lightbox-caption");
const lightboxTime = $("lightbox-time");
const prevButton = $("lightbox-prev");
const nextButton = $("lightbox-next");
let openIndex = -1;
let lastFocused = null;

const openers = () => [...grid.querySelectorAll(".card__open")];

function openLightbox(index) {
  const buttons = openers();
  if (index < 0 || index >= buttons.length) return;

  const button = buttons[index];
  openIndex = index;
  lastFocused = button;

  lightboxImage.src = button.dataset.full;
  lightboxImage.alt = button.dataset.description || "Photograph";
  lightboxImage.width = button.dataset.width;
  lightboxImage.height = button.dataset.height;
  lightboxCaption.textContent = button.dataset.description || "No description";
  lightboxTime.textContent = relativeTime(button.dataset.created);
  lightboxTime.dateTime = button.dataset.created;

  prevButton.disabled = index === 0;
  nextButton.disabled = index === buttons.length - 1;

  lightbox.hidden = false;
  body.style.overflow = "hidden";
  $("lightbox-close").focus();
}

function closeLightbox() {
  lightbox.hidden = true;
  body.style.overflow = "";
  lightboxImage.removeAttribute("src");   // stop a large download mid-flight
  openIndex = -1;
  lastFocused?.focus();                   // return focus where it came from
}

grid.addEventListener("click", (e) => {
  const opener = e.target.closest(".card__open");
  if (opener) openLightbox(openers().indexOf(opener));
});

$("lightbox-close").addEventListener("click", closeLightbox);
prevButton.addEventListener("click", () => openLightbox(openIndex - 1));
nextButton.addEventListener("click", () => openLightbox(openIndex + 1));
lightbox.addEventListener("click", (e) => { if (e.target === lightbox) closeLightbox(); });

document.addEventListener("keydown", (e) => {
  if (lightbox.hidden) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") openLightbox(openIndex - 1);
  if (e.key === "ArrowRight") openLightbox(openIndex + 1);

  // aria-modal="true" promises focus stays inside the dialog. Without this,
  // Tab walks into the gallery behind it.
  if (e.key === "Tab") {
    const stops = [$("lightbox-close"), prevButton, nextButton]
      .filter((button) => !button.disabled);
    const here = stops.indexOf(document.activeElement);
    const step = e.shiftKey ? -1 : 1;
    const next = (here + step + stops.length) % stops.length;
    e.preventDefault();
    stops[next === -1 ? 0 : next].focus();
  }
});

/* ------------------------------------------------------------------ init */
markOwned();
humaniseTimes();
