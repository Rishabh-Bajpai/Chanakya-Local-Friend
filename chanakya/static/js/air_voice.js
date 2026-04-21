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
    const playbackGapMs = 1200;
    let speechSequenceId = 0;
    let voiceTurnActive = false;

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
        setStatus(models.length ? "AIR models loaded." : "AIR is reachable but returned no models.");
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

    async function stopRecordingSilently() {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        return;
      }
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
      recordButton.textContent = continuousMode ? "Listening" : "Mic";
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
      const scheduleNextChunk = () => {
        nextAudioTimer = window.setTimeout(() => {
          nextAudioTimer = null;
          playNextAudioChunk();
        }, playbackGapMs);
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
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        return;
      }
      if (typeof beforeRecordStart === "function") {
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
      recordButton.dataset.state = "recording";
      recordButton.textContent = "Stop Mic";
      setStatus(continuousMode ? "Listening for your next turn..." : "Recording...");
    }

    async function stopRecordingAndProcess() {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        return;
      }
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
      recordButton.textContent = continuousMode ? "Listening" : "Mic";

      setStatus("Transcribing...");
      const transcript = await transcribeAudio(audioBlob);
      if (!transcript) {
        setStatus("No speech detected.", true);
        return;
      }
      if (typeof onTranscript === "function") {
        onTranscript(transcript);
      }
      const llmModel = selectedValue(llmModelSelect);
      startAssistantSpeechQueue();
      voiceTurnActive = true;
      try {
        const replyText = await submitText(transcript, {
          llmModel,
          voiceMode: true,
          onAssistantMessage: async (assistantMessage) => {
            await speakAssistantMessageAndWait(assistantMessage);
          },
        });
        latestAssistantText = typeof replyText === "string" ? replyText : "";
        if (selectedValue(ttsModelSelect)) {
          await waitForSpeechQueueToFinish();
        }
      } finally {
        voiceTurnActive = false;
      }
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
        continuousButton.textContent = continuousMode ? "Stop Voice" : "Start Voice";
        continuousButton.dataset.state = continuousMode ? "recording" : "idle";
      }
      if (!continuousMode && recordButton.dataset.state !== "recording") {
        recordButton.dataset.state = "idle";
        recordButton.textContent = "Mic";
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
          stopRequested = true;
          continuousMode = false;
          voiceTurnActive = false;
          speechSequenceId += 1;
          stopPlayback();
          await stopRecordingSilently();
          setStatus("Voice mode stopped.");
          syncButtons();
          return;
        }
        stopRequested = false;
        continuousMode = true;
        syncButtons();
        await runSingleVoiceTurn();
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
