const LOCAL_QUEUE_KEY = "marko-capture-local-queue-v1";
const TOKEN_KEY = "marko-inbox-sync-token-v1";

const input = document.getElementById("thought-input");
const enterButton = document.getElementById("enter-button");
const undoButton = document.getElementById("undo-button");
const statusText = document.getElementById("status-text");
const itemCount = document.getElementById("item-count");
const itemsList = document.getElementById("items-list");
const unlockPanel = document.getElementById("unlock-panel");
const syncTokenInput = document.getElementById("sync-token");
const unlockButton = document.getElementById("unlock-button");

let submitInFlight = false;
let undoInFlight = false;
let syncInFlight = false;
let remoteItems = [];

function setStatus(message) {
  statusText.textContent = message || "";
}

function getSyncToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function authHeaders(extra = {}) {
  const token = getSyncToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

function showUnlock(message = "Enter your sync token to connect to the desktop Inbox.") {
  unlockPanel.classList.remove("hidden");
  setStatus(message);
}

function hideUnlock() {
  unlockPanel.classList.add("hidden");
}

async function responseData(response) {
  let data = {};
  try {
    data = await response.json();
  } catch {
    // An empty or non-JSON error response is still handled below.
  }
  if (response.status === 401) {
    showUnlock();
    throw new Error("Synchronization is locked.");
  }
  if (!response.ok) {
    throw new Error(data.detail || "The Inbox request failed.");
  }
  hideUnlock();
  return data;
}

function setBusyState() {
  const isBusy = submitInFlight || undoInFlight;
  enterButton.disabled = isBusy;
  undoButton.disabled = isBusy;
}

function generateId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function sortNewestFirst(items) {
  return [...items].sort((left, right) => {
    const leftTime = Date.parse(left.captured_at || "") || 0;
    const rightTime = Date.parse(right.captured_at || "") || 0;
    return rightTime - leftTime;
  });
}

function loadLocalQueue() {
  try {
    const raw = localStorage.getItem(LOCAL_QUEUE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveLocalQueue(items) {
  localStorage.setItem(LOCAL_QUEUE_KEY, JSON.stringify(items));
}

function mergeVisibleItems() {
  const localItems = loadLocalQueue().map((item) => ({ ...item, localOnly: true }));
  const syncedIds = new Set(remoteItems.map((item) => item.id));
  const unsyncedLocal = localItems.filter((item) => !syncedIds.has(item.id));
  return sortNewestFirst([...unsyncedLocal, ...remoteItems]);
}

function upsertRemoteItem(item) {
  if (!item?.id) {
    return;
  }
  const remaining = remoteItems.filter((entry) => entry.id !== item.id);
  remoteItems = sortNewestFirst([...remaining, item]);
}

function renderItems() {
  const items = mergeVisibleItems();
  itemCount.textContent = String(items.length);
  itemsList.innerHTML = "";

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No unpublished items.";
    itemsList.append(empty);
    return;
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "item-card";

    const text = document.createElement("p");
    text.className = "item-text";
    text.textContent = item.original_text;
    card.append(text);

    const meta = document.createElement("div");
    meta.className = "item-meta";

    const time = document.createElement("span");
    time.textContent = formatTimestamp(item.captured_at);
    meta.append(time);

    if (item.localOnly) {
      const pill = document.createElement("span");
      pill.className = "item-pill";
      pill.textContent = "On iPhone";
      meta.append(pill);
    }

    card.append(meta);
    itemsList.append(card);
  }
}

async function fetchRemoteItems() {
  const response = await fetch("/api/inbox/unpublished", {
    cache: "no-store",
    headers: authHeaders(),
  });
  const data = await responseData(response);
  remoteItems = Array.isArray(data.items) ? data.items : [];
}

async function refreshRemoteItems() {
  if (!navigator.onLine) {
    renderItems();
    return;
  }

  try {
    await fetchRemoteItems();
  } catch {
    // Keep rendering local state when the desktop is unreachable.
  }
  renderItems();
}

async function syncLocalQueue() {
  if (syncInFlight || !navigator.onLine) {
    return;
  }

  const pending = sortNewestFirst(loadLocalQueue()).reverse();
  if (!pending.length) {
    await refreshRemoteItems();
    return;
  }

  syncInFlight = true;
  let syncedCount = 0;

  try {
    for (const item of pending) {
      const response = await fetch("/api/inbox/unpublished", {
        method: "POST",
        cache: "no-store",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          text: item.original_text,
          client_id: item.id,
          captured_at: item.captured_at,
        }),
      });
      const data = await responseData(response);

      const remaining = loadLocalQueue().filter((queued) => queued.id !== item.id);
      saveLocalQueue(remaining);
      upsertRemoteItem(data.item);
      syncedCount += 1;
    }

    await fetchRemoteItems();
    if (syncedCount > 0) {
      setStatus(syncedCount === 1 ? "Synced 1 item." : `Synced ${syncedCount} items.`);
    }
  } catch {
    if (syncedCount === 0) {
      setStatus("Saved on iPhone. Waiting for Marko computer.");
    }
  } finally {
    syncInFlight = false;
    renderItems();
  }
}

async function refreshEverything() {
  renderItems();
  await syncLocalQueue();
  await refreshRemoteItems();
}

async function submitThought() {
  const text = input.value.trim();
  if (!text || submitInFlight) {
    return;
  }

  submitInFlight = true;
  setBusyState();

  const item = {
    id: generateId(),
    original_text: text,
    captured_at: new Date().toISOString(),
  };
  const nextQueue = sortNewestFirst([item, ...loadLocalQueue()]).reverse();
  saveLocalQueue(nextQueue);
  input.value = "";
  renderItems();

  if (navigator.onLine) {
    setStatus("Saved on iPhone. Syncing...");
    await syncLocalQueue();
  } else {
    setStatus("Saved on iPhone.");
  }

  input.focus();
  submitInFlight = false;
  setBusyState();
}

async function undoThought() {
  if (undoInFlight) {
    return;
  }

  undoInFlight = true;
  setBusyState();

  const localItems = loadLocalQueue();
  const newestLocal = sortNewestFirst(localItems)[0] || null;
  const newestRemote = sortNewestFirst(remoteItems)[0] || null;

  const localTime = newestLocal ? Date.parse(newestLocal.captured_at || "") || 0 : -1;
  const remoteTime = newestRemote ? Date.parse(newestRemote.captured_at || "") || 0 : -1;

  try {
    if (newestLocal && localTime >= remoteTime) {
      saveLocalQueue(localItems.filter((item) => item.id !== newestLocal.id));
      renderItems();
      setStatus("Removed latest iPhone item.");
    } else if (newestRemote) {
      if (!navigator.onLine) {
        throw new Error("Desktop queue is offline.");
      }
      const response = await fetch("/api/inbox/unpublished/undo", {
        method: "POST",
        cache: "no-store",
        headers: authHeaders(),
      });
      const data = await responseData(response);
      setStatus(data.message || "Removed latest unpublished item.");
      await refreshRemoteItems();
    } else {
      setStatus("Nothing to undo.");
    }
  } catch (error) {
    setStatus(error.message);
  } finally {
    undoInFlight = false;
    setBusyState();
  }
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) {
    return;
  }
  try {
    await navigator.serviceWorker.register("/capture-sw.js");
  } catch {
    // Ignore registration failures and keep the page usable online.
  }
}

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void submitThought();
  }
});

enterButton.addEventListener("click", () => void submitThought());
undoButton.addEventListener("click", () => void undoThought());
unlockButton.addEventListener("click", () => {
  const token = syncTokenInput.value.trim();
  if (!token) {
    showUnlock("A sync token is required.");
    return;
  }
  localStorage.setItem(TOKEN_KEY, token);
  syncTokenInput.value = "";
  hideUnlock();
  setStatus("Unlocking...");
  void refreshEverything();
});
syncTokenInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    unlockButton.click();
  }
});
window.addEventListener("online", () => void refreshEverything());
window.addEventListener("focus", () => void refreshEverything());
window.addEventListener("pageshow", () => void refreshEverything());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    void refreshEverything();
  }
});

void registerServiceWorker();
void refreshEverything()
  .then(() => input.focus())
  .catch(() => {
    renderItems();
    setStatus("Saved on iPhone. Waiting for Marko computer.");
  });
