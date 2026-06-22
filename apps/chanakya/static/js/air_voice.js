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
    const interruptionListeningWindowMs = 2000;
    const interruptionVoiceThreshold = 0.045;
    const interruptionVoiceFrames = 3;
    const activeRecordingSilenceMs = 3000;
    const activeRecordingPollMs = 120;
    let isBackgroundTab = false;
    let deferredAudioQueue = [];
    let speechSequenceId = 0;
    let voiceTurnActive = false;
    let interruptionStream = null;
    let interruptionAudioContext = null;
    let interruptionAnalyser = null;
    let interruptionSource = null;
    let interruptionListenTimer = null;
    let interruptionMonitorTimer = null;
    let interruptionSpeechRecognition = null;
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
    let playbackAudioCtx = null;
    let playbackSource = null;
    const activeTtsAbortControllers = new Set();

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
      activeTtsAbortControllers.forEach(function(controller) {
        try { controller.abort(); } catch (e) {}
      });
      activeTtsAbortControllers.clear();
      audioQueue.forEach(cleanupChunkUrl);
      audioQueue = [];
      isPlayingQueue = false;
      if (playbackSource) {
        try { playbackSource.stop(); } catch (e) {}
        playbackSource = null;
      }
      if (activeAudio) {
        activeAudio.pause();
        cleanupActiveAudioUrl();
        activeAudio.src = "";
        activeAudio = null;
      }
      deferredAudioQueue.forEach(function(item) {
        URL.revokeObjectURL(item.url);
      });
      deferredAudioQueue = [];
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

    async function teardownInterruptionWindow(keepStream = false) {
      stopInterruptionMonitorTimers();
      if (interruptionSpeechRecognition) {
        try {
          interruptionSpeechRecognition.stop();
        } catch (e) {}
        interruptionSpeechRecognition = null;
      }
      const stream = interruptionStream;
      const context = interruptionAudioContext;
      interruptionStream = null;
      interruptionAudioContext = null;
      interruptionAnalyser = null;
      interruptionSource = null;
      if (stream && !keepStream) {
        stream.getTracks().forEach((track) => track.stop());
      }
      if (!keepStream && mediaRecorder && mediaRecorder.stream === stream) {
        mediaRecorder.stop();
        mediaRecorder = null;
        audioChunks = [];
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
      await teardownInterruptionWindow(result && result.startedRecording);
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
      console.debug("[air-voice] submitTranscript: transcript=%s, isInterruption=%s", transcript ? `'${transcript.substring(0, 50)}...'` : '(empty)', Boolean(metadata?.voice_interruption));
      if (!transcript) {
        setStatus("No speech detected.", true);
        if (metadata && metadata.voice_interruption) {
          window.dispatchEvent(new CustomEvent("air-voice-interruption-empty", {
            detail: { reason: "no_speech" },
          }));
        }
        return "";
      }
      if (typeof onTranscript === "function") {
        onTranscript(transcript, { metadata });
      }
      const llmModel = selectedValue(llmModelSelect);
      startAssistantSpeechQueue();
      voiceTurnActive = true;
      setStatus("Thinking...");
      try {
        console.debug("[air-voice] submitTranscript: calling submitText...");
        const replyText = await submitText(transcript, {
          llmModel,
          voiceMode: true,
          metadata,
          onAssistantMessage: async (assistantMessage) => {
            if (continuousMode) {
              setStatus("Speaking...");
            }
            await speakAssistantMessageAndWait(assistantMessage);
            setStatus(continuousMode ? "Listening for your next turn..." : "");
          },
        });
        console.debug("[air-voice] submitTranscript: submitText complete");
        latestAssistantText = typeof replyText === "string" ? replyText : "";
        if (selectedValue(ttsModelSelect)) {
          await waitForSpeechQueueToFinish();
        }
        return latestAssistantText;
      } finally {
        voiceTurnActive = false;
        if (statusNode && (statusNode.textContent === "Thinking..." || statusNode.textContent === "Speaking...")) {
          setStatus("");
        }
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
      console.debug("[air-voice] autoSubmitCurrentRecording: called (inFlight=%s, hasRecorder=%s, state=%s)",
        recordingSubmitInFlight, Boolean(mediaRecorder), mediaRecorder?.state);
      if (recordingSubmitInFlight || !mediaRecorder || mediaRecorder.state === "inactive") {
        return;
      }
      recordingSubmitInFlight = true;
      try {
        console.debug("[air-voice] autoSubmitCurrentRecording: calling stopRecordingAndProcess...");
        await stopRecordingAndProcess();
        console.debug("[air-voice] autoSubmitCurrentRecording: stopRecordingAndProcess complete, continuing loop...");
        await continueLoopIfNeeded();
        console.debug("[air-voice] autoSubmitCurrentRecording: continueLoopIfNeeded complete");
      } catch (error) {
        console.error("[air-voice] autoSubmitCurrentRecording: error", error);
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
      recordingAudioContext.addEventListener("statechange", function() {
        if (recordingAudioContext && recordingAudioContext.state === "suspended") {
          isBackgroundTab = true;
        }
      });
      recordingSource = recordingAudioContext.createMediaStreamSource(stream);
      recordingAnalyser = recordingAudioContext.createAnalyser();
      recordingAnalyser.fftSize = 2048;
      recordingSource.connect(recordingAnalyser);
      recordingSpeechDetected = pendingInterruptionSubmission;
      recordingLastVoiceAt = pendingInterruptionSubmission ? Date.now() : 0;
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
        reuseStream = false,
      } = options;
      console.debug("[air-voice] beginActiveRecording: reuseStream=%s, hasRecorder=%s, recorderState=%s",
        reuseStream, Boolean(mediaRecorder), mediaRecorder?.state);
      if (!reuseStream && mediaRecorder && mediaRecorder.state !== "inactive") {
        console.debug("[air-voice] beginActiveRecording: skipped (already recording, reuseStream=false)");
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
      let stream;
      if (reuseStream && mediaRecorder && mediaRecorder.state !== "inactive") {
        stream = mediaRecorder.stream;
      } else {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunks.push(event.data);
          }
        };
        mediaRecorder.start(250);
      }
      pendingInterruptionSubmission = interruptionTriggered;
      await startRecordingMonitor(stream);
      recordButton.dataset.state = "recording";
      setButtonLabel(recordButton, "Stop Recording");
      setStatus(interruptionTriggered ? "Recording interruption..." : (continuousMode ? "Listening for your next turn..." : "Recording..."));
    }

    async function triggerInterruptionRecording(token) {
      if (interruptionTriggerInFlight || token !== interruptionWindowToken) {
        console.debug("[air-voice] triggerInterruptionRecording: skipped (inFlight=%s, tokenMatch=%s)", interruptionTriggerInFlight, token === interruptionWindowToken);
        return;
      }
      interruptionTriggerInFlight = true;
      if (interruptionListenTimer) {
        window.clearTimeout(interruptionListenTimer);
        interruptionListenTimer = null;
      }
      console.debug("[air-voice] triggerInterruptionRecording: starting pause...");
      setStatus("Voice activity detected. Pausing assistant...");
      try {
        if (typeof pauseAssistantReplies === "function") {
          await pauseAssistantReplies({ source: "voice_interruption" });
        }
        console.debug("[air-voice] triggerInterruptionRecording: pause complete. token=%s, current=%s", token, interruptionWindowToken);
        if (token !== interruptionWindowToken) {
          console.debug("[air-voice] triggerInterruptionRecording: token mismatch after pause, aborting");
          return;
        }
        console.debug("[air-voice] triggerInterruptionRecording: finishing interruption window...");
        await finishInterruptionWindow({ interrupted: true, startedRecording: true });
        console.debug("[air-voice] triggerInterruptionRecording: beginning active recording (reuseStream)...");
        await beginActiveRecording({
          interruptionTriggered: true,
          skipBeforeRecordStart: true,
          reuseStream: true,
        });
        console.debug("[air-voice] triggerInterruptionRecording: active recording started successfully");
      } catch (error) {
        console.error("[air-voice] triggerInterruptionRecording: error", error);
        await finishInterruptionWindow({ interrupted: true, startedRecording: false, error: error instanceof Error ? error.message : String(error) });
        setStatus(error instanceof Error ? error.message : String(error), true);
      } finally {
        interruptionTriggerInFlight = false;
      }
    }

    async function waitForInterruptionWindow() {
      if (!continuousMode || !voiceTurnActive || stopRequested) {
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
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunks.push(event.data);
          }
        };
        mediaRecorder.start(250);
      }
      interruptionWindowActive = true;
      interruptionTriggerInFlight = false;
      interruptionConsecutiveVoiceFrames = 0;
      setStatus("Listening for interruption...");
      return new Promise(function(resolve) {
        interruptionResolve = resolve;
        interruptionListenTimer = window.setTimeout(async function() {
          interruptionListenTimer = null;
          if (isBackgroundTab) {
            var detected = await checkBackgroundVAD();
            if (detected && !interruptionTriggerInFlight && interruptionWindowActive && token === interruptionWindowToken) {
              void triggerInterruptionRecording(token);
              return;
            }
          }
          void finishInterruptionWindow({ interrupted: false });
        }, interruptionListeningWindowMs);

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
          try {
            interruptionSpeechRecognition = new SpeechRecognition();
            interruptionSpeechRecognition.continuous = true;
            interruptionSpeechRecognition.interimResults = true;
            interruptionSpeechRecognition.onresult = (event) => {
              if (token !== interruptionWindowToken || interruptionTriggerInFlight) {
                return;
              }
              for (let i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i][0].transcript.trim().length > 0) {
                  void triggerInterruptionRecording(token);
                  break;
                }
              }
            };
            interruptionSpeechRecognition.start();
          } catch (e) {
            console.debug("[air-voice] Failed to start Web Speech API for interruption, falling back to RMS", e);
            startRmsMonitor(token);
          }
        } else {
          startRmsMonitor(token);
        }

        function startRmsMonitor(currentToken) {
          interruptionAudioContext = new AudioContext();
          interruptionAudioContext.addEventListener("statechange", function() {
            if (interruptionAudioContext && interruptionAudioContext.state === "suspended") {
              isBackgroundTab = true;
            }
          });
          interruptionSource = interruptionAudioContext.createMediaStreamSource(stream);
          interruptionAnalyser = interruptionAudioContext.createAnalyser();
          interruptionAnalyser.fftSize = 2048;
          interruptionSource.connect(interruptionAnalyser);
          interruptionMonitorTimer = window.setInterval(() => {
            if (currentToken !== interruptionWindowToken || interruptionTriggerInFlight) {
              return;
            }
            const rms = getInterruptionRms();
            if (rms >= interruptionVoiceThreshold) {
              interruptionConsecutiveVoiceFrames += 1;
              if (interruptionConsecutiveVoiceFrames >= interruptionVoiceFrames) {
                void triggerInterruptionRecording(currentToken);
              }
              return;
            }
            interruptionConsecutiveVoiceFrames = 0;
          }, 80);
        }
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

    function mediaSourceMimeType(contentType) {
      const normalized = normalizeAudioContentType(contentType || "audio/mpeg");
      return normalized.split(";")[0].trim() || "audio/mpeg";
    }

    function canUseMediaSourceAudio(contentType) {
      if (!window.MediaSource || typeof MediaSource.isTypeSupported !== "function") {
        return false;
      }
      const mimeType = mediaSourceMimeType(contentType);
      try {
        return MediaSource.isTypeSupported(mimeType);
      } catch (e) {
        return false;
      }
    }

    function cleanupChunkUrl(chunk) {
      if (!chunk || !chunk.url) {
        return;
      }
      try {
        URL.revokeObjectURL(chunk.url);
      } catch (e) {}
      chunk.url = null;
    }

    function cleanupActiveAudioUrl() {
      if (!activeAudio || !activeAudio._airVoiceObjectUrl) {
        return;
      }
      try {
        URL.revokeObjectURL(activeAudio._airVoiceObjectUrl);
      } catch (e) {}
      activeAudio._airVoiceObjectUrl = null;
    }

    function initPlaybackAudioCtx() {
      if (playbackAudioCtx) {
        if (playbackAudioCtx.state === "suspended") {
          playbackAudioCtx.resume().catch(function() {});
        }
        return;
      }
      playbackAudioCtx = new AudioContext();
      playbackAudioCtx.addEventListener("statechange", function() {
        if (playbackAudioCtx && playbackAudioCtx.state === "suspended") {
          isBackgroundTab = true;
        }
      });
      playbackAudioCtx.resume().catch(function() {});
    }

    function cleanupPlaybackAudioCtx() {
      if (playbackSource) {
        try { playbackSource.stop(); } catch (e) {}
        playbackSource = null;
      }
      if (playbackAudioCtx) {
        try { playbackAudioCtx.close(); } catch (e) {}
        playbackAudioCtx = null;
      }
    }

    async function tryAudioCtxPlayback(chunk) {
      try {
        if (!playbackAudioCtx) initPlaybackAudioCtx();
        if (playbackAudioCtx.state === "suspended") {
          playbackAudioCtx.resume().catch(function() {});
        }
        var audioBuffer = await playbackAudioCtx.decodeAudioData(chunk.rawData.slice(0));
        var source = playbackAudioCtx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(playbackAudioCtx.destination);
        playbackSource = source;
        var ended = false;
        await new Promise(function(resolve) {
          source.onended = function() {
            ended = true;
            resolve();
          };
          source.start(0);
          var timeoutMs = Math.max(500, audioBuffer.duration * 1000 + 2000);
          setTimeout(function() {
            if (!ended) resolve();
          }, timeoutMs);
        });
        playbackSource = null;
        return ended ? "ok" : "timeout";
      } catch (e) {
        console.debug("[air-voice] AudioContext playback failed:", e);
        return "error";
      }
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

      var nextChunk = audioQueue[0];
      if (nextChunk.status === "pending") {
        isPlayingQueue = false;
        return;
      }
      if (nextChunk.status === "error") {
        audioQueue.shift();
        playNextAudioChunk();
        return;
      }

      var chunk = audioQueue.shift();
      isPlayingQueue = true;

      if (chunk.rawData) {
        void tryAudioCtxChunk(chunk);
        return;
      }

      playLegacyChunk(chunk);
    }

    async function tryAudioCtxChunk(chunk) {
      var ctxResult = await tryAudioCtxPlayback(chunk);
      if (ctxResult === "ok" || ctxResult === "timeout") {
        if (voiceTurnActive && !stopRequested && selectedValue(sttModelSelect)) {
          var windowResult = await waitForInterruptionWindow();
          if (windowResult && windowResult.interrupted) {
            isPlayingQueue = false;
            return;
          }
        }
        playNextAudioChunk();
        return;
      }
      if (chunk.url) {
        playLegacyChunk(chunk);
      } else {
        playNextAudioChunk();
      }
    }

    function playLegacyChunk(chunk) {
      chunk.playbackStarted = true;
      activeAudio = new Audio(chunk.url);
      activeAudio._airVoiceObjectUrl = chunk.url;
      var scheduleNext = function() {
        if (voiceTurnActive && !stopRequested && selectedValue(sttModelSelect)) {
          waitForInterruptionWindow().then(function(result) {
            if (result && result.interrupted) {
              isPlayingQueue = false;
              return;
            }
            nextAudioTimer = window.setTimeout(function() {
              nextAudioTimer = null;
              playNextAudioChunk();
            }, 0);
          }).catch(function(err) {
            console.debug("Interruption window check failed:", err);
          });
        } else {
          nextAudioTimer = window.setTimeout(function() {
            nextAudioTimer = null;
            playNextAudioChunk();
          }, 0);
        }
      };
      activeAudio.onended = function() {
        cleanupActiveAudioUrl();
        activeAudio = null;
        scheduleNext();
      };
      activeAudio.onerror = function() {
        cleanupActiveAudioUrl();
        activeAudio = null;
        scheduleNext();
      };
      activeAudio.play().catch(function() {
        if (isBackgroundTab) {
          deferredAudioQueue.push({ url: chunk.url });
          activeAudio = null;
          scheduleNext();
          return;
        }
        cleanupActiveAudioUrl();
        activeAudio = null;
        scheduleNext();
      });
    }

    async function waitForMediaSourceOpen(mediaSource) {
      if (mediaSource.readyState === "open") {
        return;
      }
      await new Promise(function(resolve, reject) {
        mediaSource.addEventListener("sourceopen", resolve, { once: true });
        mediaSource.addEventListener("error", reject, { once: true });
      });
    }

    function appendStreamChunk(sourceBuffer, state, chunk) {
      if (state.failed || !chunk || !chunk.byteLength) {
        return;
      }
      state.queue.push(chunk);
      drainMediaSourceQueue(sourceBuffer, state);
    }

    function drainMediaSourceQueue(sourceBuffer, state) {
      if (state.failed || sourceBuffer.updating || !state.queue.length) {
        return;
      }
      try {
        sourceBuffer.appendBuffer(state.queue.shift());
      } catch (error) {
        state.failed = true;
        throw error;
      }
    }

    async function waitForMediaSourceDrain(sourceBuffer, state) {
      while (!state.failed && (sourceBuffer.updating || state.queue.length)) {
        await new Promise(function(resolve) {
          if (!sourceBuffer.updating && !state.queue.length) {
            resolve(null);
            return;
          }
          sourceBuffer.addEventListener("updateend", resolve, { once: true });
        });
      }
    }

    async function synthesizeSpeechChunkStreaming(text, placeholder, model, controller) {
      const response = await fetch(`${baseUrl}/v1/audio/speech`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          model,
          input: text,
          voice: "alloy",
          response_format: "mp3",
          stream: true,
        }),
      });
      if (!response.ok) {
        throw new Error(`TTS failed (${response.status})`);
      }
      if (!response.body) {
        throw new Error("TTS stream body is unavailable");
      }

      const contentType = normalizeAudioContentType(response.headers.get("content-type") || "audio/mpeg");
      if (!canUseMediaSourceAudio(contentType)) {
        throw new Error(`Streaming TTS playback is unsupported for ${contentType}`);
      }

      const mediaSource = new MediaSource();
      const objectUrl = URL.createObjectURL(mediaSource);
      placeholder.url = objectUrl;
      placeholder.streaming = true;

      await waitForMediaSourceOpen(mediaSource);
      if (controller.signal.aborted) {
        throw new DOMException("TTS aborted", "AbortError");
      }

      const sourceBuffer = mediaSource.addSourceBuffer(mediaSourceMimeType(contentType));
      const appendState = { queue: [], failed: false };
      sourceBuffer.mode = "sequence";
      sourceBuffer.addEventListener("updateend", function() {
        try {
          drainMediaSourceQueue(sourceBuffer, appendState);
        } catch (error) {
          console.debug("[air-voice] MediaSource append failed:", error);
        }
      });

      placeholder.status = "ready";
      if (!isPlayingQueue) {
        playNextAudioChunk();
      }

      const reader = response.body.getReader();
      try {
        while (true) {
          const result = await reader.read();
          if (result.done) {
            break;
          }
          appendStreamChunk(sourceBuffer, appendState, result.value);
        }
        await waitForMediaSourceDrain(sourceBuffer, appendState);
        if (mediaSource.readyState === "open") {
          mediaSource.endOfStream();
        }
      } catch (error) {
        if (mediaSource.readyState === "open") {
          try { mediaSource.endOfStream("decode"); } catch (e) {}
        }
        throw error;
      } finally {
        reader.releaseLock();
      }
    }

    async function synthesizeSpeechChunkBlob(text, placeholder, model, controller) {
      const response = await fetch(`${baseUrl}/v1/audio/speech`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
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
      placeholder.rawData = await blob.arrayBuffer();
      placeholder.status = "ready";
      if (!isPlayingQueue) {
        playNextAudioChunk();
      }
    }

    async function synthesizeSpeechChunk(text, placeholder) {
      const model = selectedValue(ttsModelSelect);
      const controller = new AbortController();
      activeTtsAbortControllers.add(controller);
      ttsInFlightCount += 1;
      try {
        try {
          await synthesizeSpeechChunkStreaming(text, placeholder, model, controller);
        } catch (streamError) {
          if (controller.signal.aborted) {
            placeholder.status = "error";
            return;
          }
          if (placeholder.playbackStarted || placeholder.status === "ready") {
            console.debug("[air-voice] Streaming TTS ended after playback started:", streamError);
            return;
          }
          cleanupChunkUrl(placeholder);
          console.debug("[air-voice] Streaming TTS unavailable, falling back to blob playback:", streamError);
          await synthesizeSpeechChunkBlob(text, placeholder, model, controller);
        }
      } catch (error) {
        placeholder.status = "error";
        throw error;
      } finally {
        activeTtsAbortControllers.delete(controller);
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
      if (continuousMode) {
        setStatus("Generating speech...");
      }
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
      const isInterruption = pendingInterruptionSubmission;
      pendingInterruptionSubmission = false;
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
      let transcript = "";
      try {
        transcript = await transcribeAudio(audioBlob);
      } catch (err) {
        console.debug("STT failed (likely silent/empty audio):", err);
      }
      setStatus("");
      await submitTranscript(transcript, {
        metadata: isInterruption
          ? {
            voice_interruption: true,
            input_mode: "voice",
          }
          : {
            input_mode: "voice",
          },
      });
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
      initPlaybackAudioCtx();
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
        initPlaybackAudioCtx();
        if (continuousMode) {
          await stopVoiceMode();
          return;
        }
        await startVoiceMode();
      });
    }

    if (speakButton) {
      speakButton.addEventListener("click", async () => {
        initPlaybackAudioCtx();
        try {
          const text = (typeof getLatestAssistantText === "function" && getLatestAssistantText()) || latestAssistantText;
          await speakText(text);
          setStatus("Assistant reply spoken.");
        } catch (error) {
          setStatus(error instanceof Error ? error.message : String(error), true);
        }
      });
    }

    async function checkBackgroundVAD() {
      var chunks = audioChunks.slice();
      if (chunks.length === 0) {
        return false;
      }
      for (var ci = 0; ci < chunks.length; ci++) {
        try {
          var arrayBuffer = await chunks[ci].arrayBuffer();
          var offlineCtx = new OfflineAudioContext(1, 1, 48000);
          var audioBuffer = await offlineCtx.decodeAudioData(arrayBuffer);
          var data = audioBuffer.getChannelData(0);
          var sum = 0;
          for (var si = 0; si < data.length; si++) {
            sum += data[si] * data[si];
          }
          var rms = Math.sqrt(sum / data.length);
          if (rms >= interruptionVoiceThreshold) {
            return true;
          }
        } catch (e) {
        }
      }
      return false;
    }

    async function flushDeferredAudioQueue() {
      while (deferredAudioQueue.length > 0) {
        var item = deferredAudioQueue.shift();
        if (stopRequested) {
          URL.revokeObjectURL(item.url);
          continue;
        }
        try {
          var audio = new Audio(item.url);
          await new Promise(function(resolve) {
            audio.onended = function() { resolve(); };
            audio.onerror = function() { URL.revokeObjectURL(item.url); resolve(); };
            audio.play().catch(function() { URL.revokeObjectURL(item.url); resolve(); });
          });
        } catch (e) {
          URL.revokeObjectURL(item.url);
        }
      }
    }

    function resumeAudioContexts() {
      if (interruptionAudioContext && interruptionAudioContext.state === "suspended") {
        interruptionAudioContext.resume().catch(function() {});
      }
      if (recordingAudioContext && recordingAudioContext.state === "suspended") {
        recordingAudioContext.resume().catch(function() {});
      }
      if (playbackAudioCtx && playbackAudioCtx.state === "suspended") {
        playbackAudioCtx.resume().catch(function() {});
      }
    }

    document.addEventListener("visibilitychange", function() {
      if (document.hidden) {
        isBackgroundTab = true;
      } else {
        isBackgroundTab = false;
        void flushDeferredAudioQueue();
        resumeAudioContexts();
      }
    });

    return {
      fetchModels,
      startVoiceMode,
      stopVoiceMode,
      isContinuousModeEnabled() {
        return continuousMode;
      },
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
