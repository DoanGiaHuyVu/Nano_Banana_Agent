/* Util */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const chat = $("#chat");
const msgTpl = $("#msgTpl");
const imgTpl = $("#imgTpl");

const promptInput = $("#promptInput");
const sendBtn = $("#sendBtn");
const plusBtn = $("#plusBtn");
const plusMenu = $("#plusMenu");
const menuGoogle = $("#menuGoogle");
const menuManga = $("#menuManga");
const menuUpload = $("#menuUpload");
const attachBtn = $("#attachBtn");
const fileInput = $("#fileInput");
const clearBtn = $("#clearBtn");
const themeBtn = $("#themeBtn");
const thumbBar = $("#thumbBar");

const viewer = $("#viewer");
const viewerImg = $("#viewerImg");
const viewerClose = $("#viewerClose");
const appRoot = $("#app");

/* State */
let attachedFiles = []; // File[]
let includeGoogle = false; // toggled via + menu
let includeManga = false; // toggled via + menu
let isGenerating = false; // Track generation state

/* Helpers */
function addBubble(role, htmlOrNode) {
  const node = msgTpl.content.cloneNode(true);
  const root = node.querySelector(".msg");
  const bubble = node.querySelector(".msg__bubble");

  root.classList.add(role);

  if (typeof htmlOrNode === "string") {
    bubble.innerHTML = htmlOrNode;
  } else {
    bubble.innerHTML = "";
    bubble.appendChild(htmlOrNode);
  }
  chat.appendChild(node);
  chat.scrollTop = chat.scrollHeight;
  return bubble; // Return bubble element for potential updates
}

function addLoadingMessage() {
  const loadingHtml = `
    <div class="loading-indicator">
      <div class="loading-spinner"></div>
      <span>Generating image...</span>
    </div>
  `;
  return addBubble("assistant", loadingHtml);
}

function removeLoadingMessage(bubbleElement) {
  const msgElement = bubbleElement.closest(".msg");
  if (msgElement) {
    msgElement.remove();
  }
}

function addImageMessage(blob, caption = "generated.png") {
  const node = imgTpl.content.cloneNode(true);
  const img = node.querySelector(".imgmsg__img");
  const cap = node.querySelector(".imgmsg__cap");
  const url = URL.createObjectURL(blob);
  img.src = url;
  img.alt = caption;
  cap.textContent = caption;
  img.addEventListener("click", () => openViewer(url));
  addBubble("assistant", node);
}

function openViewer(url) {
  // Preload to avoid any alt/placeholder flash
  const probe = new Image();
  probe.onload = () => {
    viewerImg.src = url;
    viewer.hidden = false;
    viewer.setAttribute("aria-hidden", "false");
    appRoot.classList.add("blurred");
    // lock page scroll while modal is open
    document.body.style.overflow = "hidden";
  };
  probe.onerror = () => {
    // If the image fails, don't leave the blur on
    closeViewer();
  };
  probe.src = url;
}

function closeViewer() {
  viewer.hidden = true;
  viewer.setAttribute("aria-hidden", "true");
  // remove src so the browser won't show any fallback/alt while hidden
  viewerImg.removeAttribute("src");
  appRoot.classList.remove("blurred");
  document.body.style.overflow = "";
}

/* Thumbnails */
function refreshThumbs() {
  if (!attachedFiles.length) {
    thumbBar.hidden = true;
    thumbBar.innerHTML = "";
    return;
  }
  thumbBar.hidden = false;
  thumbBar.innerHTML = "";
  attachedFiles.forEach((f, idx) => {
    const wrap = document.createElement("div");
    wrap.className = "thumb";
    const img = document.createElement("img");
    const close = document.createElement("button");
    close.className = "x";
    close.textContent = "✕";
    close.title = "Remove";
    close.addEventListener("click", () => {
      attachedFiles.splice(idx, 1);
      refreshThumbs();
    });

    if (f.type.startsWith("image/")) {
      const url = URL.createObjectURL(f);
      img.src = url;
    } else {
      // non-image file
      const blob = new Blob([`📄 ${f.name}`], { type: "text/plain" });
      img.src = URL.createObjectURL(blob);
    }
    wrap.append(img, close);
    thumbBar.appendChild(wrap);
  });
}

/* Menu logic */
function togglePlusMenu(force) {
  const open = force !== undefined ? force : plusMenu.hidden;
  plusMenu.hidden = !open;
  plusBtn.setAttribute("aria-expanded", String(open));
}

function updateMenuButtonStates() {
  // Update visual states of menu buttons
  menuGoogle.classList.toggle("active", includeGoogle);
  menuManga.classList.toggle("active", includeManga);
}

document.addEventListener("click", (e) => {
  if (plusMenu.hidden) return;
  const inside = plusMenu.contains(e.target) || plusBtn.contains(e.target);
  if (!inside) togglePlusMenu(false);
});

/* Backend helpers */
function parseFilenameFromDisposition(disposition) {
  if (!disposition) return null;
  const m = /filename\*?=(?:UTF-8'')?"?([^\";]+)"?/i.exec(disposition);
  return m ? decodeURIComponent(m[1]) : null;
}

async function fetchPreset(url) {
  // Add a tiny system line describing which preset was used
  addBubble(
    "user",
    `<p><em>Use preset:</em> ${url.includes("manga") ? "Manga" : "Google"}</p>`
  );

  const loadingBubble = addLoadingMessage();

  try {
    const res = await fetch(url, { method: "POST" });
    if (!res.ok) {
      const txt = await res.text();
      removeLoadingMessage(loadingBubble);
      addBubble(
        "assistant",
        `<p style="color:#ff8b8b">Error ${res.status}: ${txt}</p>`
      );
      return;
    }
    const disp = res.headers.get("Content-Disposition");
    const fname = parseFilenameFromDisposition(disp) || "generated.png";
    const blob = await res.blob();
    removeLoadingMessage(loadingBubble);
    addImageMessage(blob, fname);
  } catch (error) {
    removeLoadingMessage(loadingBubble);
    addBubble(
      "assistant",
      `<p style="color:#ff8b8b">Network error: ${escapeHtml(
        error.message || String(error)
      )}</p>`
    );
  }
}

async function sendPrompt() {
  if (isGenerating) return; // Prevent multiple simultaneous requests

  const text = promptInput.value.trim();
  if (!text && !attachedFiles.length && !includeGoogle && !includeManga) return;

  isGenerating = true;
  sendBtn.disabled = true;

  // show user message
  let html = "";
  if (text) html += `<p>${escapeHtml(text)}</p>`;
  if (includeGoogle || includeManga) {
    const chips = [];
    if (includeGoogle) chips.push(`<code>Google styles</code>`);
    if (includeManga) chips.push(`<code>Manga styles</code>`);
    html += `<p>${chips.join(" • ")}</p>`;
  }
  if (attachedFiles.length) {
    html += `<p><small>${attachedFiles.length} file(s) attached</small></p>`;
  }

  // Make sure we have content to display
  if (html === "") {
    html = "<p>Processing request...</p>";
  }

  // Add the user bubble
  addBubble("user", html);

  // Add loading indicator
  const loadingBubble = addLoadingMessage();

  // build multipart
  const fd = new FormData();
  if (text) fd.append("prompt", text);
  fd.append("include_default_google_styles", includeGoogle ? "true" : "false");
  fd.append("include_manga_styles", includeManga ? "true" : "false");
  // default character is off by default here; you can expose another toggle if desired
  fd.append("include_default_character", "false");
  attachedFiles.forEach((f) => fd.append("style_images", f, f.name));

  // send
  try {
    const res = await fetch("/api/generate-image", {
      method: "POST",
      body: fd,
    });

    removeLoadingMessage(loadingBubble);

    if (!res.ok) {
      const txt = await res.text();
      addBubble(
        "assistant",
        `<p style="color:#ff8b8b">Error ${res.status}: ${escapeHtml(txt)}</p>`
      );
      return;
    }
    const disp = res.headers.get("Content-Disposition");
    const fname = parseFilenameFromDisposition(disp) || "generated.png";
    const blob = await res.blob();
    addImageMessage(blob, fname);
  } catch (e) {
    removeLoadingMessage(loadingBubble);
    addBubble(
      "assistant",
      `<p style="color:#ff8b8b">Network error: ${escapeHtml(
        e.message || String(e)
      )}</p>`
    );
  } finally {
    // reset per-send (keep toggles on until user changes them)
    promptInput.value = "";
    attachedFiles = [];
    refreshThumbs();
    isGenerating = false;
    sendBtn.disabled = false;
  }
}

/* Events */
plusBtn.addEventListener("click", () => togglePlusMenu());

menuGoogle.addEventListener("click", () => {
  togglePlusMenu(false);
  // Reset both flags and set only Google
  includeGoogle = true;
  includeManga = false;
  updateMenuButtonStates();

  // Set the default Google prompt in the input field
  fetch("/api/get-default-prompt")
    .then((response) => response.json())
    .then((data) => {
      promptInput.value = data.prompt;
      promptInput.focus();
    })
    .catch(() => {
      // Fallback if API fails
      promptInput.value = "Create an infographic in isometric, colorful style";
      promptInput.focus();
    });
});

menuManga.addEventListener("click", () => {
  togglePlusMenu(false);
  // Reset both flags and set only Manga
  includeGoogle = false;
  includeManga = true;
  updateMenuButtonStates();

  // Set the default Manga prompt in the input field
  fetch("/api/get-manga-prompt")
    .then((response) => response.json())
    .then((data) => {
      promptInput.value = data.prompt;
      promptInput.focus();
    })
    .catch(() => {
      // Fallback if API fails
      promptInput.value =
        "Create a manga page with 4 panels in classic manga style";
      promptInput.focus();
    });
});

menuUpload.addEventListener("click", () => {
  togglePlusMenu(false);
  fileInput.click();
});

attachBtn.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  if (!fileInput.files?.length) return;
  attachedFiles = [...attachedFiles, ...fileInput.files];
  fileInput.value = "";
  refreshThumbs();
});

sendBtn.addEventListener("click", (e) => {
  e.preventDefault();
  sendPrompt();
});

promptInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendPrompt();
  }
});

clearBtn.addEventListener("click", () => {
  chat.innerHTML = "";
  attachedFiles = [];
  refreshThumbs();
  // Reset style flags
  includeGoogle = false;
  includeManga = false;
  updateMenuButtonStates();
});

themeBtn.addEventListener("click", () => {
  const r = document.documentElement;
  r.classList.toggle("light");
});

// Close button
viewerClose.addEventListener("click", (e) => {
  e.stopPropagation();
  closeViewer();
});

// Click outside the image closes the viewer
viewer.addEventListener("click", (e) => {
  if (e.target === viewer) closeViewer();
});

// Esc key closes the viewer
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !viewer.hidden) closeViewer();
});

$$(".suggest-card").forEach((btn) => {
  btn.addEventListener("click", () => {
    promptInput.value = btn.dataset.suggest || "";
    promptInput.focus();
  });
});

/* Toggle chips inside + menu for Google/Manga (click again to toggle on/off) */
// simple long-press to turn them off (or make your own UI chip if you prefer)
menuGoogle.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  includeGoogle = false;
  updateMenuButtonStates();
});

menuManga.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  includeManga = false;
  updateMenuButtonStates();
});

/* Sanitizer */
function escapeHtml(str) {
  return String(str).replace(
    /[&<>"']/g,
    (m) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[m])
  );
}

/* Warm ping (optional) */
fetch("/api/ping").catch(() => {});
