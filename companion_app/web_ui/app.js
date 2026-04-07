(function () {
  const state = {
    app: null,
    activeTab: "inspect",
    pollingHandle: null,
    previewSyncHandle: null,
  };

  const els = {
    projectList: document.getElementById("project-list"),
    projectTitle: document.getElementById("project-title"),
    projectStatusChip: document.getElementById("project-status-chip"),
    projectSubtitle: document.getElementById("project-subtitle"),
    hudSplats: document.getElementById("hud-splats"),
    hudPerformance: document.getElementById("hud-performance"),
    previewTitle: document.getElementById("preview-title"),
    previewHint: document.getElementById("preview-hint"),
    previewEmpty: document.getElementById("preview-empty"),
    previewEmptyTitle: document.getElementById("preview-empty-title"),
    previewEmptyBody: document.getElementById("preview-empty-body"),
    previewFooter: document.getElementById("preview-footer"),
    previewNativeHost: document.getElementById("preview-native-host"),
    statusProgressLabel: document.getElementById("status-progress-label"),
    statusText: document.getElementById("status-text"),
    metricTime: document.getElementById("metric-time"),
    metricLoss: document.getElementById("metric-loss"),
    statusStage: document.getElementById("status-stage"),
    statusMessage: document.getElementById("status-message"),
    propertiesList: document.getElementById("properties-list"),
    exportDescription: document.getElementById("export-description"),
    logsOutput: document.getElementById("logs-output"),
    datasetList: document.getElementById("dataset-list"),
    modalBackdrop: document.getElementById("modal-backdrop"),
    projectNameInput: document.getElementById("project-name-input"),
    sampleProjectButton: document.getElementById("sample-project-button"),
    addPhotosButton: document.getElementById("add-photos-button"),
    startButton: document.getElementById("start-button"),
    restartButton: document.getElementById("restart-button"),
    stopButton: document.getElementById("stop-button"),
    openExportButton: document.getElementById("open-export-button"),
    exportPlyButton: document.getElementById("export-ply-button"),
    exportSketchupButton: document.getElementById("export-sketchup-button"),
  };

  function api() {
    if (!window.pywebview || !window.pywebview.api) {
      throw new Error("pywebview bridge is not ready");
    }
    return window.pywebview.api;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") {
      return "--";
    }
    return new Intl.NumberFormat().format(value);
  }

  function showToast(message, kind = "success") {
    const root = document.getElementById("toast-root");
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    root.appendChild(node);
    window.setTimeout(() => {
      node.remove();
    }, 3400);
  }

  function setLoadingState() {
    els.projectTitle.textContent = "Connecting...";
    els.projectSubtitle.textContent = "Preparing the desktop bridge.";
    els.previewEmptyTitle.textContent = "Loading interface";
    els.previewEmptyBody.textContent = "Connecting the desktop shell to the Gaussian pipeline.";
  }

  function openModal() {
    els.modalBackdrop.classList.remove("hidden");
    els.projectNameInput.focus();
    els.projectNameInput.select();
  }

  function closeModal() {
    els.modalBackdrop.classList.add("hidden");
    els.projectNameInput.value = "";
  }

  function currentProjectId() {
    return state.app?.activeProjectId || null;
  }

  function currentDetail() {
    return state.app?.activeDetail || null;
  }

  function renderProjects() {
    const projects = state.app?.projects || [];
    const activeId = state.app?.activeProjectId;
    if (!projects.length) {
      els.projectList.innerHTML = '<div class="empty-copy">No projects yet. Create one from photos or start with the bundled sample.</div>';
      return;
    }

    els.projectList.innerHTML = projects
      .map((project) => {
        const activeClass = project.id === activeId ? "active" : "";
        const statusClass = `status-${escapeHtml(project.status)}`;
        const stage = project.jobStage ? `<div class="project-meta">${escapeHtml(project.jobStage)}</div>` : "";
        return `
          <button class="project-item ${activeClass}" data-project-id="${escapeHtml(project.id)}">
            <div>
              <h3 class="project-name">${escapeHtml(project.name)}</h3>
              ${stage}
            </div>
            <div class="project-status">
              <span class="status-dot ${statusClass}"></span>
              <span>${escapeHtml(project.status)}</span>
            </div>
          </button>
        `;
      })
      .join("");

    els.projectList.querySelectorAll("[data-project-id]").forEach((button) => {
      button.addEventListener("click", () => refreshState(button.dataset.projectId));
    });
  }

  function renderHeader(detail) {
    if (!detail) {
      els.projectTitle.textContent = "No Project Selected";
      els.projectSubtitle.textContent = "Create a project, add photos, run the pipeline, inspect the result, and export it back to SketchUp.";
      els.projectStatusChip.textContent = "idle";
      els.projectStatusChip.className = "status-chip idle";
      return;
    }

    els.projectTitle.textContent = detail.header.title;
    els.projectSubtitle.textContent = detail.header.subtitle;
    const status = detail.project.status || "idle";
    els.projectStatusChip.textContent = status;
    els.projectStatusChip.className = `status-chip ${status}`;
  }

  function renderPreview(detail) {
    const preview = detail?.preview;
    els.previewTitle.textContent = preview?.title || "Scene Preview (Native Gaussian)";
    els.previewHint.textContent = preview?.hint || "";
    els.previewFooter.textContent = preview?.footer || "";

    if (preview?.hasScene) {
      els.previewEmpty.classList.add("hidden");
    } else {
      els.previewEmpty.classList.remove("hidden");
      els.previewEmptyTitle.textContent = preview?.emptyTitle || "No preview yet";
      els.previewEmptyBody.textContent = preview?.emptyBody || "Run the project to generate a preview.";
    }

    const hud = detail?.hud;
    els.hudSplats.textContent = hud?.splats ? formatNumber(hud.splats) : "0";
    els.hudPerformance.textContent = hud?.performance || "Native Preview";
  }

  function renderInspect(detail) {
    const statusPanel = detail?.statusPanel;
    const propertiesPanel = detail?.propertiesPanel;
    const exportPanel = detail?.exportPanel;

    els.statusProgressLabel.textContent = statusPanel?.progressLabel || "0%";
    els.statusText.textContent = statusPanel?.statusText || "Ready";
    els.metricTime.textContent = statusPanel?.timeTotal || "--";
    els.metricLoss.textContent = statusPanel?.finalLoss || "--";
    els.statusStage.textContent = statusPanel?.stage || "Ready";
    els.statusMessage.textContent = statusPanel?.message || "No active job.";

    const properties = propertiesPanel?.items || [];
    if (!properties.length) {
      els.propertiesList.innerHTML = '<div class="empty-copy">Select or create a project to inspect the workspace.</div>';
    } else {
      els.propertiesList.innerHTML = properties
        .map((item, index) => `
          <div class="property-row">
            <span class="property-label">${escapeHtml(item.label)}</span>
            <div class="property-value">
              <span>${escapeHtml(item.value)}</span>
              ${item.copyable ? `<button class="property-copy" data-copy-index="${index}">Copy Path</button>` : ""}
            </div>
          </div>
        `)
        .join("");
      els.propertiesList.querySelectorAll("[data-copy-index]").forEach((button) => {
        button.addEventListener("click", async () => {
          const item = properties[Number(button.dataset.copyIndex)];
          try {
            await navigator.clipboard.writeText(String(item.value));
            showToast("Path copied.", "success");
          } catch (error) {
            showToast("Could not copy the path.", "error");
          }
        });
      });
    }

    els.exportDescription.textContent = exportPanel?.body || "Run a project first to generate the exported splat package.";
    const canExport = Boolean(exportPanel?.canExport);
    els.openExportButton.disabled = !canExport;
    els.exportPlyButton.disabled = !canExport;
    els.exportSketchupButton.disabled = !canExport;
  }

  function renderConsole(detail) {
    els.logsOutput.textContent = detail?.logs || "";
    els.logsOutput.scrollTop = els.logsOutput.scrollHeight;
  }

  function renderDataset(detail) {
    const photos = detail?.photos || [];
    if (!photos.length) {
      els.datasetList.innerHTML = '<div class="empty-copy">No photos yet. Add a dataset to start training.</div>';
      return;
    }

    els.datasetList.innerHTML = photos
      .map((photo) => `<div class="dataset-item">${escapeHtml(photo)}</div>`)
      .join("");
  }

  function renderButtons(detail) {
    const toolbar = detail?.toolbar || {};
    const hasProject = Boolean(detail?.project?.id);

    els.sampleProjectButton.disabled = !state.app?.sampleAvailable;
    els.addPhotosButton.disabled = !hasProject || !toolbar.canAddPhotos;
    els.startButton.disabled = !hasProject || !toolbar.canTrain;
    els.restartButton.disabled = !hasProject;
    els.stopButton.disabled = !hasProject || !toolbar.canStop;
    els.openExportButton.disabled = !hasProject || !toolbar.canOpenExport;
  }

  function renderTabs() {
    document.querySelectorAll(".tab-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === state.activeTab);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `${state.activeTab}-panel`);
    });
  }

  function render() {
    renderProjects();
    const detail = currentDetail();
    renderHeader(detail);
    renderPreview(detail);
    renderInspect(detail);
    renderConsole(detail);
    renderDataset(detail);
    renderButtons(detail);
    renderTabs();
    queuePreviewSync();
  }

  async function consumeResult(result, { toastOnCancel = false } = {}) {
    if (!result) {
      showToast("The desktop bridge returned an empty result.", "error");
      return;
    }
    if (result.state) {
      state.app = result.state;
      render();
    }
    if (!result.ok) {
      if (!result.cancelled || toastOnCancel) {
        showToast(result.error || "The action could not be completed.", "error");
      }
      return;
    }
    if (result.message) {
      showToast(result.message, "success");
    }
  }

  async function refreshState(projectId) {
    try {
      const result = await api().refresh(projectId || currentProjectId());
      await consumeResult(result);
    } catch (error) {
      showToast(error.message || "Failed to refresh the interface.", "error");
    }
  }

  function queuePreviewSync() {
    if (state.previewSyncHandle) {
      cancelAnimationFrame(state.previewSyncHandle);
    }
    state.previewSyncHandle = requestAnimationFrame(syncPreviewHost);
  }

  async function syncPreviewHost() {
    if (!window.pywebview || !window.pywebview.api) {
      return;
    }
    const detail = currentDetail();
    const rect = els.previewNativeHost.getBoundingClientRect();
    const visible = Boolean(detail?.preview?.hasScene);
    try {
      await api().set_preview_host({
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        visible,
      });
    } catch (error) {
      console.warn("Preview sync failed", error);
    }
  }

  async function handleCreateProject() {
    const name = els.projectNameInput.value.trim();
    if (!name) {
      showToast("Enter a project name first.", "error");
      return;
    }
    closeModal();
    try {
      const result = await api().create_project(name);
      await consumeResult(result);
    } catch (error) {
      showToast(error.message || "Project creation failed.", "error");
    }
  }

  async function handleAction(actionName, ...args) {
    try {
      const result = await api()[actionName](...args);
      await consumeResult(result);
    } catch (error) {
      showToast(error.message || "Action failed.", "error");
    }
  }

  function bindEvents() {
    document.getElementById("new-project-button").addEventListener("click", openModal);
    document.getElementById("modal-cancel-button").addEventListener("click", closeModal);
    document.getElementById("modal-confirm-button").addEventListener("click", handleCreateProject);
    document.getElementById("open-data-folder-button").addEventListener("click", () => handleAction("open_data_folder"));
    els.sampleProjectButton.addEventListener("click", () => handleAction("create_sample_project"));
    els.addPhotosButton.addEventListener("click", () => handleAction("add_photos", currentProjectId()));
    els.startButton.addEventListener("click", () => handleAction("start_job", currentProjectId()));
    els.restartButton.addEventListener("click", () => handleAction("restart_job", currentProjectId()));
    els.stopButton.addEventListener("click", () => handleAction("stop_job", currentProjectId()));
    els.openExportButton.addEventListener("click", () => handleAction("open_export_folder", currentProjectId()));
    els.exportPlyButton.addEventListener("click", () => handleAction("open_export_folder", currentProjectId()));
    els.exportSketchupButton.addEventListener("click", () => handleAction("open_export_folder", currentProjectId()));

    document.querySelectorAll(".tab-button").forEach((button) => {
      button.addEventListener("click", () => {
        state.activeTab = button.dataset.tab;
        renderTabs();
      });
    });

    els.projectNameInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        handleCreateProject();
      }
    });

    window.addEventListener("resize", queuePreviewSync);
  }

  async function bootstrap() {
    try {
      const result = await api().boot();
      await consumeResult(result);
      if (state.pollingHandle) {
        clearInterval(state.pollingHandle);
      }
      state.pollingHandle = window.setInterval(() => {
        refreshState(currentProjectId());
      }, 1200);

      const observer = new ResizeObserver(() => queuePreviewSync());
      observer.observe(els.previewNativeHost);
      observer.observe(document.body);
    } catch (error) {
      showToast(error.message || "The desktop bridge failed to initialize.", "error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setLoadingState();
    bindEvents();
  });

  window.addEventListener("pywebviewready", bootstrap);
})();
