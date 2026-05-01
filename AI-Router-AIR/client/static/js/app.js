// Models and State
let models = [];
let mediaRecorder;
let audioChunks = [];
// Injected by template, fallback to the default AIR server port
const BASE_URL = window.AIR_SERVER_URL || 'http://localhost:5512';

// DOM Elements
const modelSelect = document.getElementById('model-select');
const streamCheckbox = document.getElementById('stream-checkbox');
const tabs = document.querySelectorAll('.tab-btn');
const tabContents = document.querySelectorAll('.tab-content');

// Chat Elements
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const chatMessages = document.getElementById('chat-messages');

// STT Elements
const recordBtn = document.getElementById('record-btn');
const sttResult = document.getElementById('stt-result');
const recordingStatus = document.getElementById('recording-status');

// TTS Elements
const ttsInput = document.getElementById('tts-input');
const speakBtn = document.getElementById('speak-btn');
const ttsAudio = document.getElementById('tts-audio');

// Initialization
document.addEventListener('DOMContentLoaded', async () => {
    await fetchModels();
    setupTabs();
    setupChat();
    setupSTT();
    setupTTS();
    setupVoiceMode();
});

// Models and State
let allModels = [];
let currentType = 'llm';

// ... (existing code for mediaRecorder, BASE_URL etc)

// Fetch Models
/**
 * Fetches the list of available models from the Server API.
 * The server aggregates models from all configured providers.
 */
async function fetchModels() {
    try {
        const response = await fetch(`${BASE_URL}/v1/models`);
        const data = await response.json();
        allModels = data.data || [];

        updateModelDropdown(currentType);
        updateVoiceModeDropdowns();

    } catch (error) {
        console.error('Error fetching models:', error);
        modelSelect.innerHTML = '<option disabled>Error loading models (Check Server)</option>';
    }
}

function isEmbeddingModel(model) {
    const task = String(model?.task || '').toLowerCase();
    if (task.includes('embedding')) {
        return true;
    }

    const modelId = String(model?.id || '').toLowerCase();
    return /(^|[^a-z0-9])(embed|embedding|embeddings)([^a-z0-9]|$)/.test(modelId);
}

function getModelsForType(type) {
    return allModels.filter(model => {
        const providerType = model.provider_type || 'llm';
        if (providerType !== type) {
            return false;
        }

        if (type === 'llm' && isEmbeddingModel(model)) {
            return false;
        }

        return true;
    });
}

/**
 * Updates the Model Dropdown based on the selected tab type (LLM, STT, TTS).
 * Filters the master list `allModels` to show only relevant models.
 * @param {string} type - 'llm', 'stt', or 'tts'
 */
function updateModelDropdown(type) {
    modelSelect.innerHTML = '<option value="" disabled selected>Select a Model...</option>';

    const filtered = getModelsForType(type);

    if (filtered.length === 0) {
        const option = document.createElement('option');
        option.disabled = true;
        option.textContent = `No ${type.toUpperCase()} models available`;
        modelSelect.appendChild(option);
        return;
    }

    filtered.forEach(model => {
        const option = document.createElement('option');
        option.value = model.id;
        const pName = model.provider_name || 'Unknown';
        option.textContent = `${model.id} (${pName})`;
        option.dataset.type = model.provider_type || 'llm';
        modelSelect.appendChild(option);
    });
}

function getSelectedModel() {
    return modelSelect.value;
}

// Tabs Logic
function setupTabs() {
    tabs.forEach(btn => {
        btn.addEventListener('click', () => {
            // UI Toggle
            tabs.forEach(t => t.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            btn.classList.add('active');

            const tabId = btn.dataset.tab;
            document.getElementById(`tab-${tabId}`).classList.add('active');

            // Filter Logic
            // Map tab ID to model type
            // chat -> llm, stt -> stt, tts -> tts
            if (tabId === 'chat') currentType = 'llm';
            else currentType = tabId;

            updateModelDropdown(currentType);
        });
    });
}

// Chat Logic
let conversationHistory = []; // Store message objects {role, content}
const clearChatBtn = document.getElementById('clear-chat-btn');

/**
 * Initializes Chat functionality.
 * Handles:
 * - Sending messages to /v1/chat/completions
 * - Maintaining conversation history for context
 * - Handling streaming (future) vs non-streaming responses
 * - Markdown rendering of responses
 */
function setupChat() {
    const sendMessage = async () => {
        const text = chatInput.value.trim();
        const modelId = getSelectedModel();
        const isStream = streamCheckbox.checked;

        if (!text) return;
        if (!modelId) {
            alert('Please select a model first!');
            return;
        }

        // Add User Message to History
        conversationHistory.push({ role: 'user', content: text });
        appendMessage('user', text);
        chatInput.value = '';

        const assistantMsgDiv = appendMessage('assistant', '<span class="typing">Thinking...</span>');
        let fullReply = "";

        try {
            const response = await fetch(`${BASE_URL}/v1/chat/completions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model: modelId,
                    messages: conversationHistory,
                    stream: isStream
                })
            });

            if (!response.ok) throw new Error('API Error');

            if (isStream) {
                console.log("Streaming mode enabled");
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = "";
                let hasStarted = false;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) {
                        console.log("Stream reader done");
                        break;
                    }

                    const chunk = decoder.decode(value, { stream: true });
                    // console.log("Received chunk:", chunk);
                    buffer += chunk;

                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        const trimmedLine = line.trim();
                        if (!trimmedLine) continue;

                        // Handle SSE format: data: {...}
                        if (trimmedLine.startsWith('data: ')) {
                            const dataStr = trimmedLine.slice(6).trim();

                            if (dataStr === '[DONE]') {
                                console.log("Stream DONE received");
                                break;
                            }

                            try {
                                const data = JSON.parse(dataStr);
                                const content = data.choices[0]?.delta?.content || "";

                                if (content) {
                                    if (!hasStarted) {
                                        assistantMsgDiv.innerHTML = "";
                                        hasStarted = true;
                                    }
                                    fullReply += content;
                                    assistantMsgDiv.innerHTML = formatMarkdown(fullReply);
                                    chatMessages.scrollTop = chatMessages.scrollHeight;
                                }
                            } catch (e) {
                                // Possibly partial JSON or different format
                                console.warn("Error parsing chunk as JSON:", dataStr);
                            }
                        }
                    }
                }

                // If stream ended but we never cleared thinking (e.g. error in stream)
                if (!hasStarted && !fullReply) {
                    assistantMsgDiv.innerHTML = "<span style='color:orange'>Stream ended without content. Try another model or check provider logs.</span>";
                }
            } else {
                const data = await response.json();
                fullReply = data.choices[0].message.content;
                assistantMsgDiv.innerHTML = formatMarkdown(fullReply);
            }

            // Add Assistant Reply to History
            conversationHistory.push({ role: 'assistant', content: fullReply });
            chatMessages.scrollTop = chatMessages.scrollHeight;

        } catch (error) {
            assistantMsgDiv.innerHTML = `<span style="color:red">Error: ${error.message}</span>`;
        }
    };

    sendBtn.addEventListener('click', sendMessage);

    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Clear Chat Logic
    clearChatBtn.addEventListener('click', () => {
        conversationHistory = [];
        chatMessages.innerHTML = `
            <div class="message system">
                <div class="text">Chat history cleared.</div>
            </div>
        `;
    });
}

function appendMessage(role, htmlContent) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = htmlContent;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
}

function formatMarkdown(text) {
    return text.replace(/\n/g, '<br>');
}

// STT Logic
const sttLanguage = document.getElementById('stt-language');

/**
 * Initializes Speech-to-Text functionality.
 * Handles:
 * - Microphone access (getUserMedia)
 * - MediaRecorder for capturing audio blobs
 * - Uploading audio to /v1/audio/transcriptions
 */
function setupSTT() {
    recordBtn.addEventListener('click', () => {
        // Validation moved to start? Or can we just record?
        // Actually, we need the model when sending.
        // It's better to warn user BEFORE they start recording if possible,
        // OR allow recording but check before sending.
        // Let's check before recording to save time.
        if (!recordBtn.classList.contains('recording')) {
            if (!getSelectedModel()) {
                alert('Please select an STT model first!');
                return;
            }
        }

        if (recordBtn.classList.contains('recording')) {
            stopRecording();
        } else {
            startRecording();
        }
    });

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            mediaRecorder.ondataavailable = (event) => {
                audioChunks.push(event.data);
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
                await sendAudioForTranscription(audioBlob);
            };

            mediaRecorder.start();
            recordBtn.classList.add('recording');
            recordBtn.innerHTML = '<span class="icon">⏹</span> Stop Recording';
            recordingStatus.classList.remove('hidden');
            recordingStatus.textContent = 'Recording...';

        } catch (err) {
            console.error('Error accessing microphone:', err);
            alert('Could not access microphone');
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
            // GUI update happens in onstop -> sendAudio -> finally
            // But immediate feedback:
            recordBtn.classList.remove('recording');
            recordBtn.innerHTML = '<span class="icon">🎙</span> Record Audio';
            recordingStatus.textContent = 'Processing...';
        }
    }

    async function sendAudioForTranscription(blob) {
        sttResult.value = 'Transcribing...';
        const modelId = getSelectedModel();
        const lang = sttLanguage.value;
        const isStream = streamCheckbox.checked;

        const formData = new FormData();
        formData.append('file', blob, 'recording.wav');
        formData.append('model', modelId || 'whisper-1');
        formData.append('stream', isStream);
        if (lang) {
            formData.append('language', lang);
        }

        try {
            const response = await fetch(`${BASE_URL}/v1/audio/transcriptions`, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) throw new Error('Transcription failed');

            const contentType = response.headers.get('content-type') || '';

            if (isStream && contentType.includes('text/event-stream')) {
                // Handle SSE streaming response
                sttResult.value = '';
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let fullText = '';

                let done_reading = false;

                while (!done_reading) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // Keep partial line

                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (!trimmed || !trimmed.startsWith('data: ')) continue;

                        const dataStr = trimmed.slice(6).trim();
                        if (dataStr === '[DONE]') {
                            done_reading = true;
                            break;
                        }

                        try {
                            const data = JSON.parse(dataStr);
                            if (data.text) {
                                fullText += (fullText ? ' ' : '') + data.text;
                                sttResult.value = fullText;
                            }
                        } catch (e) {
                            console.warn('STT stream parse error:', dataStr);
                        }
                    }
                }

                recordingStatus.textContent = 'Done!';
            } else {
                // Standard JSON response
                const data = await response.json();
                sttResult.value = data.text;
                recordingStatus.textContent = 'Done!';
            }

            setTimeout(() => recordingStatus.classList.add('hidden'), 2000);

        } catch (error) {
            console.error(error);
            sttResult.value = `Error: ${error.message}`;
            recordingStatus.textContent = 'Error';
        }
    }
}

// TTS Logic
const ttsVoiceSelect = document.getElementById('tts-voice');

/**
 * Initializes Text-to-Speech functionality.
 * Handles:
 * - Dynamic Voice population from model metadata
 * - Sending text to /v1/audio/speech
 * - Playing back the received audio blob
 */
function setupTTS() {
    // Update voices when model changes
    modelSelect.addEventListener('change', () => {
        if (currentType === 'tts') {
            updateVoiceDropdown();
        }
    });

    speakBtn.addEventListener('click', async () => {
        const text = ttsInput.value.trim();
        const modelId = getSelectedModel();
        const voiceId = ttsVoiceSelect.value;

        if (!text) {
            alert('Please enter some text.');
            return;
        }

        if (!modelId) {
            alert('Please select a TTS model first!');
            return;
        }

        speakBtn.disabled = true;
        speakBtn.textContent = 'Generating...';

        try {
            const response = await fetch(`${BASE_URL}/v1/audio/speech`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model: modelId,
                    input: text,
                    voice: voiceId,
                    response_format: 'mp3',
                    stream: streamCheckbox.checked
                })
            });

            if (!response.ok) throw new Error('TTS failed');

            const contentType = response.headers.get('content-type') || 'audio/mpeg';
            const rawBlob = await response.blob();
            const blob = new Blob([rawBlob], { type: contentType });
            const url = URL.createObjectURL(blob);
            ttsAudio.src = url;
            ttsAudio.play();

        } catch (error) {
            console.error(error);
            alert(`Error: ${error.message}`);
        } finally {
            speakBtn.disabled = false;
            speakBtn.textContent = 'Generate Speech';
        }
    });

    // Initial check (if TTS tab is already active or switched to)
    tabs.forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab === 'tts') {
                updateVoiceDropdown();
            }
        });
    });
}

function updateVoiceDropdown() {
    const modelId = getSelectedModel();
    if (!modelId) return;

    // Find model data
    const model = allModels.find(m => m.id === modelId);
    if (!model) return;

    ttsVoiceSelect.innerHTML = '';

    if (model.voices && Array.isArray(model.voices) && model.voices.length > 0) {
        model.voices.forEach(v => {
            const option = document.createElement('option');
            option.value = v.id;
            option.textContent = `${v.name} (${v.gender}, ${v.language})`;
            ttsVoiceSelect.appendChild(option);
        });
    } else {
        // Fallback default
        const option = document.createElement('option');
        option.value = 'alloy';
        option.textContent = 'Alloy (Default)';
        ttsVoiceSelect.appendChild(option);
    }
}

// Voice Mode Logic
let isVoiceModeActive = false;
let voiceMediaRecorder;
let voiceAudioChunks = [];
let voiceConversationHistory = [];
let isAssistantSpeaking = false; // Tracks if LLM is currently streaming
let audioContext;
let audioQueue = [];
let isPlayingQueue = false;
let ttsInFlightCount = 0; // Tracks active TTS synthesis requests
let activeVoiceStream = null;

const voiceCallBtn = document.getElementById('voice-call-btn');
const voiceStatus = document.getElementById('voice-status');
const voiceTranscript = document.getElementById('voice-transcript');
const voiceContainer = document.querySelector('.voice-mode-container');

// Voice Mode Model Selects
const voiceSttModel = document.getElementById('voice-stt-model');
const voiceLlmModel = document.getElementById('voice-llm-model');
const voiceTtsModel = document.getElementById('voice-tts-model');
const voiceTtsVoice = document.getElementById('voice-tts-voice');

function updateVoiceModeDropdowns() {
    if (!voiceSttModel) return; // Ensure elements exist

    // Populate STT
    voiceSttModel.innerHTML = '';
    const sttModels = getModelsForType('stt');
    if (sttModels.length === 0) {
        const option = document.createElement('option');
        option.disabled = true;
        option.selected = true;
        option.textContent = 'No STT models available';
        voiceSttModel.appendChild(option);
    } else {
        sttModels.forEach(m => {
            const option = document.createElement('option');
            option.value = m.id;
            option.textContent = `${m.id} (${m.provider_name || 'Unknown'})`;
            voiceSttModel.appendChild(option);
        });
    }

    // Populate LLM
    voiceLlmModel.innerHTML = '';
    const llmModels = getModelsForType('llm');
    if (llmModels.length === 0) {
        const option = document.createElement('option');
        option.disabled = true;
        option.selected = true;
        option.textContent = 'No LLM models available';
        voiceLlmModel.appendChild(option);
    } else {
        llmModels.forEach(m => {
            const option = document.createElement('option');
            option.value = m.id;
            option.textContent = `${m.id} (${m.provider_name || 'Unknown'})`;
            voiceLlmModel.appendChild(option);
        });
    }

    // Populate TTS
    voiceTtsModel.innerHTML = '';
    const ttsModels = getModelsForType('tts');
    if (ttsModels.length === 0) {
        const option = document.createElement('option');
        option.disabled = true;
        option.selected = true;
        option.textContent = 'No TTS models available';
        voiceTtsModel.appendChild(option);
    } else {
        ttsModels.forEach(m => {
            const option = document.createElement('option');
            option.value = m.id;
            option.textContent = `${m.id} (${m.provider_name || 'Unknown'})`;
            voiceTtsModel.appendChild(option);
        });
    }

    updateVoiceModeVoiceDropdown();
}

function updateVoiceModeVoiceDropdown() {
    const modelId = voiceTtsModel.value;
    if (!modelId) return;

    const model = allModels.find(m => m.id === modelId);
    if (!model) return;

    voiceTtsVoice.innerHTML = '';

    if (model.voices && Array.isArray(model.voices) && model.voices.length > 0) {
        model.voices.forEach(v => {
            const option = document.createElement('option');
            option.value = v.id;
            option.textContent = `${v.name} (${v.gender}, ${v.language})`;
            voiceTtsVoice.appendChild(option);
        });
    } else {
        const option = document.createElement('option');
        option.value = 'alloy';
        option.textContent = 'Alloy (Default)';
        voiceTtsVoice.appendChild(option);
    }
}

function setupVoiceMode() {
    voiceTtsModel.addEventListener('change', updateVoiceModeVoiceDropdown);

    voiceCallBtn.addEventListener('click', () => {
        if (isVoiceModeActive) {
            stopVoiceMode();
        } else {
            startVoiceMode();
        }
    });

    // Handle tab switch
    tabs.forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab !== 'voice' && isVoiceModeActive) {
                stopVoiceMode();
            }
        });
    });
}

async function startVoiceMode() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        activeVoiceStream = stream;
        audioContext = new (window.AudioContext || window.webkitAudioContext)();

        isVoiceModeActive = true;
        voiceCallBtn.classList.add('in-call');
        voiceCallBtn.innerHTML = '<span class="icon">⏹</span> End Call';
        voiceContainer.classList.add('active-call');
        voiceStatus.textContent = 'Listening...';
        voiceTranscript.innerHTML = ''; // Clear previous transcript

        startVoiceRecording(stream);
    } catch (err) {
        console.error('Voice Mode Auth Error:', err);
        alert('Microphone access required for Voice Mode');
    }
}

async function stopVoiceMode() {
    isVoiceModeActive = false;
    if (voiceMediaRecorder && voiceMediaRecorder.state !== 'inactive') {
        voiceMediaRecorder.stop();
    }

    // Stop all audio tracks
    if (activeVoiceStream) {
        activeVoiceStream.getTracks().forEach(track => {
            track.stop();
        });
        activeVoiceStream = null;
    }

    // Close AudioContext to prevent resource leaks
    if (audioContext && audioContext.state !== 'closed') {
        await audioContext.close();
        audioContext = null;
    }

    voiceCallBtn.classList.remove('in-call');
    voiceCallBtn.innerHTML = '<span class="icon">📞</span> Start Voice Mode';
    voiceContainer.classList.remove('active-call');
    voiceContainer.classList.remove('speaking');
    voiceStatus.textContent = 'Call Ended';

    // Clear audio queue and states
    audioQueue = [];
    isPlayingQueue = false;
    isAssistantSpeaking = false;
    ttsInFlightCount = 0;

    setTimeout(() => {
        if (!isVoiceModeActive) voiceStatus.textContent = 'Ready to Call';
    }, 2000);
}

function startVoiceRecording(stream) {
    if (!isVoiceModeActive) return;

    voiceMediaRecorder = new MediaRecorder(stream);
    voiceAudioChunks = [];

    voiceMediaRecorder.ondataavailable = (e) => voiceAudioChunks.push(e.data);

    voiceMediaRecorder.onstop = async () => {
        if (!isVoiceModeActive) return;

        const blob = new Blob(voiceAudioChunks, { type: 'audio/wav' });
        await processVoiceTurn(blob, stream);
    };

    voiceMediaRecorder.start();

    // Simple silence detection / auto-stop
    // For a real product we'd use VAD (Voice Activity Detection)
    // Here we'll just stop after 4 seconds of recording for the "turn"
    setTimeout(() => {
        if (voiceMediaRecorder && voiceMediaRecorder.state === 'recording') {
            voiceMediaRecorder.stop();
        }
    }, 4000);
}

async function processVoiceTurn(blob, stream) {
    voiceStatus.textContent = 'Processing...';

    try {
        // 1. STT
        const sttModel = voiceSttModel.value || 'whisper-1';
        const sttLang = document.getElementById('voice-stt-lang').value;
        const formData = new FormData();
        formData.append('file', blob, 'turn.wav');
        formData.append('model', sttModel);
        if (sttLang) {
            formData.append('language', sttLang);
        }

        const sttResp = await fetch(`${BASE_URL}/v1/audio/transcriptions`, {
            method: 'POST',
            body: formData
        });
        const sttData = await sttResp.json();
        const userText = sttData.text;

        if (!userText || userText.length < 2) {
            // Nothing said, restart listening
            startVoiceRecording(stream);
            voiceStatus.textContent = 'Listening...';
            return;
        }

        appendVoiceTranscript('user', userText);

        // 2. LLM Streaming & 3. TTS Chunking
        voiceStatus.textContent = 'Thinking...';
        const llmModel = voiceLlmModel.value || 'gpt-4o';
        voiceConversationHistory.push({ role: 'user', content: userText });

        const llmResp = await fetch(`${BASE_URL}/v1/chat/completions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: llmModel,
                messages: voiceConversationHistory,
                stream: true // Enable streaming for low-latency Voice Mode
            })
        });

        if (!llmResp.ok) throw new Error('LLM Error in Voice Mode');

        const reader = llmResp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        // Assistant transcript element
        const assistantMsgDiv = document.createElement('div');
        assistantMsgDiv.className = 'transcript-entry assistant';
        voiceTranscript.appendChild(assistantMsgDiv);

        let fullAssistantText = "";
        let sentenceBuffer = "";

        // Audio Queue Management for this turn
        audioQueue = []; // Reset queue for new turn
        isPlayingQueue = false;
        isAssistantSpeaking = true;
        ttsInFlightCount = 0;

        voiceStatus.textContent = 'Speaking...';
        voiceContainer.classList.add('speaking');

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            buffer += chunk;

            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                const trimmedLine = line.trim();
                if (!trimmedLine || !trimmedLine.startsWith('data: ')) continue;

                const dataStr = trimmedLine.slice(6).trim();
                if (dataStr === '[DONE]') break;

                try {
                    const data = JSON.parse(dataStr);
                    const content = data.choices[0]?.delta?.content || "";

                    if (content) {
                        fullAssistantText += content;
                        sentenceBuffer += content;

                        // Update UI transcript
                        assistantMsgDiv.innerHTML = formatMarkdown(fullAssistantText);
                        voiceTranscript.scrollTop = voiceTranscript.scrollHeight;

                        // Check for sentence completion (basic punctuation heuristic)
                        if (/[.!?](\s|\n|$)/.test(sentenceBuffer)) {
                            let sentence = sentenceBuffer.trim();
                            sentenceBuffer = ""; // Reset for next sentence
                            if (sentence) {
                                // Strip basic markdown for TTS
                                const cleanSentence = sentence.replace(/[*_#`~]/g, "").trim();
                                if (cleanSentence) {
                                    const placeholder = { url: null, status: 'pending', stream: stream };
                                    audioQueue.push(placeholder);
                                    synthesizeSpeechChunk(cleanSentence, stream, placeholder);
                                }
                            }
                        }
                    }
                } catch (e) {
                    console.warn("LLM Stream Parse Error:", dataStr);
                }
            }
        }

        // Flush remaining buffer
        if (sentenceBuffer.trim()) {
            const cleanSentence = sentenceBuffer.trim().replace(/[*_#`~]/g, "").trim();
            if (cleanSentence) {
                const placeholder = { url: null, status: 'pending', stream: stream };
                audioQueue.push(placeholder);
                synthesizeSpeechChunk(cleanSentence, stream, placeholder);
            }
        }

        // Add to history once complete
        voiceConversationHistory.push({ role: 'assistant', content: fullAssistantText });

    } catch (err) {
        console.error('Voice Turn Error:', err);
        voiceStatus.textContent = 'Error occurred';
    } finally {
        isAssistantSpeaking = false;
        checkIfTurnIsFinished();
    }
}

async function synthesizeSpeechChunk(text, stream, placeholder) {
    const ttsModel = voiceTtsModel.value || 'tts-1';
    const ttsVoice = voiceTtsVoice.value || 'alloy';
    ttsInFlightCount++;

    try {
        const ttsResp = await fetch(`${BASE_URL}/v1/audio/speech`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: ttsModel,
                input: text,
                voice: ttsVoice,
                response_format: 'mp3',
                stream: false
            })
        });

        if (!ttsResp.ok) throw new Error('TTS chunk failed');

        let contentType = ttsResp.headers.get('content-type') || 'audio/mpeg';
        if (contentType.includes('audio/mp3')) contentType = 'audio/mpeg';

        const rawBlob = await ttsResp.blob();
        if (rawBlob.size < 200) {
            console.warn('TTS Chunk too small, skipping.');
            placeholder.status = 'error';
            return;
        }

        const audioBlob = new Blob([rawBlob], { type: contentType });
        const audioUrl = URL.createObjectURL(audioBlob);

        placeholder.url = audioUrl;
        placeholder.status = 'ready';

        if (!isPlayingQueue) {
            playNextAudioChunk();
        }

    } catch (err) {
        console.error('TTS Chunk Error:', err);
        placeholder.status = 'error';
    } finally {
        ttsInFlightCount--;
        checkIfTurnIsFinished();
        // Trigger player in case it was waiting for this specific chunk
        if (!isPlayingQueue) playNextAudioChunk();
    }
}

function playNextAudioChunk() {
    if (!isVoiceModeActive || audioQueue.length === 0) {
        isPlayingQueue = false;
        checkIfTurnIsFinished();
        return;
    }

    // Check head of queue
    const nextChunk = audioQueue[0];

    if (nextChunk.status === 'pending') {
        // Wait for it to become ready
        isPlayingQueue = false;
        return;
    }

    if (nextChunk.status === 'error') {
        // Skip failed chunks
        audioQueue.shift();
        playNextAudioChunk();
        return;
    }

    isPlayingQueue = true;
    const currentAudio = audioQueue.shift();
    const audio = new Audio(currentAudio.url);

    audio.onended = () => {
        URL.revokeObjectURL(currentAudio.url);

        if (audioQueue.length > 0) {
            playNextAudioChunk();
        } else {
            isPlayingQueue = false;
            checkIfTurnIsFinished();
        }
    };

    audio.play().catch(err => {
        console.error('Audio play error:', err);
        URL.revokeObjectURL(currentAudio.url);
        playNextAudioChunk();
    });
}

function checkIfTurnIsFinished() {
    if (!isVoiceModeActive || !activeVoiceStream) return;

    // A turn is only finished if:
    // 1. Assistant has stopped streaming text
    // 2. All TTS requests have finished processing
    // 3. The audio queue is empty
    // 4. Nothing is currently playing
    if (!isAssistantSpeaking && ttsInFlightCount === 0 && audioQueue.length === 0 && !isPlayingQueue) {
        voiceContainer.classList.remove('speaking');
        voiceStatus.textContent = 'Listening...';
        startVoiceRecording(activeVoiceStream);
    }
}

function appendVoiceTranscript(role, text) {
    const entry = document.createElement('div');
    entry.className = `transcript-entry ${role}`;
    entry.textContent = text;
    voiceTranscript.appendChild(entry);
    voiceTranscript.scrollTop = voiceTranscript.scrollHeight;
}
