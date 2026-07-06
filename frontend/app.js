const PREPARE_STAGES = [
  { key: "voiceover", label: "Voice" },
  { key: "splitting", label: "Split" },
  { key: "timing", label: "Timing" },
  { key: "sourcing", label: "Footage" },
  { key: "ready_for_review", label: "Review" },
];

const EXPORT_STAGES = [
  { key: "exporting", label: "Fetch" },
  { key: "assembling", label: "Render" },
  { key: "done", label: "Export" },
];

const els = {
  inputPanel: document.getElementById("inputPanel"),
  preparePanel: document.getElementById("preparePanel"),
  reviewPanel: document.getElementById("reviewPanel"),
  exportPanel: document.getElementById("exportPanel"),
  jobForm: document.getElementById("jobForm"),
  prepareFilmstrip: document.getElementById("prepareFilmstrip"),
  prepareMessage: document.getElementById("prepareMessage"),
  prepareFill: document.getElementById("prepareFill"),
  prepareError: document.getElementById("prepareError"),
  sceneList: document.getElementById("sceneList"),
  exportBtn: document.getElementById("exportBtn"),
  exportFilmstrip: document.getElementById("exportFilmstrip"),
  exportMessage: document.getElementById("exportMessage"),
  exportFill: document.getElementById("exportFill"),
  exportError: document.getElementById("exportError"),
  result: document.getElementById("result"),
  previewVideo: document.getElementById("previewVideo"),
  downloadLink: document.getElementById("downloadLink"),
};

let currentJobId = null;
let currentJob = null;

function showPanel(panel) {
  [els.inputPanel, els.preparePanel, els.reviewPanel, els.exportPanel].forEach((p) =>
    p.classList.add("hidden")
  );
  panel.classList.remove("hidden");
}

function renderFilmstrip(container, stages, activeStatus) {
  container.innerHTML = "";
  const activeIdx = stages.findIndex((s) => s.key === activeStatus);
  stages.forEach((stage, i) => {
    const div = document.createElement("div");
    div.className = "frame";
    if (activeStatus === "done" || i < activeIdx) div.className += " complete";
    else if (i === activeIdx) div.className += " active";
    div.textContent = stage.label;
    container.appendChild(div);
  });
}

document.querySelectorAll('input[type="file"]').forEach((input) => {
  input.addEventListener("change", () => {
    const nameSpan = document.querySelector(`.file-name[data-for="${input.id}"]`);
    if (nameSpan) nameSpan.textContent = input.files[0]?.name || "No file chosen";
  });
});

const voiceoverMode = document.getElementById("voiceoverMode");
const uploadVoiceoverRow = document.getElementById("uploadVoiceoverRow");
const aiVoiceoverRow = document.getElementById("aiVoiceoverRow");
const voiceoverFileInput = document.getElementById("voiceover");
const voiceProviderSelect = document.getElementById("voiceProvider");
const voicePickerSelect = document.getElementById("voicePicker");
const elevenlabsModelRow = document.getElementById("elevenlabsModelRow");
const elevenlabsModelSelect = document.getElementById("elevenlabsModel");
const voicePreviewBtn = document.getElementById("voicePreviewBtn");
const voicePreviewAudio = document.getElementById("voicePreviewAudio");
let currentVoices = [];
let elevenlabsModelsLoaded = false;

voiceoverMode.addEventListener("change", () => {
  const isAi = voiceoverMode.value === "ai";
  uploadVoiceoverRow.classList.toggle("hidden", isAi);
  aiVoiceoverRow.classList.toggle("hidden", !isAi);
  voiceoverFileInput.required = !isAi;
  if (isAi) {
    loadVoiceList();
    toggleElevenlabsModelRow();
  }
});

voiceProviderSelect.addEventListener("change", () => {
  loadVoiceList();
  toggleElevenlabsModelRow();
});
voicePickerSelect.addEventListener("change", updatePreviewButtonState);
elevenlabsModelSelect.addEventListener("change", updatePreviewButtonState);

function toggleElevenlabsModelRow() {
  const isElevenlabs = voiceProviderSelect.value === "elevenlabs";
  elevenlabsModelRow.classList.toggle("hidden", !isElevenlabs);
  if (isElevenlabs && !elevenlabsModelsLoaded) loadElevenlabsModels();
}

async function loadElevenlabsModels() {
  try {
    const res = await fetch("/api/voices/models");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    elevenlabsModelSelect.innerHTML = "";
    (data.models || []).forEach((model) => {
      const opt = document.createElement("option");
      opt.value = model.id;
      opt.textContent = `${model.name} — ${model.description}`;
      elevenlabsModelSelect.appendChild(opt);
    });
    elevenlabsModelsLoaded = true;
  } catch (err) {
    elevenlabsModelSelect.innerHTML = '<option value="">Could not load models — using default</option>';
    console.error("failed to load ElevenLabs models", err);
  }
}

async function loadVoiceList() {
  voicePickerSelect.innerHTML = '<option value="">Loading voices…</option>';
  voicePreviewBtn.disabled = true;
  try {
    const res = await fetch(`/api/voices?provider=${voiceProviderSelect.value}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    currentVoices = data.voices || [];
    if (currentVoices.length === 0) {
      voicePickerSelect.innerHTML = '<option value="">No voices found — using default</option>';
      return;
    }

    voicePickerSelect.innerHTML = "";
    currentVoices.forEach((voice) => {
      const opt = document.createElement("option");
      opt.value = voice.id;
      opt.textContent = voice.description ? `${voice.name} — ${voice.description}` : voice.name;
      voicePickerSelect.appendChild(opt);
    });
    updatePreviewButtonState();
  } catch (err) {
    voicePickerSelect.innerHTML = '<option value="">Could not load voices — using default</option>';
    console.error("failed to load voices", err);
  }
}

function updatePreviewButtonState() {
  const selected = currentVoices.find((v) => v.id === voicePickerSelect.value);
  voicePreviewBtn.disabled = !selected;
}

voicePreviewBtn.addEventListener("click", async () => {
  const selected = currentVoices.find((v) => v.id === voicePickerSelect.value);
  if (!selected) return;

  const provider = voiceProviderSelect.value;
  const model = provider === "elevenlabs" ? elevenlabsModelSelect.value : null;
  const isDefaultModel = !model || model === "eleven_multilingual_v2";
  const originalLabel = voicePreviewBtn.textContent;

  if (provider === "elevenlabs" && selected.preview_url && isDefaultModel) {
    // Free, pre-recorded sample, and it's the default model — just play it.
    voicePreviewAudio.src = selected.preview_url;
    voicePreviewAudio.play();
    return;
  }

  // Either Google (no free preview exists) or a non-default ElevenLabs model
  // (the free preview_url wouldn't reflect that model's actual sound) —
  // generate an accurate short sample on demand (small real cost).
  voicePreviewBtn.disabled = true;
  voicePreviewBtn.textContent = "Generating…";
  try {
    let url = `/api/voices/preview?provider=${provider}&voice_id=${encodeURIComponent(selected.id)}`;
    if (model) url += `&model_id=${encodeURIComponent(model)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    voicePreviewAudio.src = URL.createObjectURL(blob);
    voicePreviewAudio.play();
  } catch (err) {
    console.error("preview generation failed", err);
  } finally {
    voicePreviewBtn.disabled = false;
    voicePreviewBtn.textContent = originalLabel;
  }
});

// ---------------- Visual source: stock+AI fallback vs AI-only ----------------

const visualModeSelect = document.getElementById("visualMode");
const aiQualitySelect = document.getElementById("aiQuality");
const aiQualityLabel = document.getElementById("aiQualityLabel");

visualModeSelect.addEventListener("change", () => {
  const isAiOnly = visualModeSelect.value === "ai_only";
  aiQualityLabel.innerHTML = isAiOnly
    ? "AI visual generator <em>(used for every scene)</em>"
    : "AI visual generator <em>(used only in AI-only mode)</em>";
  if (isAiOnly) {
    // "AI only" implies quality matters more than speed/cost by default —
    // still overridable, just a sensible starting point.
    aiQualitySelect.value = "high";
  }
});

// ---------------- Theme picker: starter scripts for popular niches ----------------

const THEME_SCRIPTS = {
  finance: "Most people think building wealth requires a huge salary. It doesn't. It requires a system. Today we're breaking down the exact framework for turning a modest monthly income into a real investment habit — starting with the one percent rule that changes how you think about every purchase. By the end of this video, you'll have a simple three-step plan you can start using this afternoon.",
  ai_tech: "A new AI tool drops almost every week now, and most of them promise to change how you work. Today we're testing one that claims to cut hours of manual work down to minutes. We'll walk through a real setup from scratch, show you exactly where it struggles, and give you an honest verdict on whether it's actually worth adding to your workflow.",
  motivation: "There's a moment right before you quit where everything feels heaviest. That moment isn't a sign to stop. It's a sign you're closer than you think. Today we're talking about the small, unglamorous habits that carry people through the hardest stretch of any goal — the one percent choices nobody sees, made on the days when nobody's watching.",
  documentary: "In a quiet corner of the world, a discovery changed how historians understood an entire era. This is the story of how it happened, the people who found it, and the questions it raised that are still being debated today. We'll trace the timeline from the first clue to the moment everything clicked into place.",
  true_story: "She didn't expect the phone call that changed everything. What started as an ordinary Tuesday turned into the beginning of a story she'd spend the next two years untangling. This is what happened when the truth finally came out — and why nobody saw it coming.",
  health: "Small daily habits shape your long-term health more than any single big decision ever will. Today we're looking at five changes backed by real research that take less than ten minutes a day, why they work at a biological level, and how to actually stick with them past the first week.",
  travel: "Some places change the way you see the rest of the world. This is a journey through a destination most travelers overlook — the food, the people, the unexpected turns — and why it might just be worth rearranging your next trip to see it for yourself.",
  product_review: "This device showed up with a lot of promises and a price tag to match. We've spent the last two weeks testing it in real conditions to find out if it holds up. Here's what worked, what didn't, and whether it actually earns a spot in your setup.",
  history: "Most people have never heard of this event, even though it quietly shaped the region for generations. Buried in old records and a handful of surviving accounts, the full story is stranger — and more human — than the footnotes suggest. Here's what actually happened.",
  ambient: "Let the sound of gentle rain settle around you as the light fades and the evening slows down. There's nowhere to be and nothing to do but breathe and let each thought pass by like clouds. Just settle in, and let the stillness take over.",
};

const themePicker = document.getElementById("themePicker");
themePicker.addEventListener("change", () => {
  const key = themePicker.value;
  if (!key) return;

  if (scriptTextarea.value.trim() && !confirm("This will replace your current script text. Continue?")) {
    themePicker.value = "";
    return;
  }

  scriptTextarea.value = THEME_SCRIPTS[key];
});

// ---------------- YouTube import: extract transcript, rewrite as original script ----------------

const youtubeUrlInput = document.getElementById("youtubeUrl");
const youtubeExtractBtn = document.getElementById("youtubeExtractBtn");
const youtubeStatus = document.getElementById("youtubeStatus");
const scriptTextarea = document.getElementById("script");

youtubeExtractBtn.addEventListener("click", async () => {
  const url = youtubeUrlInput.value.trim();
  if (!url) {
    showYoutubeStatus("Paste a YouTube URL first.", "failure");
    return;
  }

  youtubeExtractBtn.disabled = true;
  showYoutubeStatus("Fetching transcript and writing an original script in the same style…", "working");

  try {
    const res = await fetch("/api/youtube/rewrite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to extract/rewrite.");

    scriptTextarea.value = data.script;
    showYoutubeStatus("Done — new original script filled in below. Review and edit as you like.", "success");
  } catch (err) {
    showYoutubeStatus(err.message, "failure");
  } finally {
    youtubeExtractBtn.disabled = false;
  }
});

function showYoutubeStatus(message, kind) {
  youtubeStatus.textContent = message;
  youtubeStatus.className = `youtube-status ${kind}`;
  youtubeStatus.classList.remove("hidden");
}

// ---------------- Stage 1 -> 2: submit + prepare polling ----------------

els.jobForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  const script = document.getElementById("script").value;
  const music = document.getElementById("music").files[0];
  const isAiVoice = voiceoverMode.value === "ai";
  const aiQuality = document.getElementById("aiQuality").value;
  const visualMode = visualModeSelect.value;

  const fd = new FormData();
  fd.append("script", script);
  fd.append("ai_quality", aiQuality);
  fd.append("visual_mode", visualMode);
  if (music) fd.append("music", music);

  if (isAiVoice) {
    fd.append("voice_provider", voiceProviderSelect.value);
    const voiceId = voicePickerSelect.value.trim();
    if (voiceId) fd.append("voice_id", voiceId);
    if (voiceProviderSelect.value === "elevenlabs") {
      const model = elevenlabsModelSelect.value.trim();
      if (model) fd.append("elevenlabs_model", model);
    }
  } else {
    const voiceover = voiceoverFileInput.files[0];
    if (!voiceover) {
      showPrepareError("Choose a voiceover file, or switch to \"Generate with AI voice\".");
      return;
    }
    fd.append("voiceover", voiceover);
  }

  showPanel(els.preparePanel);
  els.prepareError.classList.add("hidden");
  renderFilmstrip(els.prepareFilmstrip, PREPARE_STAGES, isAiVoice ? "voiceover" : "splitting");

  try {
    const res = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    const { job_id } = await res.json();
    currentJobId = job_id;
    pollPrepare();
  } catch (err) {
    showPrepareError(`Could not start the job: ${err.message}`);
  }
});

async function pollPrepare() {
  try {
    const res = await fetch(`/api/jobs/${currentJobId}`);
    const job = await res.json();
    currentJob = job;

    renderFilmstrip(els.prepareFilmstrip, PREPARE_STAGES, job.status);
    els.prepareMessage.textContent = job.message || job.status;
    els.prepareFill.style.width = `${Math.round((job.progress || 0) * 100)}%`;

    if (job.status === "error") {
      showPrepareError(job.error || "Something went wrong while preparing the timeline.");
      return;
    }

    if (job.status === "ready_for_review") {
      renderReview(job);
      showPanel(els.reviewPanel);
      return;
    }

    setTimeout(pollPrepare, 1500);
  } catch (err) {
    showPrepareError(`Lost connection: ${err.message}`);
  }
}

function showPrepareError(message) {
  els.prepareError.textContent = message;
  els.prepareError.classList.remove("hidden");
}

// ---------------- Stage 3: review timeline ----------------

function fmtTime(seconds) {
  const s = Math.max(0, seconds || 0);
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

function parseTime(str) {
  const parts = str.split(":");
  if (parts.length === 2) {
    return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
  }
  return parseFloat(str) || 0;
}

function renderReview(job) {
  els.sceneList.innerHTML = "";
  job.scenes.forEach((scene) => els.sceneList.appendChild(renderSceneCard(scene)));
}

function renderSceneCard(scene) {
  const tpl = document.getElementById("sceneCardTemplate");
  const node = tpl.content.firstElementChild.cloneNode(true);

  node.dataset.sceneIndex = scene.index;
  node.querySelector(".scene-number").textContent = `#${scene.index + 1}`;

  node.querySelector(".scene-start").value = fmtTime(scene.start);
  node.querySelector(".scene-end").value = fmtTime(scene.end);
  node.querySelector(".scene-motion").value = scene.motion || "static";
  node.querySelector(".scene-emotion").value = scene.emotion || "neutral";
  node.querySelector(".scene-subtitle").value = scene.subtitle_text || scene.text;

  const kwContainer = node.querySelector(".scene-keywords");
  (scene.keywords || []).forEach((kw) => {
    const chip = document.createElement("span");
    chip.className = "keyword-chip";
    chip.textContent = kw;
    kwContainer.appendChild(chip);
  });

  updateSceneMedia(node, scene);
  renderCandidateStrip(node, scene);

  // ---- edit handlers, each PATCHes on change ----
  node.querySelector(".scene-start").addEventListener("change", (e) => {
    patchScene(scene.index, { start: parseTime(e.target.value) });
  });
  node.querySelector(".scene-end").addEventListener("change", (e) => {
    patchScene(scene.index, { end: parseTime(e.target.value) });
  });
  node.querySelector(".scene-motion").addEventListener("change", (e) => {
    scene.motion = e.target.value;
    patchScene(scene.index, { motion: e.target.value });
  });
  node.querySelector(".scene-emotion").addEventListener("change", (e) => {
    scene.emotion = e.target.value;
    patchScene(scene.index, { emotion: e.target.value });
  });
  node.querySelector(".scene-subtitle").addEventListener("change", (e) => {
    scene.subtitle_text = e.target.value;
    patchScene(scene.index, { subtitle_text: e.target.value });
  });

  const swapToggle = node.querySelector(".swap-toggle");
  const swapPanel = node.querySelector(".swap-panel");
  swapToggle.addEventListener("click", () => {
    swapPanel.classList.toggle("hidden");
  });

  const searchInput = node.querySelector(".candidate-search-input");
  const searchBtn = node.querySelector(".candidate-search-btn");
  searchBtn.addEventListener("click", () => runCandidateSearch(node, scene, searchInput.value));
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runCandidateSearch(node, scene, searchInput.value);
    }
  });

  return node;
}

function updateSceneMedia(node, scene) {
  const selected = (scene.candidates || []).find((c) => c.id === scene.selected_candidate_id);
  const img = node.querySelector(".scene-thumb");
  const aiBox = node.querySelector(".scene-ai-placeholder");

  if (selected && selected.thumbnail_url) {
    img.src = selected.thumbnail_url;
    img.classList.remove("hidden");
    aiBox.classList.add("hidden");
  } else if (selected && selected.source === "ai") {
    img.classList.add("hidden");
    aiBox.classList.remove("hidden");
    aiBox.querySelector(".ai-prompt").textContent = selected.prompt || scene.search_query || "";
  } else {
    img.classList.add("hidden");
    aiBox.classList.remove("hidden");
    aiBox.querySelector(".ai-prompt").textContent = "No preview available";
  }
}

function renderCandidateStrip(node, scene) {
  const strip = node.querySelector(".candidate-strip");
  strip.innerHTML = "";
  const tpl = document.getElementById("candidateThumbTemplate");

  (scene.candidates || []).forEach((candidate) => {
    const thumb = tpl.content.firstElementChild.cloneNode(true);
    thumb.classList.toggle("selected", candidate.id === scene.selected_candidate_id);

    const img = thumb.querySelector("img");
    if (candidate.thumbnail_url) {
      img.src = candidate.thumbnail_url;
    } else {
      thumb.querySelector(".candidate-ai-label").classList.remove("hidden");
    }

    thumb.addEventListener("click", () => {
      scene.selected_candidate_id = candidate.id;
      strip.querySelectorAll(".candidate-thumb").forEach((t) => t.classList.remove("selected"));
      thumb.classList.add("selected");
      updateSceneMedia(node, scene);
      patchScene(scene.index, { selected_candidate_id: candidate.id });
    });

    strip.appendChild(thumb);
  });
}

async function runCandidateSearch(node, scene, query) {
  if (!query.trim()) return;
  try {
    const res = await fetch(`/api/jobs/${currentJobId}/scenes/${scene.index}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    scene.candidates = data.candidates;
    renderCandidateStrip(node, scene);
  } catch (err) {
    console.error("candidate search failed", err);
  }
}

async function patchScene(sceneIndex, updates) {
  try {
    await fetch(`/api/jobs/${currentJobId}/scenes/${sceneIndex}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
  } catch (err) {
    console.error("failed to save edit", err);
  }
}

// ---------------- Stage 4: export ----------------

els.exportBtn.addEventListener("click", async () => {
  showPanel(els.exportPanel);
  els.exportError.classList.add("hidden");
  els.result.classList.add("hidden");
  renderFilmstrip(els.exportFilmstrip, EXPORT_STAGES, "exporting");

  try {
    const res = await fetch(`/api/jobs/${currentJobId}/export`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    pollExport();
  } catch (err) {
    showExportError(`Could not start export: ${err.message}`);
  }
});

async function pollExport() {
  try {
    const res = await fetch(`/api/jobs/${currentJobId}`);
    const job = await res.json();

    renderFilmstrip(els.exportFilmstrip, EXPORT_STAGES, job.status);
    els.exportMessage.textContent = job.message || job.status;
    els.exportFill.style.width = `${Math.round((job.progress || 0) * 100)}%`;

    if (job.status === "error") {
      showExportError(job.error || "The export failed for an unknown reason.");
      return;
    }

    if (job.status === "done") {
      const src = `/api/jobs/${currentJobId}/download`;
      els.previewVideo.src = src;
      els.downloadLink.href = src;
      els.result.classList.remove("hidden");
      return;
    }

    setTimeout(pollExport, 1500);
  } catch (err) {
    showExportError(`Lost connection: ${err.message}`);
  }
}

function showExportError(message) {
  els.exportError.textContent = message;
  els.exportError.classList.remove("hidden");
}
