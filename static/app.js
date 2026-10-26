const form = document.querySelector("#deck-form");
const fileInput = document.querySelector("#source-files");
const fileList = document.querySelector("#file-list");
const submitButton = document.querySelector("#submit-button");
const formError = document.querySelector("#form-error");
const activePanel = document.querySelector("#active-panel");
const activeTitle = document.querySelector("#active-title");
const activeProgress = document.querySelector("#active-progress");
const activeError = document.querySelector("#active-error");
const statusPill = document.querySelector("#status-pill");
const progressTrack = document.querySelector(".progress-track");
const progressBar = document.querySelector("#progress-bar");
const cancelButton = document.querySelector("#cancel-button");
const deckList = document.querySelector("#deck-list");
const emptyLibrary = document.querySelector("#empty-library");
const deckCount = document.querySelector("#deck-count");

let activeJobId = null;
let pollTimer = null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : {};
  if (!response.ok) throw new Error(data.detail || "Something went wrong. Please try again.");
  return data;
}

function showError(message) {
  formError.textContent = message;
  formError.hidden = !message;
}

function updateFileList() {
  fileList.replaceChildren();
  [...fileInput.files].forEach((file) => {
    const row = document.createElement("div");
    row.className = "file-row";
    const name = document.createElement("span");
    name.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
    row.append(name);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.addEventListener("click", () => {
      const transfer = new DataTransfer();
      [...fileInput.files].filter((candidate) => candidate !== file).forEach((candidate) => transfer.items.add(candidate));
      fileInput.files = transfer.files;
      updateFileList();
    });
    row.append(remove);
    fileList.append(row);
  });
}

function statusLabel(status) {
  return ({
    queued: "Queued",
    running: "Working",
    cancel_requested: "Cancelling",
    completed: "Complete",
    failed: "Failed",
    cancelled: "Cancelled",
  })[status] || status;
}

function renderActive(job) {
  activeJobId = job.id;
  activePanel.hidden = false;
  activeTitle.textContent = job.title || "Preparing your deck";
  activeProgress.textContent = job.progress || "Working on your presentation";
  statusPill.textContent = statusLabel(job.status);
  statusPill.dataset.status = job.status;
  activeError.textContent = job.error || "";
  const finished = ["completed", "failed", "cancelled"].includes(job.status);
  submitButton.disabled = !finished;
  cancelButton.hidden = finished;
  cancelButton.disabled = job.status === "cancel_requested";
  cancelButton.textContent = job.status === "cancel_requested" ? "Cancelling…" : "Cancel request";
  progressTrack.classList.toggle("is-moving", !finished);
  progressBar.style.width = job.status === "completed" ? "100%" : job.status === "failed" || job.status === "cancelled" ? "0%" : "26%";
  if (finished) activeJobId = null;
}

function renderDeck(deck) {
  const row = document.createElement("article");
  row.className = "deck-row";
  const icon = document.createElement("span");
  icon.className = "deck-icon";
  icon.textContent = "PPT";
  icon.setAttribute("aria-hidden", "true");
  const copy = document.createElement("div");
  copy.className = "deck-copy";
  const title = document.createElement("strong");
  title.textContent = deck.title || deck.filename || "Presentation";
  const time = document.createElement("time");
  time.dateTime = deck.updated_at;
  time.textContent = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(deck.updated_at));
  copy.append(title, time);
  const actions = document.createElement("div");
  actions.className = "deck-actions";
  const download = document.createElement("a");
  download.href = `/api/decks/${encodeURIComponent(deck.id)}/download`;
  download.setAttribute("aria-label", `Download ${title.textContent}`);
  download.title = "Download PowerPoint";
  download.textContent = "↓";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.setAttribute("aria-label", `Delete ${title.textContent}`);
  remove.title = "Delete deck";
  remove.textContent = "×";
  remove.addEventListener("click", async () => {
    if (!window.confirm(`Delete “${title.textContent}”?`)) return;
    try {
      await api(`/api/decks/${encodeURIComponent(deck.id)}`, { method: "DELETE" });
      await loadDecks();
    } catch (error) {
      showError(error.message);
    }
  });
  actions.append(download, remove);
  row.append(icon, copy, actions);
  return row;
}

async function loadDecks() {
  const { jobs } = await api("/api/jobs");
  deckList.replaceChildren(...jobs.map(renderDeck));
  deckCount.textContent = String(jobs.length);
  emptyLibrary.hidden = jobs.length > 0;
}

async function pollActive() {
  try {
    const { job } = await api("/api/jobs/active");
    if (job) {
      renderActive(job);
    } else if (activeJobId) {
      const finishedJob = await api(`/api/jobs/${encodeURIComponent(activeJobId)}`);
      renderActive(finishedJob);
      if (finishedJob.status === "completed") await loadDecks();
    }
    if (!job && !activeJobId) stopPolling();
  } catch (error) {
    showError(error.message);
    stopPolling();
  }
}

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = window.setInterval(pollActive, 1800);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

fileInput.addEventListener("change", updateFileList);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const buttonText = submitButton.querySelector("span:first-child");
  submitButton.disabled = true;
  buttonText.textContent = "Sending sources…";
  try {
    const job = await api("/api/jobs", { method: "POST", body: new FormData(form) });
    renderActive(job);
    activePanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    startPolling();
  } catch (error) {
    showError(error.message);
    submitButton.disabled = false;
  } finally {
    buttonText.textContent = "Draft presentation";
  }
});

cancelButton.addEventListener("click", async () => {
  if (!activeJobId) return;
  cancelButton.disabled = true;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(activeJobId)}/cancel`, { method: "POST" });
    renderActive(job);
    startPolling();
  } catch (error) {
    showError(error.message);
    cancelButton.disabled = false;
  }
});

async function initialize() {
  try {
    await loadDecks();
    const { job } = await api("/api/jobs/active");
    if (job) {
      renderActive(job);
      startPolling();
    }
  } catch (error) {
    showError(error.message);
  }
}

initialize();