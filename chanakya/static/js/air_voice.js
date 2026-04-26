(() => {
  function createAirVoiceController(options) {
    const {
      baseUrl,
      llmModelSelect,
      sttModelSelect,
      ttsModelSelect,
      recordButton,
      continuousButton,
      speakButton,
      statusNode,
      submitText,
      getLatestAssistantText,
      onTranscript,
      beforeRecordStart,
      pauseAssistantReplies,
    } = options;

    let mediaRecorder = null;
    let audioChunks = [];
    let continuousMode = false;
    let stopRequested = false;
    let isBusy = false;
    let activeAudio = null;
    let latestAssistantText = "";
    let audioQueue = [];
    let isPlayingQueue = false;
    let ttsInFlightCount = 0;
    let spokenAssistantSegments = [];
    let nextAudioTimer = null;
    const interruptionListeningWindowMs = 3000;
    const interruptionVoiceThreshold = 0.045;
    const interruptionVoiceFrames = 3;
    const activeRecordingSilenceMs = 3000;
    const activeRecordingPollMs = 120;
    let speechSequenceId = 0;
    let voiceTurnActive = false;
    let interruptionStream = null;
    let interruptionAudioContext = null;
    let interruptionAnalyser = null;
    let interruptionSource = null;
    let interruptionListenTimer = null;
    let interruptionMonitorTimer = null;
    let interruptionResolve = null;
    let interruptionWindowActive = false;
    let interruptionTriggerInFlight = false;
    let interruptionConsecutiveVoiceFrames = 0;
    let interruptionWindowToken = 0;
    let pendingInterruptionSubmission = false;
    let recordingAudioContext = null;
    let recordingAnalyser = null;
    let recordingSource = null;
    let recordingMonitorTimer = null;
    let recordingSpeechDetected = false;
    let recordingLastVoiceAt = 0;
    let recordingConsecutiveVoiceFrames = 0;
    let recordingSubmitInFlight = false;

    function setStatus(text, isError = false) {
      if (!statusNode) {
        return;
      }
      statusNode.textContent = text || "";
      statusNode.dataset.state = isError ? "error" : "idle";
      window.dispatchEvent(new CustomEvent("air-voice-status", {
        detail: { text: text || "", isError },
      }));
    }

    function setButtonLabel(button, label) {
      if (!button) {
        return;
      }
      const nextLabel = label || "";
      button.setAttribute("aria-label", nextLabel);
      button.setAttribute("title", nextLabel);
      const labelNode = button.querySelector(".control-button-label");
      if (labelNode) {
        labelNode.textContent = nextLabel;
        return;
      }
      button.textContent = nextLabel;
    }

    function selectedValue(select) {
      return select && select.value ? select.value : "";
    }

    function populateSelect(select, models, type) {
      if (!select) {
        return;
      }
      const previous = select.value;
      const filtered = models.filter((model) => (model.provider_type || "llm") === type);
      select.innerHTML = "";
      if (!filtered.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = `No ${type.toUpperCase()} models`;
        select.appendChild(option);
        select.disabled = true;
        return;
      }
      select.disabled = false;
      filtered.forEach((model) => {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = `${model.id} (${model.provider_name || "AIR"})`;
        select.appendChild(option);
      });
      if (previous && filtered.some((model) => model.id === previous)) {
        select.value = previous;
      }
    }

    async function fetchModels() {
      try {
        const response = await fetch(`${baseUrl}/v1/models`);
        if (!response.ok) {
          throw new Error(`Model load failed (${response.status})`);
        }
        const payload = await response.json();
        const models = Array.isArray(payload.data) ? payload.data : [];
        populateSelect(llmModelSelect, models, "llm");
        populateSelect(sttModelSelect, models, "stt");
        populateSelect(ttsModelSelect, models, "tts");
        setStatus(models.length ? "" : "AIR is reachable but returned no models.");
      } catch (error) {
        setStatus(error instanceof Error ? error.message : String(error), true);
      }
    }

    function stopPlayback() {
      if (nextAudioTimer) {
        window.clearTimeout(nextAudioTimer);
        nextAudioTimer = null;
      }
      audioQueue = [];
      isPlayingQueue = false;
      if (activeAudio) {
        activeAudio.pause();
        activeAudio.src = "";
        activeAudio = null;
      }
    }

    function stopInterruptionMonitorTimers() {
      if (interruptionListenTimer) {
        window.clearTimeout(interruptionListenTimer);
        interruptionListenTimer = null;
      }
      if (interruptionMonitorTimer) {
        window.clearInterval(interruptionMonitorTimer);
        interruptionMonitorTimer = null;
      }
    }

    async function teardownInterruptionWindow() {
      stopInterruptionMonitorTimers();
      const stream = interruptionStream;
      const context = interruptionAudioContext;
      interruptionStream = null;
      interruptionAudioContext = null;
      interruptionAnalyser = null;
      interruptionSource = null;
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
      if (context) {
        try {
          await context.close();
        } catch {
        }
      }
    }

    async function finishInterruptionWindow(result) {
      const resolve = interruptionResolve;
      interruptionResolve = null;
      interruptionWindowActive = false;
      interruptionTriggerInFlight = false;
      interruptionConsecutiveVoiceFrames = 0;
      await teardownInterruptionWindow();
      if (typeof resolve === "function") {
        resolve(result);
      }
    }

    async function cancelInterruptionWindow(result = { interrupted: false }) {
      interruptionWindowToken += 1;
      await finishInterruptionWindow(result);
    }

    function getInterruptionRms() {
      return getAnalyserRms(interruptionAnalyser);
    }

    function getAnalyserRms(analyser) {
      if (!analyser) {
        return 0;
      }
      const buffer = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(buffer);
      let sum = 0;
      for (let index = 0; index < buffer.length; index += 1) {
        const normalized = (buffer[index] - 128) / 128;
        sum += normalized * normalized;
      }
      return Math.sqrt(sum / buffer.length);
    }

    async function submitTranscript(transcript, options = {}) {
      const { metadata = null } = options;
      if (!transcript) {
        setStatus("No speech detected.", true);
        return "";
      }
      if (typeof onTranscript === "function") {
        onTranscript(transcript, { metadata });
      }
      const llmModel = selectedValue(llmModelSelect);
      startAssistantSpeechQueue();
      voiceTurnActive = true;
      try {
        const replyText = await submitText(transcript, {
          llmModel,
          voiceMode: true,
          metadata,
          onAssistantMessage: async (assistantMessage) => {
            await speakAssistantMessageAndWait(assistantMessage);
          },
        });
        latestAssistantText = typeof replyText === "string" ? replyText : "";
        if (selectedValue(ttsModelSelect)) {
          await waitForSpeechQueueToFinish();
        }
        return latestAssistantText;
      } finally {
        voiceTurnActive = false;
      }
    }

    function stopRecordingMonitor() {
      if (recordingMonitorTimer) {
        window.clearInterval(recordingMonitorTimer);
        recordingMonitorTimer = null;
      }
    }

    async function teardownRecordingMonitor() {
      stopRecordingMonitor();
      const context = recordingAudioContext;
      recordingAudioContext = null;
      recordingAnalyser = null;
      recordingSource = null;
      recordingSpeechDetected = false;
      recordingLastVoiceAt = 0;
      recordingConsecutiveVoiceFrames = 0;
      if (context) {
        try {
          await context.close();
        } catch {
        }
      }
    }

    function shouldAutoSubmitRecording() {
      return continuousMode || pendingInterruptionSubmission;
    }

    async function autoSubmitCurrentRecording() {
      if (recordingSubmitInFlight || !mediaRecorder || mediaRecorder.state === "inactive") {
        return;
      }
      recordingSubmitInFlight = true;
      try {
        await stopRecordingAndProcess();
        await continueLoopIfNeeded();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : String(error), true);
      } finally {
        recordingSubmitInFlight = false;
      }
    }

    async function startRecordingMonitor(stream) {
      await teardownRecordingMonitor();
      if (!shouldAutoSubmitRecording()) {
        return;
      }
      recordingAudioContext = new AudioContext();
      recordingSource = recordingAudioContext.createMediaStreamSource(stream);
      recordingAnalyser = recordingAudioContext.createAnalyser();
      recordingAnalyser.fftSize = 2048;
      recordingSource.connect(recordingAnalyser);
      recordingSpeechDetected = false;
      recordingLastVoiceAt = 0;
      recordingConsecutiveVoiceFrames = 0;
      recordingMonitorTimer = window.setInterval(() => {
        if (!mediaRecorder || mediaRecorder.state === "inactive" || recordingSubmitInFlight) {
          return;
        }
        const now = Date.now();
        const rms = getAnalyserRms(recordingAnalyser);
        if (rms >= interruptionVoiceThreshold) {
          recordingConsecutiveVoiceFrames += 1;
          if (recordingConsecutiveVoiceFrames >= interruptionVoiceFrames) {
            recordingSpeechDetected = true;
            recordingLastVoiceAt = now;
          }
          return;
        }
        recordingConsecutiveVoiceFrames = 0;
        if (recordingSpeechDetected && recordingLastVoiceAt && now - recordingLastVoiceAt >= activeRecordingSilenceMs) {
          void autoSubmitCurrentRecording();
        }
      }, activeRecordingPollMs);
    }

    async function beginActiveRecording(options = {}) {
      const {
        interruptionTriggered = false,
        skipBeforeRecordStart = false,
      } = options;
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        return;
      }
      if (!skipBeforeRecordStart && typeof beforeRecordStart === "function") {
        await beforeRecordStart({
          isPlaybackActive: Boolean(activeAudio || audioQueue.length || nextAudioTimer),
        });
      }
      if (activeAudio || audioQueue.length || nextAudioTimer) {
        stopPlayback();
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunks = [];
      mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };
      mediaRecorder.start();
      pendingInterruptionSubmission = interruptionTriggered;
      await startRecordingMonitor(stream);
      recordButton.dataset.state = "recording";
      setButtonLabel(recordButton, "Stop Mic");
      setStatus(interruptionTriggered ? "Recording interruption..." : (continuousMode ? "Listening for your next turn..." : "Recording..."));
    }

    async function triggerInterruptionRecording(token) {
      if (interruptionTriggerInFlight || token !== interruptionWindowToken) {
        return;
      }
      interruptionTriggerInFlight = true;
      setStatus("Voice activity detected. Pausing assistant...");
      try {
        if (typeof pauseAssistantReplies === "function") {
          await pauseAssistantReplies({ source: "voice_interruption" });
        }
        if (token !== interruptionWindowToken) {
          return;
        }
        await finishInterruptionWindow({ interrupted: true, startedRecording: false });
        await beginActiveRecording({
          interruptionTriggered: true,
          skipBeforeRecordStart: true,
        });
      } catch (error) {
        await finishInterruptionWindow({ interrupted: true, startedRecording: false, error: error instanceof Error ? error.message : String(error) });
        setStatus(error instanceof Error ? error.message : String(error), true);
      } finally {
        interruptionTriggerInFlight = false;
      }
    }

    async function waitForInterruptionWindow() {
      if (!voiceTurnActive || stopRequested) {
        return { interrupted: false };
      }
      if (!selectedValue(sttModelSelect)) {
        return { interrupted: false };
      }
      await cancelInterruptionWindow({ interrupted: false });
      const token = ++interruptionWindowToken;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (token !== interruptionWindowToken) {
        stream.getTracks().forEach((track) => track.stop());
        return { interrupted: false };
      }
      interruptionStream = stream;
      interruptionWindowActive = true;
      interruptionTriggerInFlight = false;
      interruptionConsecutiveVoiceFrames = 0;
      interruptionAudioContext = new AudioContext();
      interruptionSource = interruptionAudioContext.createMediaStreamSource(stream);
      interruptionAnalyser = interruptionAudioContext.createAnalyser();
      interruptionAnalyser.fftSize = 2048;
      interruptionSource.connect(interruptionAnalyser);
      setStatus("Listening for interruption...");
      return new Promise((resolve) => {
        interruptionResolve = resolve;
        interruptionListenTimer = window.setTimeout(() => {
          interruptionListenTimer = null;
          void finishInterruptionWindow({ interrupted: false });
        }, interruptionListeningWindowMs);
        interruptionMonitorTimer = window.setInterval(() => {
          if (token !== interruptionWindowToken || interruptionTriggerInFlight) {
            return;
          }
          const rms = getInterruptionRms();
          if (rms >= interruptionVoiceThreshold) {
            interruptionConsecutiveVoiceFrames += 1;
            if (interruptionConsecutiveVoiceFrames >= interruptionVoiceFrames) {
              void triggerInterruptionRecording(token);
            }
            return;
          }
          interruptionConsecutiveVoiceFrames = 0;
        }, 80);
      });
    }

    async function stopRecordingSilently() {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        return;
      }
      await teardownRecordingMonitor();
      const recorder = mediaRecorder;
      const stream = recorder.stream;
      await new Promise((resolve) => {
        recorder.onstop = () => resolve(null);
        recorder.stop();
      });
      stream.getTracks().forEach((track) => track.stop());
      mediaRecorder = null;
      audioChunks = [];
      recordButton.dataset.state = "idle";
      setButtonLabel(recordButton, continuousMode ? "Listening" : "Mic");
    }

    function normalizeAudioContentType(contentType) {
      if (!contentType) {
        return "audio/mpeg";
      }
      return contentType.includes("audio/mp3") ? "audio/mpeg" : contentType;
    }

    function playNextAudioChunk() {
      if (nextAudioTimer) {
        window.clearTimeout(nextAudioTimer);
        nextAudioTimer = null;
      }
      if (!audioQueue.length) {
        isPlayingQueue = false;
        activeAudio = null;
        return;
      }

      const nextChunk = audioQueue[0];
      if (nextChunk.status === "pending") {
        isPlayingQueue = false;
        return;
      }
      if (nextChunk.status === "error") {
        audioQueue.shift();
        playNextAudioChunk();
        return;
      }

      isPlayingQueue = true;
      const currentChunk = audioQueue.shift();
      activeAudio = new Audio(currentChunk.url);
      const scheduleNextChunk = async () => {
        if (voiceTurnActive && !stopRequested && selectedValue(sttModelSelect)) {
          try {
            const result = await waitForInterruptionWindow();
            if (result && result.interrupted) {
              return;
            }
          } catch (err) {
            console.debug("Interruption window check failed:", err);
          }
        }
        nextAudioTimer = window.setTimeout(() => {
          nextAudioTimer = null;
          playNextAudioChunk();
        }, 0);
      };
      activeAudio.onended = () => {
        URL.revokeObjectURL(currentChunk.url);
        activeAudio = null;
        scheduleNextChunk();
      };
      activeAudio.onerror = () => {
        URL.revokeObjectURL(currentChunk.url);
        activeAudio = null;
        scheduleNextChunk();
      };
      activeAudio.play().catch(() => {
        URL.revokeObjectURL(currentChunk.url);
        activeAudio = null;
        scheduleNextChunk();
      });
    }

    async function synthesizeSpeechChunk(text, placeholder) {
      const model = selectedValue(ttsModelSelect);
      ttsInFlightCount += 1;
      try {
        const response = await fetch(`${baseUrl}/v1/audio/speech`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model,
            input: text,
            voice: "alloy",
            response_format: "mp3",
            stream: false,
          }),
        });
        if (!response.ok) {
          throw new Error(`TTS failed (${response.status})`);
        }
        const contentType = normalizeAudioContentType(response.headers.get("content-type") || "audio/mpeg");
        const blob = await response.blob();
        if (blob.size < 200) {
          placeholder.status = "error";
          return;
        }
        placeholder.url = URL.createObjectURL(new Blob([blob], { type: contentType }));
        placeholder.status = "ready";
        if (!isPlayingQueue) {
          playNextAudioChunk();
        }
      } catch (error) {
        placeholder.status = "error";
        throw error;
      } finally {
        ttsInFlightCount -= 1;
        if (!isPlayingQueue) {
          playNextAudioChunk();
        }
      }
    }

    async function speakText(text) {
      const model = selectedValue(ttsModelSelect);
      const cleaned = String(text || "").replace(/[*_#`~]/g, "").trim();
      if (!cleaned) {
        setStatus("No assistant reply available for playback.", true);
        return;
      }
      if (!model) {
        setStatus("Select a TTS model first.", true);
        return;
      }
      stopPlayback();
      setStatus("Generating speech...");
      const placeholder = { url: null, status: "pending" };
      audioQueue = [placeholder];
      await Promise.allSettled([synthesizeSpeechChunk(cleaned, placeholder)]);
      await new Promise((resolve) => {
        const poll = () => {
          if (ttsInFlightCount === 0 && !isPlayingQueue && !activeAudio && audioQueue.length === 0) {
            resolve(null);
            return;
          }
          window.setTimeout(poll, 120);
        };
        poll();
      });
    }

    async function speakAssistantMessageAndWait(messageText) {
      const cleaned = String(messageText || "").trim();
      if (!cleaned) {
        return false;
      }
      const sequenceId = ++speechSequenceId;
      await stopRecordingSilently();
      spokenAssistantSegments.push(cleaned);
      latestAssistantText = spokenAssistantSegments.join("\n\n");
      if (!selectedValue(ttsModelSelect)) {
        return true;
      }
      await speakText(cleaned);
      return sequenceId === speechSequenceId;
    }

    function startAssistantSpeechQueue() {
      stopPlayback();
      latestAssistantText = "";
      spokenAssistantSegments = [];
    }

    async function waitForSpeechQueueToFinish() {
      await new Promise((resolve) => {
        const poll = () => {
          if (ttsInFlightCount === 0 && !isPlayingQueue && !activeAudio && audioQueue.length === 0) {
            resolve(null);
            return;
          }
          window.setTimeout(poll, 120);
        };
        poll();
      });
    }

    async function transcribeAudio(audioBlob) {
      const model = selectedValue(sttModelSelect);
      if (!model) {
        throw new Error("Select an STT model first.");
      }
      const formData = new FormData();
      formData.append("model", model);
      formData.append("file", audioBlob, "voice-input.webm");
      const response = await fetch(`${baseUrl}/v1/audio/transcriptions`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(`STT failed (${response.status})`);
      }
      const payload = await response.json();
      return typeof payload.text === "string" ? payload.text.trim() : "";
    }

    async function startRecording() {
      await cancelInterruptionWindow({ interrupted: false });
      await beginActiveRecording();
    }

    async function stopRecordingAndProcess() {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        return;
      }
      await teardownRecordingMonitor();
      const recorder = mediaRecorder;
      const stream = recorder.stream;
      const audioBlob = await new Promise((resolve) => {
        recorder.onstop = () => {
          resolve(new Blob(audioChunks, { type: "audio/webm" }));
        };
        recorder.stop();
      });
      stream.getTracks().forEach((track) => track.stop());
      mediaRecorder = null;
      recordButton.dataset.state = "idle";
      setButtonLabel(recordButton, continuousMode ? "Listening" : "Mic");

      setStatus("Transcribing...");
      const transcript = await transcribeAudio(audioBlob);
      setStatus("");
      await submitTranscript(transcript, {
        metadata: pendingInterruptionSubmission
          ? {
            voice_interruption: true,
            input_mode: "voice",
          }
          : {
            input_mode: "voice",
          },
      });
      pendingInterruptionSubmission = false;
    }

    async function runSingleVoiceTurn() {
      if (isBusy) {
        return;
      }
      try {
        isBusy = true;
        await startRecording();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : String(error), true);
        continuousMode = false;
        syncButtons();
      } finally {
        isBusy = false;
      }
    }

    function syncButtons() {
      if (continuousButton) {
        setButtonLabel(continuousButton, continuousMode ? "Stop Voice" : "Start Voice");
        continuousButton.dataset.state = continuousMode ? "recording" : "idle";
      }
      if (!continuousMode && recordButton.dataset.state !== "recording") {
        recordButton.dataset.state = "idle";
        setButtonLabel(recordButton, "Mic");
      }
    }

    async function continueLoopIfNeeded() {
      if (!continuousMode || stopRequested) {
        stopRequested = false;
        syncButtons();
        return;
      }
      await runSingleVoiceTurn();
    }

    async function stopVoiceMode() {
      stopRequested = true;
      continuousMode = false;
      voiceTurnActive = false;
      speechSequenceId += 1;
      stopPlayback();
      await cancelInterruptionWindow({ interrupted: false });
      await teardownRecordingMonitor();
      await stopRecordingSilently();
      setStatus("");
      syncButtons();
    }

    async function startVoiceMode() {
      if (continuousMode) {
        return;
      }
      stopRequested = false;
      continuousMode = true;
      syncButtons();
      await runSingleVoiceTurn();
    }

    recordButton.addEventListener("click", async () => {
      try {
        if (recordButton.dataset.state === "recording") {
          await stopRecordingAndProcess();
          await continueLoopIfNeeded();
          return;
        }
        continuousMode = false;
        stopRequested = false;
        syncButtons();
        await runSingleVoiceTurn();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : String(error), true);
      }
    });

    if (continuousButton) {
      continuousButton.addEventListener("click", async () => {
        if (continuousMode) {
          await stopVoiceMode();
          return;
        }
        await startVoiceMode();
      });
    }

    if (speakButton) {
      speakButton.addEventListener("click", async () => {
        try {
          const text = (typeof getLatestAssistantText === "function" && getLatestAssistantText()) || latestAssistantText;
          await speakText(text);
          setStatus("Assistant reply spoken.");
        } catch (error) {
          setStatus(error instanceof Error ? error.message : String(error), true);
        }
      });
    }

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        stopPlayback();
      }
    });

    return {
      fetchModels,
      startVoiceMode,
      stopVoiceMode,
      isVoiceModeEnabled() {
        return continuousMode || voiceTurnActive;
      },
      isRecordingActive() {
        return Boolean(mediaRecorder && mediaRecorder.state !== "inactive");
      },
      isInteractionActive() {
        return Boolean(
          (mediaRecorder && mediaRecorder.state !== "inactive")
          || voiceTurnActive
          || interruptionWindowActive
          || activeAudio
          || audioQueue.length
          || isPlayingQueue
          || ttsInFlightCount > 0
          || nextAudioTimer
        );
      },
      stopSpeechAndInvalidate() {
        speechSequenceId += 1;
        stopPlayback();
        void cancelInterruptionWindow({ interrupted: false });
      },
      async speakAssistantMessageAndWait(text) {
        return speakAssistantMessageAndWait(text);
      },
      setLatestAssistantText(text) {
        latestAssistantText = text || "";
      },
    };
  }

  window.createAirVoiceController = createAirVoiceController;
})();
