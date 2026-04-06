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
    } = options;

    let mediaRecorder = null;
    let audioChunks = [];
    let continuousMode = false;
    let stopRequested = false;
    let isBusy = false;
    let activeAudio = null;
    let latestAssistantText = "";

    function setStatus(text, isError = false) {
      if (!statusNode) {
        return;
      }
      statusNode.textContent = text || "";
      statusNode.dataset.state = isError ? "error" : "idle";
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
      if (activeAudio) {
        activeAudio.pause();
        activeAudio.src = "";
        activeAudio = null;
      }
    }

    async function speakText(text) {
      const model = selectedValue(ttsModelSelect);
      if (!text) {
        setStatus("No assistant reply available for playback.", true);
        return;
      }
      if (!model) {
        setStatus("Select a TTS model first.", true);
        return;
      }
      setStatus("Generating speech...");
      const response = await fetch(`${baseUrl}/v1/audio/speech`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model,
          input: text,
          voice: "alloy",
        }),
      });
      if (!response.ok) {
        throw new Error(`TTS failed (${response.status})`);
      }
      const blob = await response.blob();
      stopPlayback();
      activeAudio = new Audio(URL.createObjectURL(blob));
      await activeAudio.play();
      await new Promise((resolve) => {
        if (!activeAudio) {
          resolve(null);
          return;
        }
        activeAudio.onended = () => resolve(null);
        activeAudio.onerror = () => resolve(null);
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
      const replyText = await submitText(transcript, { llmModel });
      latestAssistantText = typeof replyText === "string" ? replyText : "";
      const speechText = (typeof getLatestAssistantText === "function" && getLatestAssistantText()) || latestAssistantText;
      if (speechText && selectedValue(ttsModelSelect)) {
        await speakText(speechText);
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
          stopPlayback();
          if (mediaRecorder && mediaRecorder.state !== "inactive") {
            mediaRecorder.stop();
          }
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
      setLatestAssistantText(text) {
        latestAssistantText = text || "";
      },
    };
  }

  window.createAirVoiceController = createAirVoiceController;
})();
