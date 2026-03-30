(() => {
  const runtime = window.focusRuntime || {};

  const elements = {
    goalInput: document.getElementById("liveGoal"),
    durationInput: document.getElementById("liveDuration"),
    expectedFrames: document.getElementById("expectedFrames"),
    plannedFrames: document.getElementById("plannedFrames"),
    startButton: document.getElementById("startSessionButton"),
    stopButton: document.getElementById("stopSessionButton"),
    message: document.getElementById("liveMessage"),
    sessionStateLabel: document.getElementById("sessionStateLabel"),
    elapsedTime: document.getElementById("elapsedTime"),
    remainingTime: document.getElementById("remainingTime"),
    progressPercent: document.getElementById("progressPercent"),
    sessionProgressBar: document.getElementById("sessionProgressBar"),
    capturePlanLabel: document.getElementById("capturePlanLabel"),
    capturedFrames: document.getElementById("capturedFrames"),
    nextCaptureIn: document.getElementById("nextCaptureIn"),
    latestStatusLabel: document.getElementById("latestStatusLabel"),
    latestScore: document.getElementById("latestScore"),
    latestProcessing: document.getElementById("latestProcessing"),
    latestContext: document.getElementById("latestContext"),
    latestReason: document.getElementById("latestReason"),
    previewLabel: document.getElementById("previewLabel"),
    previewImage: document.getElementById("capturePreview"),
    previewPlaceholder: document.getElementById("previewPlaceholder"),
    captureVideo: document.getElementById("captureVideo"),
    captureCanvas: document.getElementById("captureCanvas"),
    liveReport: document.getElementById("live-report"),
    liveDetails: document.getElementById("live-details"),
    liveReportMeta: document.getElementById("liveReportMeta"),
    liveReportTags: document.getElementById("liveReportTags"),
    liveSummaryGrid: document.getElementById("liveSummaryGrid"),
    liveKeywords: document.getElementById("liveKeywords"),
    liveMetricsGrid: document.getElementById("liveMetricsGrid"),
    liveSuggestions: document.getElementById("liveSuggestions"),
    liveDetailList: document.getElementById("liveDetailList"),
  };

  if (!elements.startButton || !elements.stopButton || !elements.goalInput || !elements.durationInput) {
    return;
  }

  const state = {
    stream: null,
    sessionId: "",
    running: false,
    finishing: false,
    starting: false,
    session: null,
    latestPayload: null,
    captureTimeoutId: 0,
    tickerIntervalId: 0,
    localStartedAt: 0,
    renderedDetailCount: 0,
  };

  const minDuration = Number(runtime.session_min_duration_minutes || 1);
  const maxDuration = Number(runtime.session_max_duration_minutes || 180);
  const defaultDuration = Number(runtime.session_default_duration_minutes || 25);

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatClock(totalSeconds) {
    const seconds = Math.max(0, Number(totalSeconds || 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainingSeconds = seconds % 60;
    if (hours > 0) {
      return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
    }
    return `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
  }

  function formatDate(timestampSeconds) {
    if (!timestampSeconds) {
      return "--";
    }
    const date = new Date(timestampSeconds * 1000);
    const parts = [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, "0"),
      String(date.getDate()).padStart(2, "0"),
    ];
    const time = [
      String(date.getHours()).padStart(2, "0"),
      String(date.getMinutes()).padStart(2, "0"),
      String(date.getSeconds()).padStart(2, "0"),
    ].join(":");
    return `${parts.join("-")} ${time}`;
  }

  function parseDurationInput() {
    const rawValue = Number.parseInt(elements.durationInput.value, 10);
    const value = Number.isFinite(rawValue) ? rawValue : defaultDuration;
    const normalized = Math.min(maxDuration, Math.max(minDuration, value));
    elements.durationInput.value = String(normalized);
    return normalized;
  }

  function syncSessionClock(session) {
    if (!session) {
      return;
    }
    const elapsedSeconds = Number(session.elapsed_seconds || 0);
    state.localStartedAt = Date.now() - elapsedSeconds * 1000;
  }

  function updateCaptureModeLabels() {
    parseDurationInput();
    if (elements.expectedFrames) {
      elements.expectedFrames.textContent = "连续采集";
    }
    if (elements.plannedFrames && (!state.session || !state.running)) {
      elements.plannedFrames.textContent = "连续采集";
    }
    if (elements.nextCaptureIn && (!state.session || !state.running)) {
      elements.nextCaptureIn.textContent = "分析完成后继续";
    }
  }

  function setMessage(type, text) {
    if (!elements.message) {
      return;
    }
    if (!text) {
      elements.message.classList.add("hidden");
      elements.message.textContent = "";
      elements.message.classList.remove("error", "info", "success");
      return;
    }
    elements.message.textContent = text;
    elements.message.classList.remove("hidden", "error", "info", "success");
    elements.message.classList.add(type);
  }

  function setControls(running) {
    const locked = running || state.starting || state.finishing;
    elements.startButton.disabled = locked;
    elements.stopButton.disabled = !running || state.finishing;
    elements.goalInput.disabled = locked;
    elements.durationInput.disabled = locked;
  }

  function clearPreview() {
    elements.previewLabel.textContent = "尚未采集";
    elements.previewImage.removeAttribute("src");
    elements.previewImage.classList.add("hidden");
    elements.previewPlaceholder.classList.remove("hidden");
  }

  function resetReportSections() {
    state.renderedDetailCount = 0;
    elements.liveReport.classList.add("hidden");
    elements.liveDetails.classList.add("hidden");
    elements.liveReportMeta.textContent = "开始后，系统会随着截图不断更新总览结果。";
    elements.liveReportTags.innerHTML = "";
    elements.liveSummaryGrid.innerHTML = "";
    elements.liveKeywords.innerHTML = '<span class="tag">等待任务线索</span>';
    elements.liveMetricsGrid.innerHTML = "";
    elements.liveSuggestions.innerHTML = "<li>开始采集后，这里会逐步出现系统给出的建议。</li>";
    elements.liveDetailList.innerHTML = '<article class="detail-card"><p class="detail-reason">开始采集后，这里会逐张累积本轮专注记录。</p></article>';
  }

  function resetLiveStatus() {
    elements.sessionStateLabel.textContent = "尚未开始";
    elements.elapsedTime.textContent = "00:00";
    elements.remainingTime.textContent = "--:--";
    elements.progressPercent.textContent = "0%";
    elements.sessionProgressBar.style.width = "0%";
    elements.capturePlanLabel.textContent = "等待开始";
    elements.capturedFrames.textContent = "0";
    elements.plannedFrames.textContent = "连续采集";
    elements.nextCaptureIn.textContent = "分析完成后继续";
    elements.latestStatusLabel.textContent = "等待第一张截图";
    elements.latestScore.textContent = "--";
    elements.latestProcessing.textContent = "--";
    elements.latestContext.textContent = "--";
    elements.latestReason.textContent = "开始后这里会持续更新最近一张截图的判断结果。";
    clearPreview();
    resetReportSections();
    updateCaptureModeLabels();
  }

  function clearCaptureTimer() {
    if (state.captureTimeoutId) {
      window.clearTimeout(state.captureTimeoutId);
      state.captureTimeoutId = 0;
    }
  }

  function cleanupTimers() {
    clearCaptureTimer();
    if (state.tickerIntervalId) {
      window.clearInterval(state.tickerIntervalId);
      state.tickerIntervalId = 0;
    }
  }

  function releaseStream() {
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
    }
    state.stream = null;
    if (elements.captureVideo) {
      elements.captureVideo.srcObject = null;
    }
  }

  function mapApiError(code) {
    const messages = {
      missing_goal: "请先填写本次专注目标。",
      invalid_duration_minutes: "请输入有效的专注时长。",
      invalid_duration: `专注时长需要在 ${minDuration} 到 ${maxDuration} 分钟之间。`,
      session_not_found: "当前会话不存在，可能已经过期，请重新开始。",
      session_not_running: "当前会话已经结束，请重新开始。",
      missing_screenshot: "当前没有成功截取到画面，请重新共享工作窗口。",
      unsupported_file_type: "当前截图格式不受支持，请重新开始共享。",
    };
    return messages[code] || "操作失败，请稍后重试。";
  }

  function mapShareError(error) {
    const errorName = error && typeof error === "object" ? error.name : "";
    const messages = {
      AbortError: "屏幕共享被中断了，请重新开始一次。",
      InvalidStateError: "浏览器当前无法开始屏幕共享，请关闭其他共享后重试。",
      NotAllowedError: "你取消了屏幕共享授权，请重新点击开始并选择要共享的窗口。",
      NotFoundError: "没有找到可共享的窗口或屏幕，请检查浏览器权限。",
      NotReadableError: "当前共享源暂时无法读取，请关闭占用它的应用后重试。",
      OverconstrainedError: "浏览器无法满足当前采集条件，请稍后再试。",
      SecurityError: "当前环境不允许使用屏幕共享，请确认是在受支持的浏览器环境中打开。",
      TypeError: "当前浏览器不支持屏幕共享。请在最新版 Chrome 或 Edge 中打开。",
    };
    return messages[errorName] || "无法开始屏幕共享，请稍后重试。";
  }

  async function parseJson(response) {
    try {
      return await response.json();
    } catch {
      return {};
    }
  }

  function updateRuntimeClock() {
    if (!state.session) {
      return;
    }

    const baseElapsed = Number(state.session.elapsed_seconds || 0);
    const durationSeconds = Number(state.session.duration_seconds || 0);
    const elapsedSeconds = state.running
      ? Math.min(durationSeconds, Math.max(baseElapsed, Math.floor((Date.now() - state.localStartedAt) / 1000)))
      : baseElapsed;
    const remainingSeconds = Math.max(0, durationSeconds - elapsedSeconds);
    const progress = durationSeconds > 0 ? Math.min(100, (elapsedSeconds / durationSeconds) * 100) : 0;

    elements.elapsedTime.textContent = formatClock(elapsedSeconds);
    elements.remainingTime.textContent = formatClock(remainingSeconds);
    elements.progressPercent.textContent = `${progress.toFixed(0)}%`;
    elements.sessionProgressBar.style.width = `${progress.toFixed(1)}%`;

    if (state.running) {
      if (!state.firstFrameReady) {
        elements.nextCaptureIn.textContent = "等待共享首帧";
      } else if (state.captureInFlight) {
        elements.nextCaptureIn.textContent = "OCR 与评分中";
      } else {
        elements.nextCaptureIn.textContent = state.finishing ? "正在收尾" : "分析完成后继续";
      }
      if (remainingSeconds <= 0 && !state.finishing) {
        completeSession("finished");
      }
      return;
    }

    elements.nextCaptureIn.textContent = state.session.status === "completed" ? "已结束" : "分析完成后继续";
  }

  function startTicker() {
    if (state.tickerIntervalId) {
      window.clearInterval(state.tickerIntervalId);
      state.tickerIntervalId = 0;
    }
    updateRuntimeClock();
    if (state.running) {
      state.tickerIntervalId = window.setInterval(updateRuntimeClock, 1000);
    }
  }

  function hasUsableVideoFrame() {
    if (!elements.captureVideo) {
      return false;
    }
    return (
      elements.captureVideo.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
      && elements.captureVideo.videoWidth > 0
      && elements.captureVideo.videoHeight > 0
    );
  }

  async function playProbeVideo() {
    if (!elements.captureVideo) {
      return;
    }
    try {
      const result = elements.captureVideo.play();
      if (result && typeof result.then === "function") {
        await result;
      }
    } catch {
      // Hidden capture probes may reject play(); metadata events can still arrive.
    }
  }

  async function waitForVideoFrame(timeoutMs = 8000) {
    if (!elements.captureVideo) {
      throw new Error("未找到共享画面。请刷新页面后重试。");
    }

    await playProbeVideo();
    if (hasUsableVideoFrame()) {
      state.firstFrameReady = true;
      return;
    }

    await new Promise((resolve, reject) => {
      const events = ["loadedmetadata", "loadeddata", "canplay", "playing", "resize"];
      let settled = false;
      let pollingId = 0;
      let timeoutId = 0;
      let frameCallbackId = 0;

      function cleanup() {
        events.forEach((eventName) => {
          elements.captureVideo.removeEventListener(eventName, handleReady);
        });
        if (pollingId) {
          window.clearInterval(pollingId);
        }
        if (frameCallbackId && typeof elements.captureVideo.cancelVideoFrameCallback === "function") {
          elements.captureVideo.cancelVideoFrameCallback(frameCallbackId);
        }
        if (timeoutId) {
          window.clearTimeout(timeoutId);
        }
      }

      function finish() {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        state.firstFrameReady = true;
        resolve();
      }

      function handleReady() {
        if (hasUsableVideoFrame()) {
          finish();
        }
      }

      timeoutId = window.setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        reject(new Error("共享画面的第一帧一直没有准备好，当前更像是截屏链路卡住了。请优先共享具体窗口或浏览器标签页后重试。"));
      }, timeoutMs);

      events.forEach((eventName) => {
        elements.captureVideo.addEventListener(eventName, handleReady);
      });
      pollingId = window.setInterval(handleReady, 180);
      if (typeof elements.captureVideo.requestVideoFrameCallback === "function") {
        frameCallbackId = elements.captureVideo.requestVideoFrameCallback(handleReady);
      }
      handleReady();
    });

    await new Promise((resolve) => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(resolve);
      });
    });
  }

  function canvasToBlob() {
    return new Promise((resolve, reject) => {
      elements.captureCanvas.toBlob((blob) => {
        if (blob) {
          resolve(blob);
          return;
        }
        reject(new Error("未能成功生成截图文件。"));
      }, "image/jpeg", 0.86);
    });
  }

  function updatePreviewFromCanvas(filename) {
    elements.previewImage.src = elements.captureCanvas.toDataURL("image/jpeg", 0.72);
    elements.previewImage.classList.remove("hidden");
    elements.previewPlaceholder.classList.add("hidden");
    elements.previewLabel.textContent = filename;
  }

  async function captureFromTrack(filename) {
    const [track] = state.stream?.getVideoTracks?.() || [];
    if (!track || typeof window.ImageCapture !== "function") {
      return null;
    }

    try {
      const imageCapture = new window.ImageCapture(track);
      const bitmap = await Promise.race([
        imageCapture.grabFrame(),
        new Promise((_, reject) => {
          window.setTimeout(() => {
            reject(new Error("image_capture_timeout"));
          }, 1200);
        }),
      ]);
      const context = elements.captureCanvas.getContext("2d");
      if (!context || !bitmap.width || !bitmap.height) {
        bitmap.close?.();
        return null;
      }

      const maxEdge = 1440;
      const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
      const targetWidth = Math.max(1, Math.round(bitmap.width * scale));
      const targetHeight = Math.max(1, Math.round(bitmap.height * scale));
      elements.captureCanvas.width = targetWidth;
      elements.captureCanvas.height = targetHeight;
      context.drawImage(bitmap, 0, 0, targetWidth, targetHeight);
      bitmap.close?.();
      updatePreviewFromCanvas(filename);
      state.firstFrameReady = true;
      return await canvasToBlob();
    } catch {
      return null;
    }
  }

  async function captureBlob(filename) {
    const trackBlob = await captureFromTrack(filename);
    if (trackBlob) {
      return trackBlob;
    }

    await waitForVideoFrame();

    const width = elements.captureVideo.videoWidth;
    const height = elements.captureVideo.videoHeight;
    if (!width || !height) {
      throw new Error("共享画面尺寸无效，无法截图。当前更像是截屏链路还没有拿到第一帧。请重新选择具体窗口或标签页后再试。");
    }

    const maxEdge = 1440;
    const scale = Math.min(1, maxEdge / Math.max(width, height));
    const targetWidth = Math.max(1, Math.round(width * scale));
    const targetHeight = Math.max(1, Math.round(height * scale));
    const context = elements.captureCanvas.getContext("2d");

    if (!context) {
      throw new Error("当前浏览器无法生成截图，请更换浏览器后重试。");
    }

    elements.captureCanvas.width = targetWidth;
    elements.captureCanvas.height = targetHeight;
    context.drawImage(elements.captureVideo, 0, 0, targetWidth, targetHeight);
    updatePreviewFromCanvas(filename);
    return await canvasToBlob();
  }

  function statusClass(status) {
    if (status === "专注") {
      return "focus";
    }
    if (status === "轻微偏离") {
      return "warn";
    }
    return "danger";
  }

  function renderSummary(summary) {
    if (!summary) {
      elements.liveSummaryGrid.innerHTML = "";
      return;
    }

    const cards = [
      {
        className: "summary-card score-card",
        label: "平均专注状态",
        value: summary.avg_focus_score ?? "--",
        description: "分数越高，说明这一段记录越稳定地围绕当前目标展开。",
        meter: summary.avg_focus_score ?? 0,
      },
      {
        className: "summary-card",
        label: "专注占比",
        value: `${summary.focus_ratio ?? 0}%`,
        description: `专注 ${summary.focus_count ?? 0} 张，轻微偏离 ${summary.drift_count ?? 0} 张，分心 ${summary.distract_count ?? 0} 张。`,
      },
      {
        className: "summary-card value-card",
        label: "最常见的投入场景",
        value: summary.top_context || "--",
        description: "这通常代表你更容易进入状态的内容类型或页面氛围。",
      },
      {
        className: "summary-card value-card",
        label: "最需要注意的偏离场景",
        value: summary.top_distractor || "--",
        description: "先把这类偏离场景梳理清楚，整体表现通常会更稳定。",
      },
      {
        className: "summary-card",
        label: "和目标的贴合度",
        value: summary.avg_relevance_score ?? "--",
        description: "贴合度越高，说明截图内容越接近你一开始写下的任务。",
      },
      {
        className: "summary-card",
        label: "画面识别清晰度",
        value: summary.avg_ocr_quality ?? "--",
        description: "截图越清晰，报告越适合拿来复盘细节和定位问题。",
      },
    ];

    elements.liveSummaryGrid.innerHTML = cards
      .map((card) => `
        <article class="${card.className}">
          <span>${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <p>${escapeHtml(card.description)}</p>
          ${typeof card.meter === "number" ? `<div class="meter"><span style="width: ${Math.max(0, Math.min(100, Number(card.meter) || 0))}%"></span></div>` : ""}
        </article>
      `)
      .join("");
  }

  function renderKeywordBlock(summary) {
    const keywords = Array.isArray(summary?.keywords) ? summary.keywords : [];
    const suggestions = Array.isArray(summary?.suggestions) ? summary.suggestions : [];
    const metrics = [
      { label: "目标贴合", value: summary?.avg_relevance_score ?? "--" },
      { label: "画面清晰", value: summary?.avg_ocr_quality ?? "--" },
      { label: "重点命中", value: summary?.avg_strong_hit_score ?? "--" },
      { label: "页面结构", value: summary?.avg_structure_score ?? "--" },
    ];

    elements.liveKeywords.innerHTML = keywords.length
      ? keywords.map((keyword) => `<span class="tag">${escapeHtml(keyword)}</span>`).join("")
      : '<span class="tag">等待任务线索</span>';

    elements.liveMetricsGrid.innerHTML = metrics
      .map((metric) => `
        <div>
          <span>${escapeHtml(metric.label)}</span>
          <strong>${escapeHtml(metric.value)}</strong>
        </div>
      `)
      .join("");

    elements.liveSuggestions.innerHTML = suggestions.length
      ? suggestions.map((suggestion) => `<li>${escapeHtml(suggestion)}</li>`).join("")
      : "<li>开始采集后，这里会逐步出现系统给出的建议。</li>";
  }

  function renderDetailCard(item) {
    const keywords = Array.isArray(item.matched_keywords) ? item.matched_keywords : [];
    const flags = [];
    if (item.cache_hit) {
      flags.push('<span class="tag">结果复用</span>');
    }
    if (item.used_fallback) {
      flags.push('<span class="tag">备用识别</span>');
    }
    return `
      <article class="detail-card" data-detail-index="${escapeHtml(item.index ?? "")}">
        <div class="detail-top">
          <div>
            <h3>${escapeHtml(item.filename || "截图")}</h3>
            <p>${escapeHtml(item.category_label || "待确认场景")} · ${escapeHtml(item.status || "未分析")}</p>
          </div>
          <div class="score-badge ${statusClass(item.status)}">${escapeHtml(item.focus_score ?? "--")}</div>
        </div>
        <div class="detail-metrics">
          <span>贴合度 ${escapeHtml(item.relevance_score ?? "--")}</span>
          <span>清晰度 ${escapeHtml(item.ocr_quality_score ?? "--")}</span>
          <span>重点命中 ${escapeHtml(item.strong_hit_score ?? "--")}</span>
          <span>关键词 ${escapeHtml(item.keyword_hit_score ?? "--")}</span>
          <span>结构信号 ${escapeHtml(item.structure_score ?? "--")}</span>
          <span>连续稳定 ${escapeHtml(item.window_consistency_score ?? "--")}</span>
        </div>
        ${flags.length ? `<div class="detail-flags">${flags.join("")}</div>` : ""}
        <p class="detail-reason">${escapeHtml(item.decision_reason || "等待分析说明")}</p>
        ${item.fallback_reason ? `<p class="detail-reason subtle">${escapeHtml(item.fallback_reason)}</p>` : ""}
        ${keywords.length ? `<div class="tag-list compact">${keywords.map((keyword) => `<span class="tag">${escapeHtml(keyword)}</span>`).join("")}</div>` : ""}
        <details>
          <summary>查看识别文本</summary>
          <pre>${escapeHtml(item.ocr_text || "当前图片没有提取到足够清晰的文本内容。")}</pre>
        </details>
      </article>
    `;
  }

  function renderDetailList(items) {
    if (!Array.isArray(items) || !items.length) {
      state.renderedDetailCount = 0;
      elements.liveDetailList.innerHTML = '<article class="detail-card"><p class="detail-reason">开始采集后，这里会逐张累积本轮专注记录。</p></article>';
      elements.liveDetails.classList.add("hidden");
      return;
    }

    elements.liveDetails.classList.remove("hidden");

    if (state.renderedDetailCount > items.length) {
      state.renderedDetailCount = 0;
    }

    if (state.renderedDetailCount === 0) {
      elements.liveDetailList.innerHTML = items.map((item) => renderDetailCard(item)).join("");
      state.renderedDetailCount = items.length;
      return;
    }

    const newItems = items.slice(state.renderedDetailCount);
    if (!newItems.length) {
      return;
    }

    elements.liveDetailList.insertAdjacentHTML(
      "beforeend",
      newItems.map((item) => renderDetailCard(item)).join(""),
    );
    state.renderedDetailCount = items.length;
  }

  function renderSessionPayload(payload) {
    if (!payload || !payload.session) {
      return;
    }

    state.latestPayload = payload;
    state.session = payload.session;
    state.sessionId = payload.session.session_id || state.sessionId;
    syncSessionClock(payload.session);

    const latestItem = Array.isArray(payload.items) && payload.items.length ? payload.items[payload.items.length - 1] : null;
    const summary = payload.summary || {};
    const isCompleted = payload.session.status === "completed";
    const statusLabel = isCompleted ? "报告已完成" : "正在记录屏幕";
    const captureLabel = isCompleted ? "本轮采集已结束" : state.captureInFlight ? "最新截图已发出，正在 OCR 与评分" : "上一张分析完成后立即继续采集";

    elements.sessionStateLabel.textContent = statusLabel;
    elements.capturePlanLabel.textContent = captureLabel;
    elements.capturedFrames.textContent = String(payload.session.captured_frames || 0);
    elements.plannedFrames.textContent = isCompleted ? "已结束" : "连续采集";
    updateRuntimeClock();

    if (latestItem) {
      elements.latestStatusLabel.textContent = latestItem.status || "已收到新截图";
      elements.latestScore.textContent = latestItem.focus_score != null ? String(latestItem.focus_score) : "--";
      elements.latestProcessing.textContent = latestItem.processing_ms ? `${latestItem.processing_ms} ms` : "--";
      elements.latestContext.textContent = latestItem.category_label || "待确认场景";
      elements.latestReason.textContent = latestItem.decision_reason || "系统正在整理最新截图的判断说明。";
      elements.previewLabel.textContent = latestItem.filename || "最新截图";
    }

    if ((payload.session.captured_frames || 0) > 0) {
      elements.liveReport.classList.remove("hidden");
      renderSummary(summary);
      renderKeywordBlock(summary);
      renderDetailList(payload.items || []);

      const statusText = isCompleted ? "本轮实时报告已完成" : "正在边采集边更新报告";
      elements.liveReportMeta.textContent = `${statusText} · 目标：“${payload.goal}” · 已采集 ${payload.session.captured_frames} 张 · 开始于 ${formatDate(payload.session.created_at)}`;
      elements.liveReportTags.innerHTML = [
        `<span class="tag">专注占比 ${escapeHtml(summary.focus_ratio ?? 0)}%</span>`,
        `<span class="tag">平均专注分 ${escapeHtml(summary.avg_focus_score ?? "--")}</span>`,
        `<span class="tag">总处理耗时 ${escapeHtml(summary.processing_ms ?? 0)} ms</span>`,
      ].join("");
    }
  }

  async function requestScreenStream() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      throw new Error("当前浏览器不支持屏幕共享。请在最新版 Chrome、Edge 或支持该能力的浏览器中打开。");
    }

    try {
      return await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: 6, max: 12 } },
        audio: false,
      });
    } catch (error) {
      throw new Error(mapShareError(error));
    }
  }

  async function attachStream(stream) {
    state.stream = stream;
    state.firstFrameReady = false;
    elements.captureVideo.muted = true;
    elements.captureVideo.playsInline = true;
    elements.captureVideo.srcObject = stream;
    await playProbeVideo();
    const [track] = stream.getVideoTracks();
    if (track) {
      track.addEventListener(
        "ended",
        () => {
          if (state.running && !state.finishing) {
            completeSession("screen-ended");
          }
        },
        { once: true },
      );
    }
  }

  function queueNextCapture(delayMs = 0, retry = false) {
    clearCaptureTimer();
    if (!state.running || state.finishing) {
      updateRuntimeClock();
      return;
    }
    state.captureInFlight = false;
    elements.nextCaptureIn.textContent = retry ? "准备重试" : "分析完成后继续";
    state.captureTimeoutId = window.setTimeout(() => {
      captureAndUpload();
    }, delayMs);
  }

  async function captureAndUpload() {
    if (!state.running || state.finishing || !state.stream || !state.sessionId) {
      return;
    }

    const nextIndex = Number(state.session?.captured_frames || 0) + 1;
    const filename = `capture-${String(nextIndex).padStart(3, "0")}.jpg`;

    try {
      state.captureInFlight = true;
      elements.previewLabel.textContent = filename;
      elements.latestStatusLabel.textContent = "正在分析最新截图";
      elements.latestScore.textContent = "处理中";
      elements.latestProcessing.textContent = "处理中";
      elements.latestContext.textContent = state.firstFrameReady ? "截图已生成" : "等待首帧";
      elements.latestReason.textContent = state.firstFrameReady
        ? "截图已经生成，正在进行 OCR 与评分。首张截图通常需要约 8 到 15 秒，请稍等。"
        : "共享已经建立，系统正在等待浏览器准备第一帧画面。";
      elements.capturePlanLabel.textContent = "最新截图已发出，正在 OCR 与评分";
      updateRuntimeClock();

      const blob = await captureBlob(filename);
      const formData = new FormData();
      formData.append("screenshot", blob, filename);

      const response = await fetch(`/api/session/${state.sessionId}/frame`, {
        method: "POST",
        body: formData,
      });
      const payload = await parseJson(response);
      if (!response.ok) {
        if (payload.error === "session_not_running") {
          state.captureInFlight = false;
          await completeSession("finished");
          return;
        }
        throw new Error(mapApiError(payload.error));
      }

      state.captureInFlight = false;
      renderSessionPayload(payload);
      if (payload.session.status === "completed" || Number(payload.session.remaining_seconds || 0) <= 0) {
        await completeSession("finished", true);
        return;
      }

      if (state.running && !state.finishing) {
        queueNextCapture();
      }
    } catch (error) {
      state.captureInFlight = false;
      if (!state.running || state.finishing) {
        return;
      }
      setMessage("error", error instanceof Error ? error.message : "截图上传失败，系统会在下一轮继续尝试。");
      elements.latestStatusLabel.textContent = "等待重新采集";
      elements.latestReason.textContent = error instanceof Error ? error.message : "截图上传失败，系统会在下一轮继续尝试。";
      queueNextCapture(1200, true);
    }
  }

  async function startSession() {
    if (state.running || state.starting) {
      return;
    }

    const goal = elements.goalInput.value.trim();
    if (!goal) {
      setMessage("error", "请先填写本次专注目标。");
      return;
    }

    const durationMinutes = parseDurationInput();
    state.starting = true;
    state.finishing = false;
    state.captureInFlight = false;
    state.firstFrameReady = false;
    state.session = null;
    state.latestPayload = null;
    state.sessionId = "";
    cleanupTimers();
    releaseStream();
    resetLiveStatus();
    setControls(false);
    setMessage("info", "浏览器即将请求屏幕共享权限，请选择你要工作的窗口或屏幕。建议优先共享具体窗口或浏览器标签页，这样首帧通常更快。");

    let stream = null;
    try {
      stream = await requestScreenStream();
      const response = await fetch("/api/session/start", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          goal,
          duration_minutes: durationMinutes,
        }),
      });
      const payload = await parseJson(response);
      if (!response.ok) {
        throw new Error(mapApiError(payload.error));
      }

      await attachStream(stream);
      state.running = true;
      renderSessionPayload(payload);
      setControls(true);
      startTicker();
      setMessage("info", "实时专注记录已经开始。系统正在抓取第一张截图；首张图完成 OCR 与评分通常需要约 8 到 15 秒。");
      elements.latestStatusLabel.textContent = "等待第一张截图";
      elements.latestReason.textContent = "共享已经建立，正在等待浏览器准备第一帧画面。";
      await captureAndUpload();
    } catch (error) {
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
      releaseStream();
      cleanupTimers();
      state.running = false;
      state.captureInFlight = false;
    state.firstFrameReady = false;
    state.session = null;
      state.latestPayload = null;
      state.sessionId = "";
      resetLiveStatus();
      setMessage("error", error instanceof Error ? error.message : "启动实时会话失败，请稍后重试。");
    } finally {
      state.starting = false;
      setControls(state.running);
    }
  }

  async function completeSession(reason = "manual", alreadyComplete = false) {
    if ((!state.sessionId && !state.latestPayload) || state.finishing) {
      return;
    }

    const activeSessionId = state.sessionId;
    state.finishing = true;
    state.running = false;
    cleanupTimers();

    let payload = state.latestPayload;
    try {
      if (!alreadyComplete && activeSessionId) {
        const response = await fetch(`/api/session/${activeSessionId}/complete`, {
          method: "POST",
        });
        const result = await parseJson(response);
        if (response.ok) {
          payload = result;
        } else if (!payload) {
          throw new Error(mapApiError(result.error));
        }
      }
    } catch (error) {
      if (!payload) {
        setMessage("error", error instanceof Error ? error.message : "结束会话失败，请稍后重试。");
      }
    }

    if (payload) {
      renderSessionPayload(payload);
    }

    releaseStream();
    cleanupTimers();
    state.finishing = false;
    setControls(false);
    startTicker();

    if (payload) {
      const messageMap = {
        manual: "已提前结束本轮记录，当前报告已经整理好。",
        finished: "本轮专注时长已结束，最终报告已经输出。",
        "screen-ended": "屏幕共享已结束，系统已根据已采集截图整理当前报告。",
      };
      setMessage("success", messageMap[reason] || "报告已生成。");
    }
  }

  elements.startButton.addEventListener("click", () => {
    startSession();
  });

  elements.stopButton.addEventListener("click", () => {
    completeSession("manual");
  });

  elements.durationInput.addEventListener("input", () => {
    updateCaptureModeLabels();
  });

  updateCaptureModeLabels();
  resetLiveStatus();
  setControls(false);
})();







