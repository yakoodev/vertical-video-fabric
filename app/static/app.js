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

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

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

  stepButtons.forEach((button) => {
    button.addEventListener("click", () => activateStep(button.dataset.stepTarget));
  });
  activateStep(studio.dataset.initialStage || "analysis");

  const video = studio.querySelector("[data-studio-video]");
  const playButton = studio.querySelector("[data-player-play]");
  const seek = studio.querySelector("[data-player-seek]");
  const timeLabel = studio.querySelector("[data-player-time]");
  let activeRangeEnd = null;
  let dragState = null;

  function formatTime(value) {
    return Number(value || 0).toFixed(1);
  }

  function updatePlayerUi() {
    if (!video || !seek) return;
    seek.value = String(video.currentTime || 0);
    if (timeLabel) {
      const duration = video.duration || Number(studio.dataset.sourceDuration || 0);
      timeLabel.textContent = `${formatTime(video.currentTime)} / ${formatTime(duration)}s`;
    }
  }

  function playRange(start, end) {
    if (!video) return;
    activeRangeEnd = Number(end);
    video.currentTime = Math.max(0, Number(start));
    video.play();
  }

  function timelinePercent(value) {
    const duration = Number(studio.dataset.sourceDuration || 0);
    if (!duration) return 0;
    return Math.max(0, Math.min(100, (Number(value || 0) / duration) * 100));
  }

  function timelineTime(percent) {
    const duration = Number(studio.dataset.sourceDuration || 0);
    return Math.max(0, Math.min(duration, (Number(percent || 0) / 100) * duration));
  }

  function pointerTime(clientX, track) {
    const rect = track.getBoundingClientRect();
    const percent = ((clientX - rect.left) / Math.max(1, rect.width)) * 100;
    return timelineTime(Math.max(0, Math.min(100, percent)));
  }

  function setSegmentUi(segmentId, start, end, message = "") {
    const startValue = Number(start);
    const endValue = Number(end);
    const segmentButton = studio.querySelector(`[data-segment-select="${segmentId}"]`);
    if (segmentButton) {
      segmentButton.dataset.rangeStart = String(startValue);
      segmentButton.dataset.rangeEnd = String(endValue);
      segmentButton.style.left = `${timelinePercent(startValue)}%`;
      segmentButton.style.width = `${Math.max(0.25, timelinePercent(endValue) - timelinePercent(startValue))}%`;
      segmentButton.title = `${startValue.toFixed(1)}–${endValue.toFixed(1)}s`;
    }
    const editor = studio.querySelector(`[data-segment-editor="${segmentId}"]`);
    if (editor) {
      editor.dataset.startSec = String(startValue);
      editor.dataset.endSec = String(endValue);
      const playButton = editor.querySelector("[data-play-range]");
      if (playButton) {
        playButton.dataset.start = String(startValue);
        playButton.dataset.end = String(endValue);
      }
      const readout = editor.querySelector("[data-segment-time]");
      if (readout) readout.textContent = `${startValue.toFixed(3)}–${endValue.toFixed(3)}s`;
      const status = editor.querySelector("[data-editor-status]");
      if (status) status.textContent = message;
    }
  }

  function selectPlan(planId) {
    studio.querySelectorAll("[data-plan-row]").forEach((row) => {
      row.classList.toggle("active", row.dataset.planRow === String(planId));
    });
    studio.querySelectorAll("[data-plan-detail]").forEach((detail) => {
      detail.classList.toggle("hidden", detail.dataset.planDetail !== String(planId));
    });
  }

  function selectSegment(segmentId) {
    const segmentButton = studio.querySelector(`[data-segment-select="${segmentId}"]`);
    if (!segmentButton) return;
    selectPlan(segmentButton.dataset.planSelect);
    studio.querySelectorAll("[data-segment-select]").forEach((button) => {
      button.classList.toggle("active", button.dataset.segmentSelect === String(segmentId));
    });
    studio.querySelectorAll("[data-segment-editor]").forEach((editor) => {
      editor.classList.toggle("active", editor.dataset.segmentEditor === String(segmentId));
    });
  }

  function refreshPlanRange(planId) {
    const editors = Array.from(studio.querySelectorAll(`[data-segment-editor][data-plan-id="${planId}"]`));
    const times = editors
      .map((editor) => ({
        start: Number(editor.dataset.startSec),
        end: Number(editor.dataset.endSec),
      }))
      .filter((item) => Number.isFinite(item.start) && Number.isFinite(item.end));
    if (!times.length) return;
    const start = Math.min(...times.map((item) => item.start));
    const end = Math.max(...times.map((item) => item.end));
    studio.querySelectorAll(`[data-plan-select="${planId}"]`).forEach((node) => {
      if (node.classList.contains("clip-range") || node.classList.contains("clip-track-label")) {
        node.dataset.rangeStart = String(start);
        node.dataset.rangeEnd = String(end);
      }
      if (node.classList.contains("clip-range")) {
        node.style.left = `${timelinePercent(start)}%`;
        node.style.width = `${Math.max(0.25, timelinePercent(end) - timelinePercent(start))}%`;
      }
    });
  }

  if (video) {
    video.addEventListener("timeupdate", () => {
      if (activeRangeEnd !== null && video.currentTime >= activeRangeEnd) {
        video.pause();
        activeRangeEnd = null;
      }
      updatePlayerUi();
    });
    video.addEventListener("loadedmetadata", updatePlayerUi);
  }

  if (playButton && video) {
    playButton.addEventListener("click", () => {
      activeRangeEnd = null;
      if (video.paused) video.play();
      else video.pause();
    });
  }

  if (seek && video) {
    seek.addEventListener("input", () => {
      activeRangeEnd = null;
      video.currentTime = Number(seek.value || 0);
      updatePlayerUi();
    });
  }

  studio.querySelectorAll("[data-add-segment-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      const duration = Number(studio.dataset.sourceDuration || 0);
      const start = Math.max(0, video ? video.currentTime : 0);
      const end = Math.min(duration, start + 12);
      const startInput = form.querySelector('input[name="start_sec"]');
      const endInput = form.querySelector('input[name="end_sec"]');
      const titleInput = form.querySelector('input[name="title"]');
      if (startInput) startInput.value = String(start);
      if (endInput) endInput.value = String(end);
      if (titleInput && !titleInput.value.trim()) titleInput.value = `Segment ${start.toFixed(1)}s`;
    });
  });

  studio.querySelectorAll("[data-play-range]").forEach((button) => {
    button.addEventListener("click", () => {
      playRange(button.dataset.start, button.dataset.end);
    });
  });

  studio.querySelectorAll("[data-plan-select]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.dragging === "true") return;
      selectPlan(button.dataset.planSelect);
      playRange(button.dataset.rangeStart, button.dataset.rangeEnd);
    });
  });

  studio.querySelectorAll("[data-segment-select]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.dragging === "true") {
        button.dataset.dragging = "false";
        return;
      }
      selectSegment(button.dataset.segmentSelect);
      playRange(button.dataset.rangeStart, button.dataset.rangeEnd);
    });
  });

  async function saveSegment(segmentId, start, end) {
    const editor = studio.querySelector(`[data-segment-editor="${segmentId}"]`);
    const status = editor?.querySelector("[data-editor-status]");
    if (status) status.textContent = "сохраняю...";
    try {
      const response = await fetch(`/api/segments/${segmentId}/timecodes`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ start_sec: Number(start), end_sec: Number(end) }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "ошибка сохранения");
      }
      const payload = await response.json();
      setSegmentUi(segmentId, payload.start_sec, payload.end_sec, "сохранено");
      if (editor) refreshPlanRange(editor.dataset.planId);
    } catch (error) {
      if (status) status.textContent = error.message;
    }
  }

  studio.querySelectorAll("[data-resize-handle]").forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const segment = handle.closest("[data-segment-select]");
      const track = handle.closest(".clip-track");
      if (!segment || !track) return;
      dragState = {
        handle: handle.dataset.resizeHandle,
        segment,
        track,
        segmentId: segment.dataset.segmentSelect,
        planId: segment.dataset.planSelect,
        start: Number(segment.dataset.rangeStart),
        end: Number(segment.dataset.rangeEnd),
      };
      segment.classList.add("resizing");
      segment.dataset.dragging = "true";
      handle.setPointerCapture?.(event.pointerId);
    });
  });

  document.addEventListener("pointermove", (event) => {
    if (!dragState) return;
    const minDuration = Math.min(5, Number(studio.dataset.sourceDuration || 0));
    const time = pointerTime(event.clientX, dragState.track);
    let start = dragState.start;
    let end = dragState.end;
    if (dragState.handle === "start") {
      start = Math.min(time, end - minDuration);
    } else {
      end = Math.max(time, start + minDuration);
    }
    start = Math.max(0, start);
    end = Math.min(Number(studio.dataset.sourceDuration || end), end);
    setSegmentUi(dragState.segmentId, start, end, "перетащите край и отпустите");
    refreshPlanRange(dragState.planId);
  });

  document.addEventListener("pointerup", () => {
    if (!dragState) return;
    const segment = dragState.segment;
    const segmentId = dragState.segmentId;
    const start = Number(segment.dataset.rangeStart);
    const end = Number(segment.dataset.rangeEnd);
    segment.classList.remove("resizing");
    dragState = null;
    saveSegment(segmentId, start, end);
  });

  const publishForm = studio.querySelector("[data-publish-form]");
  const titleInput = studio.querySelector("[data-publish-title]");
  const descriptionInput = studio.querySelector("[data-publish-description]");

  const renderForm = studio.querySelector("[data-render-form]");
  if (renderForm) {
    renderForm.addEventListener("submit", () => {
      const busy = renderForm.querySelector("[data-render-busy]");
      const submit = renderForm.querySelector("[data-render-submit]");
      if (busy) busy.hidden = false;
      if (submit) {
        submit.disabled = true;
        submit.textContent = "Рендер идет...";
      }
      renderForm.classList.add("is-busy");
    });
  }

  studio.querySelectorAll("[data-pick-clip]").forEach((button) => {
    button.addEventListener("click", () => {
      const clipId = button.dataset.pickClip;
      studio.querySelectorAll("[data-pick-clip]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      studio.querySelectorAll("[data-clip-preview]").forEach((preview) => {
        preview.classList.toggle("hidden", preview.dataset.clipPreview !== clipId);
      });
      if (publishForm) publishForm.action = `/ui/clips/${clipId}/posts`;
      if (titleInput) titleInput.value = button.dataset.title || "";
      if (descriptionInput) descriptionInput.value = button.dataset.description || "";
    });
  });

  const firstClip = studio.querySelector("[data-pick-clip]");
  if (firstClip) firstClip.classList.add("active");
  const firstPlan = studio.querySelector("[data-plan-row]");
  if (firstPlan) selectPlan(firstPlan.dataset.planRow);
  const firstSegment = studio.querySelector("[data-segment-select]");
  if (firstSegment) selectSegment(firstSegment.dataset.segmentSelect);
})();
