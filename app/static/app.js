(() => {
  const settingsModal = document.querySelector("[data-settings-modal]");

  function openSettings(tab = "models") {
    if (!settingsModal) return;
    settingsModal.hidden = false;
    document.body.classList.add("modal-open");
    activateSettingsTab(tab);
  }

  function closeSettings() {
    if (!settingsModal) return;
    settingsModal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function activateSettingsTab(tab) {
    document.querySelectorAll("[data-settings-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.settingsTab === tab);
    });
    document.querySelectorAll("[data-settings-pane]").forEach((pane) => {
      pane.classList.toggle("active", pane.dataset.settingsPane === tab);
    });
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-settings]");
    if (opener) {
      openSettings(opener.dataset.openSettings || "models");
      return;
    }
    if (event.target.closest("[data-close-settings]")) {
      closeSettings();
      return;
    }
    const tab = event.target.closest("[data-settings-tab]");
    if (tab) {
      activateSettingsTab(tab.dataset.settingsTab);
      return;
    }
    if (event.target === settingsModal) {
      closeSettings();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSettings();
  });

  // Percentage sliders: mirror the slider value into the paired output as "NN%".
  function syncPercentOutput(slider) {
    const field = slider.closest(".range-field");
    const out = field && field.querySelector("[data-pct-out]");
    if (out) out.textContent = `${Math.round(parseFloat(slider.value || "0") * 100)}%`;
  }
  document.querySelectorAll("input[type=range][data-pct]").forEach(syncPercentOutput);
  document.addEventListener("input", (event) => {
    const slider = event.target.closest("input[type=range][data-pct]");
    if (slider) syncPercentOutput(slider);
  });

  document.querySelectorAll("[data-llm-preset]").forEach((select) => {
    select.addEventListener("change", () => {
      if (!select.value) return;
      const [provider, model] = select.value.split("|");
      const form = select.closest("form");
      const providerInput = form?.querySelector("[data-llm-provider]");
      const modelInput = form?.querySelector("[data-llm-model]");
      if (providerInput) providerInput.value = provider;
      if (modelInput) modelInput.value = model || "";
    });
  });

  // Clips library: toggle the "upload a finished clip" panel.
  const clipUploadPanel = document.querySelector("[data-clip-upload]");
  if (clipUploadPanel) {
    document.querySelectorAll("[data-open-clip-upload]").forEach((button) => {
      button.addEventListener("click", () => {
        clipUploadPanel.hidden = false;
        clipUploadPanel.querySelector("input[type=file]")?.focus();
        clipUploadPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    });
    document.querySelector("[data-close-clip-upload]")?.addEventListener("click", () => {
      clipUploadPanel.hidden = true;
    });
  }

  // Forms that kick off a slow synchronous job (e.g. rendering an uploaded clip)
  // reveal a busy overlay and lock their submit button until the page navigates.
  document.querySelectorAll("[data-busy-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      form.querySelector("[data-busy-overlay]")?.removeAttribute("hidden");
      const submit = form.querySelector('button[type="submit"]');
      if (submit) { submit.disabled = true; submit.textContent = "Рендер идёт…"; }
    });
  });

  const taskProgress = document.querySelector("[data-task-progress]");
  const taskDismissedKey = "vvf-task-history-dismissed";
  document.querySelector("[data-task-close]")?.addEventListener("click", () => {
    window.localStorage.setItem(taskDismissedKey, "1");
    taskProgress.hidden = true;
  });

  async function refreshTasks() {
    if (!taskProgress) return;
    try {
      const response = await fetch("/api/tasks/active");
      if (!response.ok) throw new Error("tasks failed");
      const tasks = await response.json();
      const count = taskProgress.querySelector("[data-task-count]");
      const title = taskProgress.querySelector("[data-task-title]");
      const bar = taskProgress.querySelector("[data-task-progress-bar]");
      const list = taskProgress.querySelector("[data-task-list]");
      const activeCount = tasks.filter((task) => ["queued", "running", "rendering"].includes(task.status)).length;
      const dismissed = window.localStorage.getItem(taskDismissedKey) === "1";
      if (activeCount) {
        window.localStorage.removeItem(taskDismissedKey);
      }
      taskProgress.hidden = tasks.length === 0 || (dismissed && activeCount === 0);
      if (count) count.textContent = String(activeCount || tasks.length);
      if (title) title.textContent = activeCount ? "Задачи выполняются" : "История задач";
      if (bar) bar.style.width = activeCount ? "62%" : "100%";
      if (list) {
        list.innerHTML = tasks
          .slice(0, 6)
          .map((task) => {
            const status = escapeHtml(task.status || "");
            const label = escapeHtml(task.label || "");
            const error = escapeHtml(task.error || "");
            return `<div class="task-item ${status}"><strong>${task.kind} #${task.id}</strong><span>${status} · ${label}</span>${error ? `<em>${error}</em>` : ""}</div>`;
          })
          .join("");
      }
    } catch {
      taskProgress.hidden = true;
    }
  }
  refreshTasks();
  window.setInterval(refreshTasks, 3000);

  // Auto page: live-refresh the automation run list.
  const autoRuns = document.querySelector("[data-auto-runs]");
  if (autoRuns) {
    const statusRu = (s) => ({
      queued: "в очереди", downloading: "скачивание", analyzing: "анализ",
      rendering: "рендер", scheduling: "очередь", done: "готово", failed: "ошибка",
    })[s] || s;
    const refreshAuto = async () => {
      try {
        const response = await fetch("/api/auto/runs");
        if (!response.ok) return;
        const runs = await response.json();
        if (!runs.length) return;
        autoRuns.innerHTML = runs
          .map((run) => {
            const st = escapeHtml(run.status || "");
            const meta =
              `<span>планов ${run.plans || 0}</span><span>клипов ${run.clips || 0}</span><span>постов ${run.jobs || 0}</span>` +
              (run.source_id ? `<a href="/sources/${run.source_id}">проект →</a>` : "");
            return `<div class="auto-run ${st}"><div class="auto-run-head"><strong>#${run.id} ${escapeHtml(run.label || "")}</strong><span class="status ${st}">${escapeHtml(statusRu(run.status))}</span></div><div class="muted">${escapeHtml(run.message || "")}</div><div class="auto-run-meta">${meta}</div>${run.error ? `<div class="publish-error">${escapeHtml(run.error)}</div>` : ""}</div>`;
          })
          .join("");
      } catch {}
    };
    refreshAuto();
    window.setInterval(refreshAuto, 3000);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  document.querySelectorAll("[data-smotvibe-form]").forEach((form) => {
    const urlInput = form.querySelector("[data-smotvibe-url]");
    const loadButton = form.querySelector("[data-smotvibe-load]");
    const submitButton = form.querySelector("[data-smotvibe-submit]");
    const picker = form.querySelector("[data-smotvibe-picker]");
    const seasonSelect = form.querySelector("[data-smotvibe-season]");
    const episodeSelect = form.querySelector("[data-smotvibe-episode]");
    const voiceSelect = form.querySelector("[data-smotvibe-voice]");
    const status = form.querySelector("[data-smotvibe-status]");
    const summary = form.querySelector("[data-smotvibe-summary]");
    const mediaUrlInput = form.querySelector("[data-smotvibe-media-url]");
    const refererInput = form.querySelector("[data-smotvibe-referer]");
    const audioFormatInput = form.querySelector("[data-smotvibe-audio-format]");
    const filenameLabelInput = form.querySelector("[data-smotvibe-filename-label]");
    let options = [];

    function resetSmotvibeSelection(message = "") {
      options = [];
      if (picker) picker.hidden = true;
      if (mediaUrlInput) mediaUrlInput.value = "";
      if (refererInput) refererInput.value = "";
      if (audioFormatInput) audioFormatInput.value = "";
      if (filenameLabelInput) filenameLabelInput.value = "";
      if (summary) summary.textContent = "";
      if (status) status.textContent = message;
      if (submitButton) submitButton.textContent = "Добавить URL";
    }

    function optionSeason(option) {
      return option.season || "1";
    }

    function optionEpisode(option) {
      return option.episode || "1";
    }

    function sortNatural(values) {
      return values.sort((a, b) =>
        String(a).localeCompare(String(b), "ru", { numeric: true, sensitivity: "base" })
      );
    }

    function setSelectOptions(select, values, labelPrefix, currentValue = "") {
      if (!select) return "";
      const selected = values.includes(currentValue) ? currentValue : values[0] || "";
      select.innerHTML = values
        .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(labelPrefix ? `${labelPrefix} ${value}` : value)}</option>`)
        .join("");
      select.value = selected;
      return selected;
    }

    function voiceLabel(option) {
      const parts = [option.translation || "Default", option.provider, option.quality].filter(Boolean);
      return parts.join(" · ");
    }

    function fillVoiceSelect(season, episode, currentOptionId = "") {
      const voices = options.filter((option) => optionSeason(option) === season && optionEpisode(option) === episode);
      if (!voiceSelect) return null;
      const selected = voices.some((option) => option.id === currentOptionId) ? currentOptionId : voices[0]?.id || "";
      voiceSelect.innerHTML = voices
        .map((option) => `<option value="${escapeHtml(option.id)}">${escapeHtml(voiceLabel(option))}</option>`)
        .join("");
      voiceSelect.value = selected;
      return voices.find((option) => option.id === selected) || null;
    }

    function applySelectedOption(option) {
      if (!option) {
        resetSmotvibeSelection("Вариант не выбран");
        return;
      }
      if (mediaUrlInput) mediaUrlInput.value = option.media_url || "";
      if (refererInput) refererInput.value = option.referer || "";
      if (audioFormatInput) audioFormatInput.value = option.audio_format_id || "";
      if (filenameLabelInput) filenameLabelInput.value = option.filename_label || "";
      if (submitButton) submitButton.textContent = "Скачать выбранное";
      if (summary) {
        const duration = Number(option.duration_sec || 0);
        const durationText = duration ? ` · ${Math.round(duration / 60)} мин` : "";
        summary.textContent = `${option.title || `Сезон ${optionSeason(option)}, серия ${optionEpisode(option)}`} · ${voiceLabel(option)}${durationText}`;
      }
      if (status) status.textContent = "";
    }

    function refreshSmotvibePicker() {
      if (!options.length) {
        resetSmotvibeSelection("Варианты не найдены");
        return;
      }
      const currentSeason = seasonSelect?.value || "";
      const currentEpisode = episodeSelect?.value || "";
      const currentVoice = voiceSelect?.value || "";
      const seasons = sortNatural(Array.from(new Set(options.map(optionSeason))));
      const season = setSelectOptions(seasonSelect, seasons, "Сезон", currentSeason);
      const episodes = sortNatural(
        Array.from(new Set(options.filter((option) => optionSeason(option) === season).map(optionEpisode)))
      );
      const episode = setSelectOptions(episodeSelect, episodes, "Серия", currentEpisode);
      const selectedOption = fillVoiceSelect(season, episode, currentVoice);
      if (picker) picker.hidden = false;
      applySelectedOption(selectedOption);
    }

    loadButton?.addEventListener("click", async () => {
      const url = (urlInput?.value || "").trim();
      resetSmotvibeSelection("");
      if (!url) {
        if (status) status.textContent = "Введите URL";
        return;
      }
      if (loadButton) {
        loadButton.disabled = true;
        loadButton.textContent = "Ищу...";
      }
      if (status) status.textContent = "Поиск вариантов...";
      try {
        const response = await fetch("/api/smotvibe/options", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
        const payload = await response.json().catch(() => []);
        if (!response.ok) {
          throw new Error(payload.detail || "Не удалось получить варианты");
        }
        options = Array.isArray(payload) ? payload : [];
        if (status) status.textContent = options.length ? `Найдено вариантов: ${options.length}` : "Варианты не найдены";
        refreshSmotvibePicker();
      } catch (error) {
        resetSmotvibeSelection(error.message || "Ошибка поиска вариантов");
      } finally {
        if (loadButton) {
          loadButton.disabled = false;
          loadButton.textContent = "Найти варианты Smotvibe";
        }
      }
    });

    seasonSelect?.addEventListener("change", refreshSmotvibePicker);
    episodeSelect?.addEventListener("change", refreshSmotvibePicker);
    voiceSelect?.addEventListener("change", () => {
      applySelectedOption(options.find((option) => option.id === voiceSelect.value) || null);
    });
    urlInput?.addEventListener("input", () => {
      if (options.length || mediaUrlInput?.value) resetSmotvibeSelection("");
    });
  });

  const studio = document.querySelector("[data-studio]");
  if (!studio) return;

  const steps = Array.from(studio.querySelectorAll("[data-step]"));
  const stepButtons = Array.from(studio.querySelectorAll("[data-step-target]"));

  function activateStep(stepName) {
    steps.forEach((step) => step.classList.toggle("active", step.dataset.step === stepName));
    stepButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.stepTarget === stepName);
    });
    const url = new URL(window.location.href);
    url.searchParams.set("stage", stepName);
    window.history.replaceState({}, "", url);
  }

  // Pause the source video whenever we leave the preview stage (it must never
  // keep playing while hidden), and (re)lay out the timeline when it becomes
  // visible — its width can only be measured once the stage is on screen.
  function syncStageMedia(stepName) {
    if (stepName !== "preview" && video && !video.paused) video.pause();
    if (stepName === "preview") requestAnimationFrame(() => layoutTimeline());
  }
  stepButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activateStep(button.dataset.stepTarget);
      syncStageMedia(button.dataset.stepTarget);
    });
  });

  // --------------------------------- player ----------------------------------
  const video = studio.querySelector("[data-studio-video]");
  const playButton = studio.querySelector("[data-player-play]");
  const seek = studio.querySelector("[data-player-seek]");
  const timeLabel = studio.querySelector("[data-player-time]");
  const deckOverlay = studio.querySelector("[data-deck-overlay]");
  const sourceDuration = Number(studio.dataset.sourceDuration || 0);
  let activeRangeEnd = null;

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  function videoDuration() {
    return (video && Number.isFinite(video.duration) && video.duration) || sourceDuration || 0;
  }
  function fmtClock(value) {
    let s = Math.max(0, Math.round(Number(value) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    s = s % 60;
    const mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? `${h}:` : "") + `${mm}:${String(s).padStart(2, "0")}`;
  }
  function setPlayIcon() {
    const playing = video && !video.paused;
    if (playButton) playButton.textContent = playing ? "⏸" : "▶";
    if (deckOverlay) deckOverlay.hidden = !!playing;
  }
  function togglePlay() {
    if (!video) return;
    activeRangeEnd = null;
    if (video.paused) video.play(); else video.pause();
  }
  function seekBy(delta) {
    if (!video) return;
    activeRangeEnd = null;
    video.currentTime = clamp((video.currentTime || 0) + delta, 0, videoDuration());
    updatePlayerUi(); updatePlayhead(); updateReadout();
  }
  function playRange(start, end) {
    if (!video) return;
    const previewActive = steps.find((s) => s.dataset.step === "preview")?.classList.contains("active");
    if (!previewActive) { activateStep("preview"); syncStageMedia("preview"); }
    activeRangeEnd = Number(end);
    video.currentTime = Math.max(0, Number(start) || 0);
    video.play();
  }
  function updatePlayerUi() {
    if (!video) return;
    if (seek) { seek.max = String(videoDuration() || 0); seek.value = String(video.currentTime || 0); }
    if (timeLabel) timeLabel.textContent = `${fmtClock(video.currentTime)} / ${fmtClock(videoDuration())}`;
  }

  if (video) {
    video.addEventListener("click", togglePlay);
    video.addEventListener("play", setPlayIcon);
    video.addEventListener("pause", setPlayIcon);
    video.addEventListener("timeupdate", () => {
      if (activeRangeEnd !== null && video.currentTime >= activeRangeEnd) {
        video.pause();
        activeRangeEnd = null;
      }
      updatePlayerUi();
      updatePlayhead();
      updateReadout();
    });
    video.addEventListener("loadedmetadata", () => { updatePlayerUi(); layoutTimeline(); });
  }
  if (playButton) playButton.addEventListener("click", togglePlay);
  if (seek && video) {
    seek.addEventListener("input", () => {
      activeRangeEnd = null;
      video.currentTime = Number(seek.value || 0);
      updatePlayerUi();
      updatePlayhead();
      updateReadout();
    });
  }
  studio.querySelector("[data-player-back]")?.addEventListener("click", () => seekBy(-5));
  studio.querySelector("[data-player-fwd]")?.addEventListener("click", () => seekBy(5));

  // "+ сегмент": add a clip to the active plan at the current playhead position
  // by submitting that plan's add-segment form (its submit handler fills in the
  // start/end from the playhead).
  studio.querySelector("[data-add-at-playhead]")?.addEventListener("click", () => {
    const activeHead = studio.querySelector("[data-plan-head].active") || studio.querySelector("[data-plan-head]");
    const planId = activeHead?.dataset.planHead;
    const form = planId && studio.querySelector(`[data-plan-detail="${planId}"] [data-add-segment-form]`);
    if (form) form.requestSubmit();
  });

  // Keyboard shortcuts on the preview stage: space = play/pause, arrows = seek.
  document.addEventListener("keydown", (event) => {
    const previewActive = steps.find((s) => s.dataset.step === "preview")?.classList.contains("active");
    if (!previewActive || !video) return;
    const t = event.target;
    if (t && (t.matches("input, textarea, select") || t.isContentEditable)) return;
    if (event.key === " " || event.code === "Space") { event.preventDefault(); togglePlay(); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); seekBy(event.shiftKey ? -5 : -1); }
    else if (event.key === "ArrowRight") { event.preventDefault(); seekBy(event.shiftKey ? 5 : 1); }
    else if (event.key === "Home") { event.preventDefault(); video.currentTime = 0; updatePlayerUi(); updatePlayhead(); }
  });

  // ----------------------- right-panel segment editors -----------------------
  function selectPlan(planId) {
    studio.querySelectorAll("[data-plan-head]").forEach((h) =>
      h.classList.toggle("active", h.dataset.planHead === String(planId)));
    studio.querySelectorAll("[data-tl-track]").forEach((t) =>
      t.classList.toggle("active", t.dataset.planId === String(planId)));
    studio.querySelectorAll("[data-plan-detail]").forEach((d) =>
      d.classList.toggle("hidden", d.dataset.planDetail !== String(planId)));
  }
  function selectSegment(segmentId) {
    const clip = studio.querySelector(`.tl-clip[data-segment-select="${segmentId}"]`);
    if (clip) selectPlan(clip.dataset.planSelect);
    studio.querySelectorAll(".tl-clip[data-segment-select]").forEach((c) =>
      c.classList.toggle("active", c.dataset.segmentSelect === String(segmentId)));
    studio.querySelectorAll("[data-segment-editor]").forEach((e) => {
      const on = e.dataset.segmentEditor === String(segmentId);
      e.classList.toggle("active", on);
      if (on) e.scrollIntoView({ block: "nearest" });
    });
  }
  function setSegmentUi(segmentId, start, end, message) {
    const s = Number(start), e = Number(end);
    const clip = studio.querySelector(`.tl-clip[data-segment-select="${segmentId}"]`);
    if (clip) {
      clip.dataset.rangeStart = String(s);
      clip.dataset.rangeEnd = String(e);
      positionClip(clip);
      clip.title = `${fmtClock(s)}–${fmtClock(e)}`;
    }
    const editor = studio.querySelector(`[data-segment-editor="${segmentId}"]`);
    if (editor) {
      editor.dataset.startSec = String(s);
      editor.dataset.endSec = String(e);
      const pb = editor.querySelector("[data-play-range]");
      if (pb) { pb.dataset.start = String(s); pb.dataset.end = String(e); }
      const ro = editor.querySelector("[data-segment-time]");
      if (ro) ro.textContent = `${s.toFixed(2)}–${e.toFixed(2)}s`;
      const st = editor.querySelector("[data-editor-status]");
      if (st && message !== undefined) st.textContent = message;
    }
  }
  async function saveSegment(segmentId, start, end) {
    const editor = studio.querySelector(`[data-segment-editor="${segmentId}"]`);
    const status = editor?.querySelector("[data-editor-status]");
    if (status) status.textContent = "сохраняю…";
    try {
      const r = await fetch(`/api/segments/${segmentId}/timecodes`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_sec: Number(start), end_sec: Number(end) }),
      });
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || "ошибка"); }
      const p = await r.json();
      setSegmentUi(segmentId, p.start_sec, p.end_sec, "сохранено");
    } catch (err) { if (status) status.textContent = err.message; }
  }

  studio.querySelectorAll("[data-play-range]").forEach((b) =>
    b.addEventListener("click", () => playRange(b.dataset.start, b.dataset.end)));
  studio.querySelectorAll("[data-plan-head]").forEach((h) =>
    h.addEventListener("click", () => selectPlan(h.dataset.planSelect)));
  studio.querySelectorAll("[data-add-segment-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      const start = Math.max(0, video ? video.currentTime : 0);
      const end = Math.min(videoDuration() || start + 12, start + 12);
      form.querySelector('input[name="start_sec"]').value = String(start);
      form.querySelector('input[name="end_sec"]').value = String(end);
      const t = form.querySelector('input[name="title"]');
      if (t && !t.value.trim()) t.value = `Сегмент ${fmtClock(start)}`;
    });
  });

  // ------------------------------ pro timeline -------------------------------
  const timeline = studio.querySelector("[data-timeline]");
  const tlScroll = timeline && timeline.querySelector("[data-tl-scroll]");
  const tlInner = timeline && timeline.querySelector("[data-tl-inner]");
  const tlRuler = timeline && timeline.querySelector("[data-tl-ruler]");
  const tlPlayhead = timeline && timeline.querySelector("[data-tl-playhead]");
  const tlReadout = timeline && timeline.querySelector("[data-tl-readout]");
  // Zoom controls live in the transport bar (outside the timeline element).
  const tlZoomLabel = studio.querySelector("[data-tl-zoom-label]");
  const tlClips = timeline ? Array.from(timeline.querySelectorAll(".tl-clip")) : [];
  const TL_DURATION = timeline ? Number(timeline.dataset.duration || sourceDuration || 0) : 0;
  const MIN_SEG = Math.min(2, TL_DURATION || 2);
  const SNAP_PX = 7;
  let pps = 0, fitPps = 0, maxPps = 0;

  function positionClip(clip) {
    const s = Number(clip.dataset.rangeStart) || 0;
    const e = Number(clip.dataset.rangeEnd) || 0;
    clip.style.left = `${s * pps}px`;
    clip.style.width = `${Math.max(2, (e - s) * pps)}px`;
    if (clip.dataset.color) clip.style.background = clip.dataset.color;
  }
  function buildRuler() {
    if (!tlRuler) return;
    tlRuler.innerHTML = "";
    const steps2 = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
    const step = steps2.find((i) => i * pps >= 66) || steps2[steps2.length - 1];
    const frag = document.createDocumentFragment();
    for (let t = 0; t <= TL_DURATION + 0.001; t += step) {
      const tick = document.createElement("div");
      tick.className = "tl-tick";
      tick.style.left = `${t * pps}px`;
      const span = document.createElement("span");
      span.textContent = fmtClock(t);
      tick.appendChild(span);
      frag.appendChild(tick);
    }
    tlRuler.appendChild(frag);
  }
  function updatePlayhead() {
    if (!tlPlayhead || !video) return;
    const x = (video.currentTime || 0) * pps;
    tlPlayhead.style.left = `${x}px`;
    if (!video.paused && tlScroll) {
      const left = tlScroll.scrollLeft, right = left + tlScroll.clientWidth;
      if (x < left + 40 || x > right - 40) tlScroll.scrollLeft = x - tlScroll.clientWidth / 2;
    }
  }
  function updateReadout() {
    if (tlReadout) tlReadout.textContent = `${fmtClock(video ? video.currentTime : 0)} / ${fmtClock(TL_DURATION)}`;
  }
  function layoutTimeline(centerTime) {
    if (!timeline || !tlScroll || !tlInner || !TL_DURATION) return;
    const view = tlScroll.clientWidth || timeline.clientWidth || 1;
    fitPps = view / TL_DURATION;
    maxPps = Math.max(fitPps, 80);
    pps = clamp(pps || fitPps, fitPps, maxPps);
    tlInner.style.width = `${Math.max(view, TL_DURATION * pps)}px`;
    buildRuler();
    tlClips.forEach(positionClip);
    updatePlayhead();
    if (tlZoomLabel) tlZoomLabel.textContent = `${(pps / fitPps).toFixed(1)}×`;
    if (typeof centerTime === "number") tlScroll.scrollLeft = centerTime * pps - tlScroll.clientWidth / 2;
    updateReadout();
  }
  function zoomBy(factor) {
    if (!pps) return;
    const centerTime = (tlScroll.scrollLeft + tlScroll.clientWidth / 2) / pps;
    pps = clamp(pps * factor, fitPps, maxPps);
    layoutTimeline(centerTime);
  }
  if (timeline) {
    studio.querySelectorAll("[data-tl-zoom]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.tlZoom;
        if (k === "in") zoomBy(1.6);
        else if (k === "out") zoomBy(1 / 1.6);
        else { pps = fitPps; layoutTimeline(); tlScroll.scrollLeft = 0; }
      }));
    tlScroll.addEventListener("wheel", (e) => {
      if (e.ctrlKey) { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15); }
      else if (e.deltaX === 0 && e.deltaY !== 0 && tlInner.clientWidth > tlScroll.clientWidth) {
        tlScroll.scrollLeft += e.deltaY; e.preventDefault();
      }
    }, { passive: false });
  }

  // seek by clicking / scrubbing the ruler or empty track space
  function seekToClientX(clientX) {
    if (!video || !pps || !tlInner) return;
    const rect = tlInner.getBoundingClientRect();
    activeRangeEnd = null;
    video.currentTime = clamp((clientX - rect.left) / pps, 0, TL_DURATION);
    updatePlayerUi(); updatePlayhead(); updateReadout();
  }
  let scrubbing = false;
  if (tlRuler) {
    tlRuler.addEventListener("pointerdown", (e) => {
      scrubbing = true; tlRuler.setPointerCapture?.(e.pointerId); seekToClientX(e.clientX);
    });
    tlRuler.addEventListener("pointermove", (e) => { if (scrubbing) seekToClientX(e.clientX); });
    tlRuler.addEventListener("pointerup", () => { scrubbing = false; });
  }
  studio.querySelectorAll("[data-tl-track]").forEach((track) => {
    track.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".tl-clip")) return;
      seekToClientX(e.clientX);
    });
  });

  // drag-to-move and edge-resize clips, with snapping to neighbours / playhead
  let drag = null;
  function snapTime(time, exclude) {
    const x = time * pps;
    let best = time, bestDist = SNAP_PX;
    const cands = [0, TL_DURATION];
    if (video) cands.push(video.currentTime || 0);
    tlClips.forEach((c) => {
      if (c === exclude) return;
      cands.push(Number(c.dataset.rangeStart), Number(c.dataset.rangeEnd));
    });
    cands.forEach((t) => { const d = Math.abs(t * pps - x); if (d < bestDist) { bestDist = d; best = t; } });
    return best;
  }
  tlClips.forEach((clip) => {
    clip.addEventListener("pointerdown", (e) => {
      const grip = e.target.closest("[data-resize-handle]");
      drag = {
        clip,
        mode: grip ? grip.dataset.resizeHandle : "move",
        startX: e.clientX,
        moved: false,
        s: Number(clip.dataset.rangeStart),
        e: Number(clip.dataset.rangeEnd),
        len: Number(clip.dataset.rangeEnd) - Number(clip.dataset.rangeStart),
      };
      clip.setPointerCapture?.(e.pointerId);
      clip.classList.add("dragging");
      e.preventDefault();
    });
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag || !pps) return;
    const dt = (e.clientX - drag.startX) / pps;
    if (Math.abs(e.clientX - drag.startX) > 3) drag.moved = true;
    let s = drag.s, en = drag.e;
    if (drag.mode === "move") {
      s = clamp(snapTime(drag.s + dt, drag.clip), 0, TL_DURATION - drag.len);
      en = s + drag.len;
    } else if (drag.mode === "start") {
      s = clamp(snapTime(drag.s + dt, drag.clip), 0, drag.e - MIN_SEG);
    } else {
      en = clamp(snapTime(drag.e + dt, drag.clip), drag.s + MIN_SEG, TL_DURATION);
    }
    setSegmentUi(drag.clip.dataset.segmentSelect, s, en, "отпустите для сохранения");
  });
  document.addEventListener("pointerup", () => {
    if (!drag) return;
    const { clip, moved } = drag;
    clip.classList.remove("dragging");
    const segId = clip.dataset.segmentSelect;
    const s = Number(clip.dataset.rangeStart), en = Number(clip.dataset.rangeEnd);
    drag = null;
    selectSegment(segId);
    if (moved) saveSegment(segId, s, en);
    else playRange(s, en);
  });

  // ------------------------- publish picker + render -------------------------
  const publishForm = studio.querySelector("[data-publish-form]");
  const titleInput = studio.querySelector("[data-publish-title]");
  const descriptionInput = studio.querySelector("[data-publish-description]");
  const renderForm = studio.querySelector("[data-render-form]");
  if (renderForm) {
    renderForm.addEventListener("submit", () => {
      const busy = renderForm.querySelector("[data-render-busy]");
      const submit = renderForm.querySelector("[data-render-submit]");
      if (busy) busy.hidden = false;
      if (submit) { submit.disabled = true; submit.textContent = "Рендер идёт…"; }
      renderForm.classList.add("is-busy");
    });
  }
  studio.querySelectorAll("[data-pick-clip]").forEach((button) => {
    button.addEventListener("click", () => {
      const clipId = button.dataset.pickClip;
      studio.querySelectorAll("[data-pick-clip]").forEach((i) => i.classList.toggle("active", i === button));
      studio.querySelectorAll("[data-clip-preview]").forEach((p) => p.classList.toggle("hidden", p.dataset.clipPreview !== clipId));
      if (publishForm) publishForm.action = `/ui/clips/${clipId}/posts`;
      if (titleInput) titleInput.value = button.dataset.title || "";
      if (descriptionInput) descriptionInput.value = button.dataset.description || "";
    });
  });

  // ----------------------------------- init ----------------------------------
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => layoutTimeline(), 120);
  });
  const firstClip = studio.querySelector("[data-pick-clip]");
  if (firstClip) firstClip.classList.add("active");
  const firstHead = studio.querySelector("[data-plan-head]");
  if (firstHead) selectPlan(firstHead.dataset.planSelect);
  const firstSeg = studio.querySelector(".tl-clip[data-segment-select]");
  if (firstSeg) selectSegment(firstSeg.dataset.segmentSelect);
  setPlayIcon();
  updatePlayerUi();
  activateStep(studio.dataset.initialStage || "analysis");
  layoutTimeline();
  syncStageMedia(studio.dataset.initialStage || "analysis");
})();
