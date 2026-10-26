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
const deckList = document.querySelector("#deck-list");
const emptyLibrary = document.querySelector("#empty-library");
const deckCount = document.querySelector("#deck-count");

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

function renderGenerationState({ title, status, message, error = "", complete = false }) {
  activePanel.hidden = false;
  activeTitle.textContent = title;
  activeProgress.textContent = message;
  statusPill.textContent = status;
  statusPill.dataset.status = error ? "failed" : complete ? "completed" : "running";
  activeError.textContent = error;
  progressTrack.classList.toggle("is-moving", !complete && !error);
  progressBar.style.width = complete ? "100%" : error ? "0%" : "26%";
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
  download.href = `/api/decks/${encodeURIComponent(deck.filename)}/download`;
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
      await api(`/api/decks/${encodeURIComponent(deck.filename)}`, { method: "DELETE" });
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
  const { decks } = await api("/api/decks");
  deckList.replaceChildren(...decks.map(renderDeck));
  deckCount.textContent = String(decks.length);
  emptyLibrary.hidden = decks.length > 0;
}

fileInput.addEventListener("change", updateFileList);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const buttonText = submitButton.querySelector("span:first-child");
  submitButton.disabled = true;
  buttonText.textContent = "Sending sources…";
  renderGenerationState({
    title: "Creating your presentation",
    status: "Working",
    message: "Reading sources and generating slides. Keep this page open until the download starts.",
  });
  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      body: new FormData(form),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "PowerPoint generation failed. Check the server log.");
    }
    const file = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || "presentation.pptx";
    const downloadUrl = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
    renderGenerationState({
      title: filename.replace(/-[^-]+\.pptx$/, "").replaceAll("-", " "),
      status: "Complete",
      message: "Your PowerPoint was downloaded and saved in the presentations folder.",
      complete: true,
    });
    await loadDecks();
  } catch (error) {
    showError(error.message);
    renderGenerationState({
      title: "Generation failed",
      status: "Failed",
      message: "The presentation could not be generated.",
      error: error.message,
    });
  } finally {
    submitButton.disabled = false;
    buttonText.textContent = "Draft presentation";
  }
});

async function initialize() {
  try {
    await loadDecks();
  } catch (error) {
    showError(error.message);
  }
}

initialize();