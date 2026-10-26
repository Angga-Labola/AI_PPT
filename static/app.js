const form = document.querySelector("#deck-form");
const fileInput = document.querySelector("#source-files");
const fileList = document.querySelector("#file-list");
let selectedFiles = [];
const submitButton = document.querySelector("#submit-button");
const formError = document.querySelector("#form-error");
const activePanel = document.querySelector("#active-panel");
const activeTitle = document.querySelector("#active-title");
const activeProgress = document.querySelector("#active-progress");
const activeError = document.querySelector("#active-error");
const relevantLinksPanel = document.querySelector("#relevant-links");
const searchResultList = document.querySelector("#search-result-list");
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

function renderSearchResults(results, researchDepth = "off", statusMessage = "") {
  searchResultList.replaceChildren();
  for (const source of results) {
    const item = document.createElement("li");
    item.className = "search-result";
    const heading = document.createElement("div");
    heading.className = "search-result-heading";
    const link = document.createElement("a");
    const parsedUrl = URL.canParse(source.url) ? new URL(source.url) : null;
    if (parsedUrl && ["http:", "https:"].includes(parsedUrl.protocol)) {
      link.href = parsedUrl.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    link.textContent = `${source.source_id ? `${source.source_id}: ` : ""}${source.title || source.url}`;
    const status = document.createElement("span");
    status.className = `search-result-status is-${source.status || "not_assessed"}`;
    status.textContent = {
      relevant: "Relevant",
      not_relevant: "Not relevant",
      not_assessed: "Not assessed",
    }[source.status] || "Not assessed";
    heading.append(link, status);
    item.append(heading);
    if (source.query) {
      const query = document.createElement("p");
      query.className = "search-result-query";
      query.textContent = `Found for: ${source.query}`;
      item.append(query);
    }
    if (source.status_detail) {
      const detail = document.createElement("p");
      detail.className = "search-result-detail";
      detail.textContent = source.status_detail;
      item.append(detail);
    }
    searchResultList.append(item);
  }
  if (statusMessage) {
    const status = document.createElement("li");
    status.className = "relevant-links-status";
    status.textContent = statusMessage;
    searchResultList.append(status);
  }
  relevantLinksPanel.hidden = researchDepth === "off";
}

function syncFileInput() {
  const transfer = new DataTransfer();
  selectedFiles.forEach((file) => transfer.items.add(file));
  fileInput.files = transfer.files;
}

function updateFileList() {
  fileList.replaceChildren();
  selectedFiles.forEach((file, index) => {
    const row = document.createElement("div");
    row.className = "file-row";
    row.setAttribute("role", "listitem");
    const details = document.createElement("span");
    details.className = "file-details";
    const label = document.createElement("strong");
    label.className = "file-label";
    label.textContent = `File ${index + 1}`;
    const name = document.createElement("span");
    name.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
    details.append(label, name);
    row.append(details);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove File ${index + 1}: ${file.name}`);
    remove.addEventListener("click", () => {
      selectedFiles = selectedFiles.filter((_, fileIndex) => fileIndex !== index);
      syncFileInput();
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

fileInput.addEventListener("change", () => {
  const newlySelected = [...fileInput.files];
  const existingFiles = new Set(
    selectedFiles.map((file) => `${file.name}:${file.size}:${file.lastModified}`),
  );
  for (const file of newlySelected) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!existingFiles.has(key)) {
      selectedFiles.push(file);
      existingFiles.add(key);
    }
  }
  syncFileInput();
  updateFileList();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  relevantLinksPanel.hidden = true;
  searchResultList.replaceChildren();
  const researchDepth = new FormData(form).get("research_depth") || "off";
  const buttonText = submitButton.querySelector("span:first-child");
  submitButton.disabled = true;
  buttonText.textContent = "Sending sources…";
  const formData = new FormData(form);
  formData.set("include_metadata_form", "true");
  renderGenerationState({
    title: "Creating your presentation",
    status: "Working",
    message: "Reading sources and generating slides. Keep this page open until the download starts.",
  });
  try {
    const response = await fetch("/api/generate?include_metadata=true", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "PowerPoint generation failed. Check the server log.");
    }
    const contentType = response.headers.get("content-type") || "";
    let generation;
    if (contentType.includes("application/json")) {
      generation = await response.json();
    } else if (contentType.includes("application/vnd.openxmlformats-officedocument.presentationml.presentation")) {
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
      generation = {
        filename,
        relevant_links: [],
        research_depth: researchDepth,
        status_message: "The active server returned a PowerPoint instead of research metadata. Stop the server handling this page and start app.py from the slides_generator project folder.",
      };
    } else {
      const responseText = await response.text();
      throw new Error(responseText || "The server returned an unexpected response.");
    }
    if (generation.download_url) {
      const link = document.createElement("a");
      link.href = generation.download_url;
      link.download = generation.filename;
      document.body.append(link);
      link.click();
      link.remove();
    }
    const results = generation.search_results
      || (generation.relevant_links || []).map((link) => ({
        ...link,
        status: "relevant",
        status_detail: "Cited in the presentation.",
      }));
    const links = generation.relevant_links || [];
    const stats = generation.research_stats || {};
    const statusMessage = generation.status_message
      || (researchDepth !== "off" && results.length === 0
        ? stats.searches_performed === 0
          ? "No web search was performed. Please restart the app so the latest research settings are loaded."
          : `Web search ran (${stats.searches_performed} search${stats.searches_performed === 1 ? "" : "es"}), but no pages were successfully retrieved.`
        : "");
    renderSearchResults(results, researchDepth, statusMessage);
    renderGenerationState({
      title: generation.title || generation.filename.replace(/-[^-]+\.pptx$/, "").replaceAll("-", " "),
      status: "Complete",
      message: generation.relevant_links
        ? "Your PowerPoint was downloaded and saved in the presentations folder."
        : "Your PowerPoint was downloaded, but the active server did not return research metadata. Restart the current project server to display relevant links.",
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