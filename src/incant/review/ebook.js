"use strict";

const $ = (selector) => document.querySelector(selector);
const form = $("#review");
const select = $("#outline");
const api = location.pathname.replace(/\/$/, "") + "/api/draft";
let draft;
let revision;
let selected = 0;
let dirty = false;

function status(message, error = false) {
  $("#status").textContent = message;
  $("#status").classList.toggle("error", error);
}

async function request(options) {
  const result = await fetch(api, options);
  const data = await result.json();
  if (!result.ok) throw new Error(data.error || result.statusText);
  return data;
}

function capture() {
  draft.chapters[selected] = {
    title: $("#chapter-title").value.trim(),
    paragraphs: $("#prose").value.split(/\n[ \t]*\n/).map((text) => text.trim()).filter(Boolean),
  };
}

function show(index) {
  selected = index;
  select.replaceChildren(...draft.chapters.map((chapter, i) => new Option(chapter.title || "Untitled chapter", i)));
  select.value = index;
  $("#chapter-title").value = draft.chapters[index].title;
  $("#prose").value = draft.chapters[index].paragraphs.join("\n\n");
  $("#earlier").disabled = index === 0;
  $("#later").disabled = index === draft.chapters.length - 1;
  $("#remove").disabled = draft.chapters.length === 1;
}

function move(offset) {
  capture();
  const [chapter] = draft.chapters.splice(selected, 1);
  draft.chapters.splice(selected + offset, 0, chapter);
  dirty = true;
  show(selected + offset);
}

select.addEventListener("change", () => {
  const index = Number(select.value);
  capture();
  show(index);
});

form.addEventListener("input", () => { dirty = true; });
$("#earlier").addEventListener("click", () => move(-1));
$("#later").addEventListener("click", () => move(1));
$("#add").addEventListener("click", () => {
  capture();
  draft.chapters.splice(selected + 1, 0, { title: "New chapter", paragraphs: [] });
  dirty = true;
  show(selected + 1);
  $("#chapter-title").focus();
});
$("#remove").addEventListener("click", () => {
  if (!confirm("Remove this chapter from the ebook draft?")) return;
  draft.chapters.splice(selected, 1);
  dirty = true;
  show(Math.min(selected, draft.chapters.length - 1));
});

window.addEventListener("beforeunload", (event) => {
  if (dirty) event.preventDefault();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  capture();
  const values = new FormData(form);
  for (const key of ["title", "author", "language", "description"]) draft[key] = values.get(key).trim();
  const publish = event.submitter?.value === "publish";
  const buttons = [...form.querySelectorAll('button[name="action"]')];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const data = await request({
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft, revision, publish }),
    });
    revision = data.revision;
    dirty = JSON.stringify(draft) !== JSON.stringify(data.draft);
    $("#title").textContent = data.draft.title;
    if (data.download) {
      $("#download").href = data.download;
      $("#download").hidden = false;
    }
    status(publish ? "EPUB exported." : "Draft saved.");
  } catch (error) {
    status(error.message, true);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
});

request().then((data) => {
  ({ draft, revision } = data);
  for (const key of ["title", "author", "language", "description"]) form.elements[key].value = draft[key] || "";
  $("#title").textContent = draft.title;
  show(0);
  form.hidden = false;
  status("");
}).catch((error) => status(error.message, true));
