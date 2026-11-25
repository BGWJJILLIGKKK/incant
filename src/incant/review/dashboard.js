"use strict";

const $ = (selector) => document.querySelector(selector);
const form = $("#pipeline");
const active = new Set(["queued", "running"]);
const files = new Map();
let currentView = "jobs";
let selectedJob = null;
let selectedFile = null;
let jobListVersion = "";
let polling = false;
let allBooks = [];
let settings;
const params = new URLSearchParams(location.search);
const publishing = form.elements.publishing;
const libraryFilter = $("#library-search");
if (params.get("publishing") === "ebook" || (!params.has("publishing") && sessionStorage.getItem("incant-publishing") === "ebook")) {
  publishing.value = "ebook";
}

const STAGE_META = {
  preingest: { title: "Pre-ingest", desc: "Batch processes link lists or bookmark files" },
  ingest: { title: "Ingest", desc: "Extracts text from eBooks (EPUB, PDF, MOBI) or URLs" },
  draft: { title: "Draft", desc: "Prepare chapters for ebook review" },
  publish: { title: "Publish", desc: "Export the manuscript as EPUB" },
  parse: { title: "Parse", desc: "Splits book into chapters & builds character roster" },
  attribute: { title: "Attribute", desc: "Assigns who speaks each quote using AI or rules" },
  normalize: { title: "Normalize", desc: "Expands numbers, dates & abbreviations for speech" },
  synthesize: { title: "Synthesize", desc: "Voice engine that speaks narration & characters" },
  output: { title: "Output", desc: "Packages audio into final M4B audiobook or MP3 format" },
};

const METHOD_LABELS = {
  // attribute
  speakers: "speakers (AI deep reading · 95% accuracy)",
  tags: "tags (Fast rule-based tags · 68–80% accuracy)",
  narrator: "narrator (Single solo narrator voice)",
  // parse
  basic: "basic (Fast chapter detection)",
  characters: "characters (AI character profile extraction)",
  // synthesize
  kokoro: "kokoro (Fast local CPU · no GPU or key needed)",
  indextts: "indextts (Expressive GPU voice server)",
  elevenlabs: "elevenlabs (Cloud voice · requires API key)",
  dummy: "dummy (Test tone beeps)",
  // output
  m4b: "m4b (Standard audiobook with chapters)",
  mp3: "mp3 (Single consolidated MP3 file)",
  chapters: "chapters (Separate file per chapter)",
};

async function request(path, options) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || response.statusText);
  return result;
}

function post(path, data) {
  return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

function feedback(message, isError = false) {
  const element = $("#feedback");
  if (!message) {
    element.hidden = true;
    return;
  }
  element.textContent = (isError ? "⚠ " : "✓ ") + message;
  element.classList.toggle("error", isError);
  element.hidden = false;
}

function textElement(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

function option(value, text) {
  return new Option(text, value);
}

async function loadSettings() {
  settings = await request("/api/settings");
  renderSettings();
}

function renderSettings() {
  $("#allowed-paths").replaceChildren(...settings.path_roots.map((path) => {
    const small = textElement("small", path, "path");
    small.style.display = "block";
    return small;
  }));
  const ebook = publishing.value === "ebook";
  $("#library-publishing").value = publishing.value;
  $("#output-path").textContent = settings.output_dir ? `Default output: ${settings.output_dir}${ebook ? "/ebooks" : ""}` : "";
  $("#run-title").textContent = ebook ? "New Ebook Run" : "New Audiobook Run";
  $("#library-title").textContent = ebook ? "Ebook Library" : "Audiobook Library";
  $("#run-description").textContent = ebook ? "Import a source, prepare a draft, or publish a reflowable EPUB." : "Choose a source and how you want it voiced.";
  for (const field of document.querySelectorAll("[data-audio-only]")) field.hidden = ebook;
  if (ebook && form.elements.force.value.split(",").some((name) => !["", "all", ...settings.publishing_stages.ebook].includes(name.trim()))) {
    form.elements.force.value = "";
  }
  form.elements.llm.disabled = form.elements.chapters.disabled = ebook;
  const former = Object.fromEntries(new FormData(form));
  const names = settings.publishing_stages[publishing.value];

  $("#methods").replaceChildren(...names.map((name) => {
    const card = document.createElement("div");
    card.className = "stage-card";

    const header = document.createElement("div");
    header.className = "stage-header";

    const meta = STAGE_META[name] || { title: name, desc: "" };
    header.append(textElement("span", meta.title, "stage-title"), textElement("span", name, "stage-badge"));

    if (meta.desc) {
      card.append(header, textElement("p", meta.desc, "stage-desc"));
    } else {
      card.append(header);
    }

    const select = document.createElement("select");
    select.name = `method.${name}`;
    const defaultMethod = settings.pipeline[name];
    select.add(option("", defaultMethod ? `Default (${defaultMethod})` : "Automatic / default"));

    for (const value of settings.stages[name]) {
      const label = METHOD_LABELS[value] || value;
      select.add(option(value, label));
    }
    select.value = former[select.name] || "";

    const label = document.createElement("label");
    label.style.fontWeight = "normal";
    label.append(select);
    card.append(label);

    return card;
  }));

  for (const name of ["start", "stop"]) {
    const select = form.elements[name];
    select.replaceChildren(select.options[0], ...names.filter((n) => n !== "preingest").map((n) => {
      const meta = STAGE_META[n] || { title: n };
      return option(n, `${meta.title} (${n})`);
    }));
    select.value = former[name] || "";
  }
  form.elements.llm.replaceChildren(option("", "Default configured profile"), ...settings.profiles.map((n) => option(n, n)));
  form.elements.llm.value = former.llm || "";
}

publishing.addEventListener("change", () => {
  sessionStorage.setItem("incant-publishing", publishing.value);
  if (settings) renderSettings();
  if (currentView === "library") renderBookList(libraryFilter.value);
});

$("#library-publishing").addEventListener("change", (event) => {
  publishing.value = event.target.value;
  publishing.dispatchEvent(new Event("change"));
});

// Preset Handler
function applyPreset(preset) {
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.preset === preset);
  });

  const setMethod = (stage, val) => {
    const sel = form.elements[`method.${stage}`];
    if (sel) {
      const hasVal = Array.from(sel.options).some(o => o.value === val);
      sel.value = hasVal ? val : "";
    }
  };

  if (preset === "recommended") {
    setMethod("parse", "characters");
    setMethod("attribute", "speakers");
    setMethod("synthesize", ""); // uses best default (indextts / kokoro)
    feedback("Preset selected: Full-Cast AI (character extraction + deep speaker attribution).");
  } else if (preset === "cpu") {
    setMethod("parse", "basic");
    setMethod("attribute", "tags");
    setMethod("synthesize", "kokoro");
    feedback("Preset selected: Fast & Free (100% offline CPU with Kokoro voices).");
  } else if (preset === "narrator") {
    setMethod("parse", "basic");
    setMethod("attribute", "narrator");
    setMethod("synthesize", "");
    feedback("Preset selected: Classic Solo Narrator (single voice for the entire book).");
  }
}

document.querySelectorAll(".preset-btn").forEach((btn) => {
  btn.addEventListener("click", () => applyPreset(btn.dataset.preset));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const data = Object.fromEntries(new FormData(form));
  const methods = {};
  for (const [key, value] of Object.entries(data)) {
    if (key.startsWith("method.")) {
      if (value) methods[key.slice(7)] = value;
      delete data[key];
    }
  }
  if (button) button.disabled = true;
  const mode = button?.value || "run";
  try {
    const job = await post("/api/jobs", { ...data, mode, methods });
    selectedJob = job.id;
    const modeName = mode === "run" ? (data.publishing === "ebook" ? "Ebook run" : "Audiobook run") : mode === "plan" ? "Plan preview" : "Readiness check";
    feedback(`${modeName} queued successfully.`);
    jobListVersion = "";
    await pollJobs();
  } catch (error) {
    feedback(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
});

// Upload and Drag-and-drop
async function handleFile(file) {
  if (!file) return;
  const button = $("#upload");
  button.disabled = true;
  feedback(`Uploading ${file.name}…`);
  try {
    const result = await request(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    $("#source").value = result.source;
    feedback(`${file.name} is uploaded and ready to run.`);
  } catch (error) {
    feedback(error.message, true);
  } finally {
    button.disabled = false;
  }
}

$("#upload").addEventListener("click", () => $("#upload-file").click());
$("#upload-file").addEventListener("change", (event) => {
  handleFile(event.target.files[0]);
  event.target.value = "";
});

const dropZone = $("#drop-indicator");
if (dropZone) {
  dropZone.addEventListener("click", () => $("#upload-file").click());
  ["dragenter", "dragover"].forEach((name) => {
    dropZone.addEventListener(name, (e) => {
      e.preventDefault();
      dropZone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropZone.addEventListener(name, (e) => {
      e.preventDefault();
      dropZone.classList.remove("dragover");
    });
  });
  dropZone.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) handleFile(file);
  });
}

// Copy Log
const copyLogBtn = $("#copy-log");
if (copyLogBtn) {
  copyLogBtn.addEventListener("click", () => {
    const logText = $("#log").textContent;
    navigator.clipboard.writeText(logText).then(() => {
      feedback("Log copied to clipboard!");
    }).catch(() => {
      feedback("Failed to copy log.", true);
    });
  });
}

function statePill(state) {
  const icons = {
    running: "⚙️ ",
    queued: "⏳ ",
    succeeded: "✓ ",
    failed: "✕ ",
    interrupted: "⏹ ",
  };
  const icon = icons[state] || "";
  return textElement("span", `${icon}${state}`, `state ${state}`);
}

async function pollJobs() {
  if (polling || currentView !== "jobs") return;
  polling = true;
  try {
    const jobs = await request("/api/jobs");
    if (!jobs.some((job) => job.id === selectedJob)) selectedJob = jobs[0]?.id ?? null;
    $("#clear-finished").hidden = !jobs.some((job) => !active.has(job.state));
    const key = JSON.stringify([selectedJob, jobs]);
    if (key !== jobListVersion) {
      $("#job-list").replaceChildren(...jobs.map((job) => {
        const row = document.createElement("li");
        const button = textElement("button", "", "job");
        button.type = "button";
        button.setAttribute("aria-pressed", String(selectedJob === job.id));

        const sourceLabel = textElement("strong", job.source || "Untitled");
        const stateEl = statePill(job.state);
        const timeStr = new Date(job.created).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        const sub = textElement("small", `${job.publishing === "ebook" ? "Ebook" : "Audiobook"} · ${job.mode.toUpperCase()} · ${timeStr}`);

        button.append(sourceLabel, stateEl, sub);
        button.addEventListener("click", () => {
          selectedJob = job.id;
          $("#log").textContent = "";
          pollJobs().catch((error) => feedback(error.message, true));
        });
        row.append(button);
        return row;
      }));
      $("#empty-jobs").hidden = jobs.length > 0;
      jobListVersion = key;
    }
    $("#job-detail").hidden = !selectedJob;
    if (selectedJob) {
      const id = selectedJob;
      const response = await fetch(`/api/jobs/${id}`);
      if (response.status === 404) {
        if (id === selectedJob) {
          selectedJob = null;
          $("#job-detail").hidden = true;
        }
        return;
      }
      const job = await response.json();
      if (!response.ok) throw new Error(job.error || response.statusText);
      if (id !== selectedJob) return;
      const log = $("#log");
      const tail = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
      const content = job.log || (active.has(job.state) ? "Waiting for output from Incant pipeline…" : "No log output recorded.");
      if (log.textContent !== content) {
        log.textContent = content;
        if (tail) log.scrollTop = log.scrollHeight;
      }
      $("#cancel").hidden = !active.has(job.state);
    }
  } finally {
    polling = false;
  }
}

$("#clear-finished").addEventListener("click", async (event) => {
  if (!window.confirm("Delete finished job history and logs? Queued and running jobs will be kept.")) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const { removed } = await post("/api/jobs/clear-finished", {});
    feedback(`Cleared ${removed} finished ${removed === 1 ? "job" : "jobs"}.`);
    await pollJobs();
  } catch (error) {
    feedback(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#cancel").addEventListener("click", async () => {
  if (!selectedJob) return;
  try {
    await post(`/api/jobs/${selectedJob}/cancel`, {});
    feedback("Cancellation requested.");
    await pollJobs();
  } catch (error) {
    feedback(error.message, true);
  }
});

function renderBookList(filter = "") {
  const norm = filter.trim().toLowerCase();
  const ebook = publishing.value === "ebook";
  const filtered = allBooks.filter(b => !norm || (b.title && b.title.toLowerCase().includes(norm)) || (b.author && b.author.toLowerCase().includes(norm)));
  $("#empty-books").hidden = allBooks.length > 0;

  $("#book-list").replaceChildren(...filtered.map((book) => {
    const card = textElement("article", "", "book-card");

    const header = document.createElement("div");
    header.className = "book-card-header";

    const titleGroup = document.createElement("div");
    titleGroup.append(textElement("h2", book.title || "Untitled Book", "book-name"));
    titleGroup.append(textElement("p", book.author || "Unknown Author", "muted"));

    const icon = textElement("div", ebook ? "📖" : "🎧", "book-icon");
    header.append(titleGroup, icon);
    card.append(header);

    // Progress
    const total = book.chapters || 0;
    const rendered = book.rendered || 0;
    const percent = total > 0 ? Math.round((rendered / total) * 100) : 0;

    const progWrap = document.createElement("div");
    progWrap.append(textElement("small", `${rendered} of ${total} chapters voiced (${percent}%)`, "muted"));

    const barWrap = document.createElement("div");
    barWrap.className = "progress-bar-wrap";
    const barFill = document.createElement("div");
    barFill.className = "progress-bar-fill";
    barFill.style.width = `${percent}%`;
    barWrap.append(barFill);
    progWrap.append(barWrap);
    if (!ebook) card.append(progWrap);
    else {
      const state = !book.ebook_allowed ? "Source is already EPUB" : book.ebook_exports?.length ? "EPUB available" :
        book.ebook_draft ? "Draft ready" : "Ready to prepare";
      card.append(textElement("p", state, "muted"));
    }

    // Actions
    const actions = textElement("div", "", "row");
    actions.style.marginTop = "0.5rem";

    if (ebook && book.ebook_allowed) {
      const review = textElement("a", book.ebook_draft ? "Review ebook" : "Prepare ebook draft", "action primary");
      review.href = `/ebook/${encodeURIComponent(book.slug)}/`;
      actions.append(review);
    } else if (!ebook && book.chapters) {
      const review = textElement("a", "🎙️ Studio Review", "action primary");
      review.href = `/review/${encodeURIComponent(book.slug)}/`;
      review.title = "Audit dialogue lines, customize character voices, and fix pronunciations";
      actions.append(review);
    }

    const run = textElement("button", "🔄 Re-run");
    run.type = "button";
    run.title = "Pre-fill New Run form with this book";
    run.addEventListener("click", () => {
      $("#source").value = book.slug;
      show("jobs").catch((error) => feedback(error.message, true));
      $("#source").focus();
    });
    if (!ebook || book.ebook_allowed) actions.append(run);

    for (const file of (ebook ? book.ebook_exports : book.exports) || []) {
      const link = textElement("a", `📦 ${file.name}`, "export-badge");
      link.href = file.url;
      link.title = `Download finished file: ${file.name}`;
      actions.append(link);
    }

    card.append(actions);
    return card;
  }));
}

const libSearch = $("#library-search");
if (libSearch) {
  libSearch.addEventListener("input", (e) => renderBookList(e.target.value));
}

async function loadLibrary() {
  allBooks = await request("/api/books");
  renderBookList(libSearch ? libSearch.value : "");
}

function chooseFile(id) {
  selectedFile = id;
  const file = files.get(id);
  $("#config-text").value = file.draft ?? file.text;
  $("#config-path").textContent = file.path;
  const readOnly = file.writable === false;
  $("#config-text").readOnly = readOnly;
  $("#config-form button[type='submit']").disabled = readOnly;
  $("#config-state").textContent = readOnly ? "Read-only" : file.draft === undefined ? "" : "Unsaved changes";
}

async function loadFiles() {
  const updated = await request("/api/config");
  for (const file of updated) {
    const draft = files.get(file.id)?.draft;
    files.set(file.id, { ...file, draft });
  }
  $("#config-file").replaceChildren(...updated.map((file) => option(file.id, file.path.split(/[\\/]/).pop() +
    (file.id === "installation-local" ? " (installation)" : ""))));
  $("#config-file").value = updated.some((f) => f.id === selectedFile) ? selectedFile : updated[0].id;
  chooseFile($("#config-file").value);
}

$("#config-file").addEventListener("change", (event) => chooseFile(event.target.value));
$("#config-text").addEventListener("input", (event) => {
  const file = files.get(selectedFile);
  file.draft = event.target.value === file.text ? undefined : event.target.value;
  $("#config-state").textContent = file.draft === undefined ? "" : "Unsaved changes";
});

$("#config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  if (!selectedFile || files.get(selectedFile).writable === false) return;
  const id = selectedFile;
  const text = $("#config-text").value;
  if (button) button.disabled = true;
  try {
    await post("/api/config", { id, text });
    const file = files.get(id);
    file.text = text;
    if (file.draft === text) file.draft = undefined;
    if (selectedFile === id) $("#config-state").textContent = file.draft === undefined ? "Saved ✓" : "Unsaved changes";
    feedback("Configuration saved successfully.");
    await loadSettings();
  } catch (error) {
    feedback(error.message, true);
  } finally {
    if (button) button.disabled = files.get(selectedFile)?.writable === false;
  }
});

async function show(view) {
  currentView = view;
  feedback("");
  for (const button of document.querySelectorAll("[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
    document.getElementById(button.dataset.view).hidden = button.dataset.view !== view;
  }
  if (view === "configuration") await loadFiles();
  if (view === "library") await loadLibrary();
  if (view === "jobs") await pollJobs();
}

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => show(button.dataset.view).catch((error) => feedback(error.message, true)));
}

Promise.all([loadSettings(), params.get("view") === "library" ? show("library") : pollJobs()]).catch((error) => feedback(error.message, true));
setInterval(() => pollJobs().catch((error) => feedback(error.message, true)), 2000);
