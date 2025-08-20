// === Global Variable Declarations ===
let chatArea, messageInput, sendButton, recordButton, playResponseButton, darkModeButton;
let callModeButton, statusIndicator, toggleChatButton, chatAreaWrapper, animationArea;
let isChatVisible = false;
let manualIsRecording = false;
let isKeywordSpottingActive = false;
let isQuickWakeWordRecording = false;
let audioPlaybackUnlocked = false;
let keywordListenToggleButton; 
let isExplicitlyListeningForKeywords = false; 
let isQuickCommandActive = false;
let isCallModeActive = false;
let botIsPlayingInCall = false;
let callAudioContext;
let callAnalyserNode, callMicSourceNode, callMediaRecorder, callBotAudio, callStream;
let manualMediaRecorder, manualAudioChunks = [];
let quickWakeWordRecorder, quickWakeWordAudioChunks = [], quickWakeWordSilenceTimer;
let keywordSpotter;
let lastBotAudioPlayer = null;
let isSystemBusy = false;
const QUICK_WAKE_WORD_SILENCE_TIMEOUT_MS = 2500;
const MIN_SILENCE_MS = 2500, SPEECH_LVL_THRESHOLD = 10, INTERRUPT_LVL_THRESHOLD = 18;
let orb, orbCore; // For the new animation
let particles1 = [];
let particles2 = [];
let time = 0; // For particle animation
const numParticles1 = 20; // Reduced for potentially smaller area
const numParticles2 = 5;  // Reduced
const particleRadius1 = 4; // Reduced
const particleRadius2 = 8;   // Reduced
let coreRadius = 0; // Will be calculated
let boundaryRadius = 0; // Will be calculated
const offsetAngle = -45 * (Math.PI / 180);
let animationFrameId_particles; // To control particle animation loop


// === ALL FUNCTION DEFINITIONS START HERE ===

// === Particle Animation Functions (Copied and slightly modified) ===
function initializeParticles() {
    if (!orbCore || particles1.length > 0) return; // Already initialized or orbCore not ready

    coreRadius = orbCore.offsetWidth / 2;
    boundaryRadius = coreRadius * 0.8; // Adjusted boundary

    // Clear existing particles if any (e.g., on re-initialization, though we aim for once)
    orbCore.innerHTML = '';
    particles1 = [];
    particles2 = [];

    for (let i = 0; i < numParticles1; i++) {
        const particle = document.createElement('div');
        particle.classList.add('particle');
        particle.style.width = particleRadius1 + 'px';
        particle.style.height = particleRadius1 + 'px';
        orbCore.appendChild(particle);
        particles1.push(particle);
        // Position particles (simplified from your example for brevity, use your original complex positioning)
        const angle = Math.random() * Math.PI * 2;
        const distance = Math.random() * boundaryRadius;
        particle.style.left = (coreRadius + distance * Math.cos(angle) - particleRadius1/2) + 'px';
        particle.style.top = (coreRadius + distance * Math.sin(angle) - particleRadius1/2) + 'px';
    }

    for (let i = 0; i < numParticles2; i++) {
        const particle = document.createElement('div');
        particle.classList.add('particle');
        particle.style.width = particleRadius2 + 'px';
        particle.style.height = particleRadius2 + 'px';
        orbCore.appendChild(particle);
        particles2.push(particle);
        // Position particles
        const angle = Math.random() * Math.PI * 2;
        const distance = Math.random() * boundaryRadius * 0.6;
        particle.style.left = (coreRadius + distance * Math.cos(angle) - particleRadius2/2) + 'px';
        particle.style.top = (coreRadius + distance * Math.sin(angle) - particleRadius2/2) + 'px';
    }

    // Start animation loop only if it's not already running
    if (!animationFrameId_particles) {
        animateParticles();
    }
}

function animateParticlesWithOffset(particlesToAnimate, speedFactor, currentOffsetAngle) { // Renamed variable
    if (!orbCore || !coreRadius) return;
    particlesToAnimate.forEach(particle => {

        let x = parseFloat(particle.style.left) + parseFloat(particle.style.width)/2 - coreRadius; // center of particle relative to core center
        let y = parseFloat(particle.style.top) + parseFloat(particle.style.height)/2 - coreRadius; // center of particle relative to core center

        const currentAngle = Math.atan2(y, x);
        const distance = Math.sqrt(x*x + y*y);

        const dx = Math.cos(currentAngle + currentOffsetAngle) * speedFactor; // Use currentOffsetAngle
        const dy = Math.sin(currentAngle + currentOffsetAngle) * speedFactor;

        let newXCenter = x + dx;
        let newYCenter = y + dy;

        const newDistFromCenter = Math.sqrt(newXCenter*newXCenter + newYCenter*newYCenter);
        if (newDistFromCenter > boundaryRadius) {
            newXCenter = boundaryRadius * Math.cos(currentAngle + currentOffsetAngle);
            newYCenter = boundaryRadius * Math.sin(currentAngle + currentOffsetAngle);
        }
        // Convert back to top-left for style
        particle.style.left = (coreRadius + newXCenter - parseFloat(particle.style.width)/2) + 'px';
        particle.style.top = (coreRadius + newYCenter - parseFloat(particle.style.height)/2) + 'px';
    });
}

function animateParticles() {
    if (!orbCore || !coreRadius) { // Ensure elements are ready
        animationFrameId_particles = requestAnimationFrame(animateParticles);
        return;
    }
    time += 0.05;
    const speedFactor1 = Math.sin(time) * 3 + 4; // Adjusted speeds
    const speedFactor2 = Math.sin(time) * 2 + 1.5;

    // Animate first set
    particles1.forEach(particle => {

        let x = parseFloat(particle.style.left) + parseFloat(particle.style.width)/2;
        let y = parseFloat(particle.style.top) + parseFloat(particle.style.height)/2;

        x += (Math.random() - 0.5) * speedFactor1;
        y += (Math.random() - 0.5) * speedFactor1;

        const distFromCoreCenter = Math.sqrt(Math.pow(x - coreRadius, 2) + Math.pow(y - coreRadius, 2));
        if (distFromCoreCenter > boundaryRadius) {
            const angleToCenter = Math.atan2(y - coreRadius, x - coreRadius);
            x = coreRadius + boundaryRadius * Math.cos(angleToCenter);
            y = coreRadius + boundaryRadius * Math.sin(angleToCenter);
        }
        particle.style.left = (x - parseFloat(particle.style.width)/2) + 'px';
        particle.style.top = (y - parseFloat(particle.style.height)/2) + 'px';
    });

    animateParticlesWithOffset(particles2, speedFactor2, offsetAngle + time * 0.1); // Add time to offset for rotation

    animationFrameId_particles = requestAnimationFrame(animateParticles);
}




// updateStatus function:
function updateStatus(newStatusText) {
    if (statusIndicator) {
        statusIndicator.textContent = newStatusText;
    }

    if (orb) { // Check if orb element exists
        const lowerStatus = newStatusText.toLowerCase().trim();
        let orbStateClass = 'idle'; // Default state

        if (lowerStatus.includes("processing") || 
            lowerStatus.includes("playing") || 
            lowerStatus.includes("bot speaking") || 
            lowerStatus.includes(WAKE_WORD + " speaking") ||
            lowerStatus.includes("sending audio")) {
            orbStateClass = 'speaking';
        } else if (lowerStatus.includes("listening (pause)") ||
                lowerStatus.includes("user speaking") || 
                lowerStatus.includes("speaking...") || 
                lowerStatus.includes("recording") || 
                lowerStatus.includes(WAKE_WORD + " listening for command...")) {
            orbStateClass = 'listening';
        } else if (lowerStatus.includes("listening for wake word") || 
                lowerStatus.includes("ready") ||
                lowerStatus.includes("no command heard") ||
                lowerStatus.includes("mic permission denied") ||
                lowerStatus.includes("keyword spotter error") ||
                lowerStatus.includes("playback stopped") ||
                lowerStatus.includes("fetch audio error")) {
            orbStateClass = 'idle';
        }
        
        orb.className = `orb ${orbStateClass}`; // Update the orb's main state class

        // --- START: Apply/Remove synchronized particle animation ---
        const applySynchronizedAnimation = (particles) => {
            particles.forEach(particle => {
                particle.style.animation = 'moveParticlesSynchronized 5s linear infinite'; // Added alternate for smoother loop
            });
        };

        const removeSynchronizedAnimation = (particles) => {
            particles.forEach(particle => {
                particle.style.animation = 'none';
            });
        };

        if (orbStateClass === 'listening' || orbStateClass === 'speaking') {
            if (particles1.length > 0) applySynchronizedAnimation(particles1);
            if (particles2.length > 0) applySynchronizedAnimation(particles2);
        } else { // idle state
            if (particles1.length > 0) removeSynchronizedAnimation(particles1);
            if (particles2.length > 0) removeSynchronizedAnimation(particles2);
        }
        // --- END: Apply/Remove synchronized particle animation ---
    }
}

function toggleChatAreaVisibility() {
    isChatVisible = !isChatVisible;
    if (isChatVisible) {
        chatAreaWrapper.classList.remove("collapsed");
        chatAreaWrapper.classList.add("expanded");
        animationArea.style.display = "none";
        toggleChatButton.textContent = "🤖";
        toggleChatButton.title = "Hide Chat / Show Animation";
        scrollToBottom();
    } else {
        chatAreaWrapper.classList.add("collapsed");
        chatAreaWrapper.classList.remove("expanded");
        animationArea.style.display = "flex";
        toggleChatButton.textContent = "💬";
        toggleChatButton.title = "Show Chat Messages";
        initializeParticles();
    }
    localStorage.setItem("isChatVisible", isChatVisible);

    // When chat is expanded (animationArea hidden)
    if (animationFrameId_particles) {
        cancelAnimationFrame(animationFrameId_particles);
        animationFrameId_particles = null;
    }

    // When animationArea becomes visible again
    if (!animationFrameId_particles && orb && orbCore) { // Check if orb elements are ready
        animateParticles(); // Restart loop
    }
}

function applyChatVisibilityPreference() {
    const storedVisibility = localStorage.getItem("isChatVisible");
    isChatVisible = storedVisibility === "true";
    if (isChatVisible) {
        isChatVisible = false; 
        toggleChatAreaVisibility();
    } else {
        chatAreaWrapper.classList.add("collapsed");
        animationArea.style.display = "flex";
        if (toggleChatButton) { // Check if element exists
            toggleChatButton.textContent = "💬";
            toggleChatButton.title = "Show Chat Messages";
        }
    }
}

async function unlockAudioPlayback() {
    if (audioPlaybackUnlocked) {
        console.log("Audio playback already unlocked.");
        return true;
    }
    console.log("Attempting to unlock audio playback...");
    let activeAudioContext = null;
    if (typeof callAudioContext !== 'undefined' && callAudioContext && callAudioContext.state === 'running') {
        activeAudioContext = callAudioContext;
    } else if (typeof callAudioContext !== 'undefined' && callAudioContext && callAudioContext.state === 'suspended') {
        try { await callAudioContext.resume(); activeAudioContext = callAudioContext; } catch (e) { console.warn("Failed to resume 'callAudioContext'.", e); }
    }
    let tempContextCreated = false;
    if (!activeAudioContext && window.AudioContext) {
        try {
            activeAudioContext = new (window.AudioContext || window.webkitAudioContext)();
            tempContextCreated = true;
            if (activeAudioContext.state === 'suspended') await activeAudioContext.resume();
        } catch (e) { console.error("Could not create/resume temporary AudioContext.", e); }
    }
    const dummyAudio = new Audio();
    dummyAudio.src = 'data:audio/wav;base64,UklGRjIAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
    try {
        await dummyAudio.play();
        audioPlaybackUnlocked = true;
        console.log("Audio playback unlocked via dummy audio.");
        return true;
    } catch (e) {
        console.warn("Dummy audio play() failed. Playback may be restricted.", e);
        return false;
    }
}

function toggleDarkMode() {
    document.body.classList.toggle("dark-mode");
    if (document.body.classList.contains("dark-mode")) {
        localStorage.setItem("darkMode", "enabled");
        if (darkModeButton) darkModeButton.textContent = "☀️";
    } else {
        localStorage.setItem("darkMode", "disabled");
        if (darkModeButton) darkModeButton.textContent = "🌙";
    }
}

function applyDarkModePreference() {
    // This function is now ONLY called after darkModeButton is assigned in DOMContentLoaded
    if (localStorage.getItem("darkMode") === "enabled") {
        document.body.classList.add("dark-mode");
        if (darkModeButton) {
            darkModeButton.textContent = "☀️";
        }
    } else {
        // If your CSS :root is already dark, this 'else' means the button shows the "go to dark mode" icon
        // if the preference isn't explicitly "enabled".
        if (darkModeButton) {
            darkModeButton.textContent = "🌙";
        }
    }
}

// --- NO standalone applyDarkModePreference(); call here ---
function appendMessage(text, sender, data) {
    if (!chatArea) return;

    const messageContainer = document.createElement("div");
    messageContainer.className = `message-container ${sender}-message-container`;

    const messageElement = document.createElement("pre");
    messageElement.textContent = text;
    messageElement.className = sender === "user" ? "user-message" : "bot-message";
    messageContainer.appendChild(messageElement);

    if (sender === 'bot' && data && data.used_tools && data.used_tools.length > 0) {
        const toolsContainer = document.createElement('div');
        toolsContainer.className = 'tool-tags-container';

        const usedToolsTitle = document.createElement('span');
        usedToolsTitle.className = 'tool-tag-title';
        usedToolsTitle.textContent = 'Tools:';
        toolsContainer.appendChild(usedToolsTitle);

        data.used_tools.forEach((toolName, index) => {
            const toolTag = document.createElement('span');
            toolTag.className = 'tool-tag';
            toolTag.textContent = toolName;
            toolsContainer.appendChild(toolTag);

            if (index < data.used_tools.length - 1) {
                const comma = document.createTextNode(', ');
                toolsContainer.appendChild(comma);
            }
        });
        messageContainer.appendChild(toolsContainer);
    }

    const isScrolledToBottom = chatArea.scrollHeight - chatArea.clientHeight <= chatArea.scrollTop + 1;
    if (chatArea.firstChild) {
        chatArea.insertBefore(messageContainer, chatArea.firstChild);
    } else {
        chatArea.appendChild(messageContainer);
    }

    if (isChatVisible && isScrolledToBottom) {
        scrollToBottom();
    }
}

function scrollToBottom() {
    if (chatArea) {
        setTimeout(() => { chatArea.scrollTop = 0; }, 50);
    }
}

async function sendMessage() {
    await unlockAudioPlayback(); // Good place for unlock
    const message = messageInput.value.trim();
    if (!message || isCallModeActive) return;
    stopKeywordSpotter();
    messageInput.value = "";
    appendMessage(message, "user");
    try {
        const response = await fetch("/chat", {
            method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: "message=" + encodeURIComponent(message)
        });
        const data = await response.json();
        appendMessage(data.error ? `Error: ${data.error}` : data.response, "bot", data);
    } catch (error) {
        console.error("Error sending message:", error);
        appendMessage("Error: Could not connect.", "bot");
    } finally {
        // Only restart keyword spotter if not in call mode and it was the intended state
        if (!isCallModeActive && isExplicitlyListeningForKeywords) { // isExplicitlyListeningForKeywords is your toggle button state
            startKeywordSpotter();
        } else if (!isCallModeActive) {
            updateStatus("Ready."); // Or whatever idle status is appropriate
        }
        // If in call mode, listening is managed by VAD (processCallAudio)
    }
}

function handleKeyPress(event) { if (event.key === "Enter") { event.preventDefault(); sendMessage(); } }

// --- Manual Record Button Functions --- (Ensure updateStatus is used)
async function toggleRecord() {
    await unlockAudioPlayback(); // Good place for unlock
    if (isQuickWakeWordRecording && quickWakeWordRecorder && quickWakeWordRecorder.state === "recording") {
         quickWakeWordRecorder.stop(); // Stop quick command recording if active
    }
    isQuickWakeWordRecording = false;
    if (isCallModeActive) { alert("Manual recording disabled during call."); return; }
    if (manualIsRecording) {
        stopManualRecording();
    } else {
        stopKeywordSpotter();
        await startManualRecording();
    }
}
async function startManualRecording() {
    stopKeywordSpotter();
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        manualIsRecording = true; 
        if(recordButton) recordButton.textContent = "🛑"; 
        updateStatus("Recording...");
        manualAudioChunks = []; 
        manualMediaRecorder = new MediaRecorder(stream);
        manualMediaRecorder.ondataavailable = (event) => manualAudioChunks.push(event.data);
        manualMediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop()); // Stop stream tracks here
            updateStatus("Sending audio...");
            const audioBlob = new Blob(manualAudioChunks, { type: 'audio/wav' });
            const formData = new FormData();
            formData.append('audio', audioBlob, 'manual.wav');

            const voiceFileInput = document.getElementById('voice-file-input');
            const voicePresetSelect = document.getElementById('voice-preset-select');

            if (voiceFileInput.files.length > 0) {
                formData.append('voice_file', voiceFileInput.files[0]);
            } else if (voicePresetSelect.value) {
                formData.append('voice_preset', voicePresetSelect.value);
            }

            try {
                const response = await fetch('/record', { method: 'POST', body: formData });
                const data = await response.json();
                if (data.error) {
                    appendMessage(`Error: ${data.error}`, "bot");
                } else {
                    if (data.transcription) appendMessage(data.transcription, "user");
                    appendMessage(data.response, "bot", data);
                }
            } catch (e) { appendMessage("Error sending audio.", "bot"); console.error(e); }
            if (!isCallModeActive && isExplicitlyListeningForKeywords) {
                startKeywordSpotter();
            } else if (!isCallModeActive) {
                updateStatus("Ready.");
            }
        };
        manualMediaRecorder.start();
    } catch (e) {
        alert("Mic error: " + e.message); 
        manualIsRecording = false; 
        if(recordButton) recordButton.textContent = "🎤"; 
        updateStatus("Ready");
        startKeywordSpotter();
    }
}
function stopManualRecording() {
    if (manualMediaRecorder && manualMediaRecorder.state !== "inactive") {
        manualMediaRecorder.stop(); 
        // Stream tracks are stopped in onstop of startManualRecording or here if not already
        if (manualMediaRecorder.stream && manualMediaRecorder.stream.active) {
             manualMediaRecorder.stream.getTracks().forEach(t => t.stop());
        }
    }
    manualIsRecording = false; 
    if(recordButton) recordButton.textContent = "🎤"; 
    updateStatus("Stopping..."); // Will soon become "Ready" via onstop or next keyword spotter start
    startKeywordSpotter();
}

// --- Play Last Response --- (Ensure updateStatus is used)
async function playResponse() {
console.log("playResponse called. isCallModeActive:", isCallModeActive); // Log 1
await unlockAudioPlayback(); // Ensure this completes
console.log("Audio playback unlocked (or was already)."); // Log 2

if (isCallModeActive) {
    alert("Playback disabled during call.");
    return; // This is an intentional stop
}
if (lastBotAudioPlayer && !lastBotAudioPlayer.paused) {
    lastBotAudioPlayer.pause(); lastBotAudioPlayer.currentTime = 0;
    updateStatus("Playback stopped."); setTimeout(() => updateStatus("Ready"), 1500);
    return; // This is also intentional
}
updateStatus("Fetching audio...");
try {
    const response = await fetch('/play_response', { method: 'POST' });
    console.log("Fetched /play_response"); // Log 3
    const data = await response.json();
    console.log("/play_response data:", data); // Log 4

    if (data.audio_data_url) {
        lastBotAudioPlayer = new Audio(data.audio_data_url);
        updateStatus("Bot speaking...");
        lastBotAudioPlayer.play().catch(e => {
            updateStatus("Playback error.");
            console.error("Error playing last response:", e); // Log 5
        });
        lastBotAudioPlayer.onended = () => updateStatus("Ready");
    } else {
        const errorMsg = data.error || "No audio available to play.";
        updateStatus(errorMsg);
        alert(errorMsg);
        console.warn("/play_response no audio_data_url:", errorMsg); // Log 6
    }
} catch (e) {
    updateStatus("Fetch audio error.");
    alert("Failed to get audio.");
    console.error("Error in playResponse fetch:", e); // Log 7
}
}

// --- Call Mode --- (Ensure updateStatus is used)
function updateCallButtonVisuals() {
    if(callModeButton) callModeButton.textContent = isCallModeActive ? "🛑" : "📞";
    [recordButton, playResponseButton, sendButton, messageInput].forEach(el => { if(el) el.disabled = isCallModeActive; });
    if (!isCallModeActive) updateStatus("Ready");
}

async function toggleCallMode() {
console.log("toggleCallMode called. Current isCallModeActive:", isCallModeActive); // Log A
await unlockAudioPlayback();
console.log("Audio unlocked for toggleCallMode."); // Log B

if (isQuickWakeWordRecording && quickWakeWordRecorder && quickWakeWordRecorder.state === "recording") {
    console.log("Stopping active Quick " + WAKE_WORD + " recording."); // Log C
    quickWakeWordRecorder.stop();
}
isQuickWakeWordRecording = false;

if (isCallModeActive) {
    console.log("toggleCallMode: Attempting to stop call."); // Log D
    await stopCall();
    startKeywordSpotter(); // This should resume general listening
} else {
    console.log("toggleCallMode: Attempting to start call."); // Log E
    if (manualIsRecording) {
        console.log("Stopping manual recording before starting call."); // Log F
        stopManualRecording();
    }
    stopKeywordSpotter(); // Stop general listening before VAD starts
    await startCall(); // This starts VAD
}
}

async function handleByeCommandInCall() {
    // ... (ensure all statusIndicator.textContent are updateStatus())
    if (!isCallModeActive) return;
    appendMessage("Bye", "user"); 
    updateStatus("Saying goodbye...");
    try {
        const serverResponse = await fetch("/chat", {
            method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: "message=" + encodeURIComponent("Bye")
        });
        const data = await serverResponse.json();
        if (data.error) appendMessage(`Bot Error: ${data.error}`, "bot");
        else {
            appendMessage(data.response, "bot");
            if (data.audio_data_url) { // This depends on /chat endpoint providing it for "Bye"
                // For consistency, let's use /play_response to get audio for last_ai_response
                const playDataResponse = await fetch('/play_response', { method: 'POST' });
                const playData = await playDataResponse.json();
                if (playData.audio_data_url) {
                    const byeAudio = new Audio(playData.audio_data_url);
                    updateStatus("Bot speaking..."); // Changed from "Bot saying goodbye..."
                    await new Promise(resolve => {
                        byeAudio.onended = resolve; byeAudio.onerror = resolve; byeAudio.play().catch(resolve);
                    });
                }
            }
        }
    } catch (error) { console.error("Error sending 'Bye' message:", error); appendMessage("Bot Error: Could not say goodbye.", "bot");}
    if (isCallModeActive) await stopCall();
}

async function startCall() {
    if (isCallModeActive) return;
    stopKeywordSpotter(); // Stop idle keyword spotter
    isCallModeActive = true; 
    isSystemBusy = false; // Ensure system is not marked busy at the start of a call
    updateCallButtonVisuals(); 
    updateStatus("Call starting...");
    try {
        callAudioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (callAudioContext.state === 'suspended') await callAudioContext.resume();
        callStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        callMicSourceNode = callAudioContext.createMediaStreamSource(callStream);
        callAnalyserNode = callAudioContext.createAnalyser();
        callAnalyserNode.fftSize = 512; callAnalyserNode.smoothingTimeConstant = 0.6;
        callMicSourceNode.connect(callAnalyserNode);
        callMediaRecorder = new MediaRecorder(callStream, { mimeType: 'audio/webm;codecs=opus' });
        callAudioChunks = [];
        callMediaRecorder.ondataavailable = e => { if (e.data.size > 0) callAudioChunks.push(e.data); };
        callMediaRecorder.onstop = async () => {
            if (!isCallModeActive) {
                console.log("callMediaRecorder.onstop: Call mode is no longer active.");
                callAudioChunks = []; 
                isSystemBusy = false; // Reset busy state
                return;
            }
            if (callAudioChunks.length === 0) {
                console.log("callMediaRecorder.onstop: No audio chunks to send.");
                if (isCallModeActive && !botIsPlayingInCall && !isSystemBusy) {
                    updateStatus("Listening..."); 
                }
                isSystemBusy = false; // Reset busy state
                return;
            }

            isSystemBusy = true; // <<<< SET SYSTEM BUSY HERE
            stopKeywordSpotter(); 
            updateStatus("Sending audio..."); 
            
            const audioBlob = new Blob(callAudioChunks, { type: callMediaRecorder.mimeType });
            callAudioChunks = [];
            const formData = new FormData();
            formData.append('audio', audioBlob, 'call_audio_chunk' + (callMediaRecorder.mimeType.includes('webm') ? '.webm' : '.wav'));

            const voiceFileInput = document.getElementById('voice-file-input');
            const voicePresetSelect = document.getElementById('voice-preset-select');

            if (voiceFileInput.files.length > 0) {
                formData.append('voice_file', voiceFileInput.files[0]);
            } else if (voicePresetSelect.value) {
                formData.append('voice_preset', voicePresetSelect.value);
            }

            let botWillSpeak = false;
            try {
                const resp = await fetch('/record', { method: 'POST', body: formData });
                const data = await resp.json();

                if (!isCallModeActive) { 
                    console.log("callMediaRecorder.onstop: Call mode deactivated during server response.");
                    isSystemBusy = false; // Reset
                    return;
                }

                if (data.error) {
                    appendMessage(`Bot Error: ${data.error}`, "bot");
                    updateStatus("Error processing your speech."); 
                } else {
                    if (data.transcription) appendMessage(data.transcription, "user");
                    appendMessage(data.response, "bot", data);
                    if (data.audio_data_url) {
                        botWillSpeak = true;
                        // playBotInCall will set botIsPlayingInCall and also manage isSystemBusy
                        await playBotInCall(data.audio_data_url);
                    }
                }
            } catch (e) { 
                if(isCallModeActive) { 
                    appendMessage("Bot Error: Network issue sending audio.", "bot");
                    console.error("Error fetching /record in callMediaRecorder.onstop:", e); 
                }
            } finally {
                userSpokeThisTurn = false; 
                if (!botWillSpeak) { // If bot is not going to speak immediately from this flow
                    isSystemBusy = false; // <<<< RESET SYSTEM BUSY if bot is not speaking
                }
                // Status update for listening is handled by processCallAudio or end of playBotInCall
                if (isCallModeActive && !botIsPlayingInCall && !isSystemBusy) { 
                    updateStatus("Listening..."); 
                }
            }
        };
        userIsSpeaking = false; userSpokeThisTurn = false; silenceStartTime = 0; botIsPlayingInCall = false; isSystemBusy = false;
        updateStatus("Listening...");
        processCallAudio(); // Start VAD loop
        if (isExplicitlyListeningForKeywords) {
            console.log("Call started. Starting keyword spotter for 'bye' detection.");
            startKeywordSpotter({ forInCallByeDetection: true });
        }
    } catch (e) { console.error("Error in startCall:", e); await stopCall(); /*startKeywordSpotter(); Don't auto start, respect toggle */ }
}

async function stopCall() {
    if (!isCallModeActive && !keywordSpotter) { 
         if (callAnimFrameId) cancelAnimationFrame(callAnimFrameId);
         return; 
    }
    const wasTrulyActive = isCallModeActive;
    isCallModeActive = false; 
    isSystemBusy = false; // <<<< RESET SYSTEM BUSY
    updateStatus("Call ending...");

    if (callAnimFrameId) cancelAnimationFrame(callAnimFrameId); callAnimFrameId = null;
    if (callMediaRecorder && callMediaRecorder.state === "recording") {
        callMediaRecorder.onstop = null; // Important to prevent onstop from firing again
        callMediaRecorder.stop();
    }
    callMediaRecorder = null;
    if (callBotAudio && !callBotAudio.paused) { callBotAudio.pause(); callBotAudio.src = ""; }
    callBotAudio = null; botIsPlayingInCall = false;
    if (callMicSourceNode) callMicSourceNode.disconnect();
    if (callStream) callStream.getTracks().forEach(t => t.stop()); callStream = null;
    
    callAudioChunks = []; userIsSpeaking = false; userSpokeThisTurn = false;
    
    updateCallButtonVisuals(); // This might set status to "Ready"
    
    // Only restart keyword spotter if user explicitly had it on
    if (isExplicitlyListeningForKeywords) { // If user wants idle listening
        startKeywordSpotter(); // Start normal idle spotter
    } else {
        updateStatus("Call ended. Ready.");
    }
}

function processCallAudio() {
    if (!isCallModeActive || !callAnalyserNode) { 
        callAnimFrameId = null; 
        return; 
    }

    // <<<< ADD CHECK FOR isSystemBusy >>>>
    if (botIsPlayingInCall || isSystemBusy) { 
        if(isCallModeActive) callAnimFrameId = requestAnimationFrame(processCallAudio); 
        else callAnimFrameId = null;
        return; // Don't process mic input if bot is playing OR system is busy (e.g., sending audio)
    }

    const data = new Uint8Array(callAnalyserNode.frequencyBinCount);
    callAnalyserNode.getByteTimeDomainData(data);
    let sum = 0; data.forEach(val => sum += Math.abs(val - 128));
    const avgAmp = sum / data.length;

    if (avgAmp > SPEECH_LVL_THRESHOLD) {
        if (!userIsSpeaking) {
            userIsSpeaking = true; userSpokeThisTurn = true; updateStatus("Speaking...");
            if (callMediaRecorder?.state === "inactive") { 
                callAudioChunks = []; 
                callMediaRecorder.start(); 
            }
        }
        silenceStartTime = 0;
    } else { // Below speech threshold (silence)
        if (userIsSpeaking) { // Was just speaking, now silence starts
            userIsSpeaking = false; 
            silenceStartTime = Date.now(); 
            updateStatus("Listening (pause)...");
        }
        if (silenceStartTime > 0 && (Date.now() - silenceStartTime > MIN_SILENCE_MS)) {
            if (userSpokeThisTurn && callMediaRecorder?.state === "recording") {
                // This is where the VAD decides to stop recording and process the chunk
                console.log("VAD: Silence timeout, stopping recorder.");
                callMediaRecorder.stop(); // This will trigger callMediaRecorder.onstop
                // isSystemBusy will be set to true inside onstop
                updateStatus("Processing speech..."); // Indicates VAD finished, onstop will take over
            }
            silenceStartTime = 0; // Reset silence timer
            // userSpokeThisTurn is reset in onstop's finally block
        }
    }
    
    if(isCallModeActive) {
        callAnimFrameId = requestAnimationFrame(processCallAudio); 
    } else {
        callAnimFrameId = null; 
    }
}

async function playBotInCall(audioUrl) {
    if (!isCallModeActive) {
        console.log("playBotInCall: Call mode not active, aborting playback.");
        // No promise to return if we abort early, or could return a pre-resolved/rejected one
        return Promise.resolve({ interrupted: false, error: "Call not active" }); 
    }
    if (!audioUrl) { 
        if (isCallModeActive) updateStatus("Listening..."); 
        isSystemBusy = false; 
        console.log("playBotInCall: No audio URL provided.");
        return Promise.resolve({ interrupted: false, error: "No audio URL" });
    }

    // Return a new promise that resolves/rejects when playback is complete or an error occurs
    return new Promise(async (resolve, reject) => { 
        let promiseFinalized = false; // Local flag for this specific promise instance

        botIsPlayingInCall = true; 
        isSystemBusy = true; 
        updateStatus("Bot speaking...");
        
        stopKeywordSpotter(); // Stop main idle/bye spotter
        let bargeInSpotter = null; // Initialize to null
        let bargeInActive = false;
        
        // Initialize and start barge-in spotter
        bargeInSpotter = initializeKeywordSpotter(); 
        if (bargeInSpotter) {
            try {
                // Barge-in specific onresult: only concerned with "stop"
                bargeInSpotter.onresult = (event) => { 
                    let transcript = "";
                    for (let i = event.resultIndex; i < event.results.length; ++i) {
                        transcript += event.results[i][0].transcript;
                    }
                    if (transcript.toLowerCase().includes("stop")) {
                        console.log("Keyword: 'stop' (barge-in) detected during bot playback!");
                        if (callBotAudio && !callBotAudio.paused) {
                            callBotAudio.pause(); // Triggers pauseHandler which calls cleanupAndFinalize
                        }
                    }
                };
                // Simplified event handlers for barge-in spotter
                bargeInSpotter.onstart = () => { bargeInActive = true; console.log("Barge-in spotter started.");};
                bargeInSpotter.onend = () => { bargeInActive = false; console.log("Barge-in spotter ended."); }; 
                bargeInSpotter.onerror = (e) => { console.error("Barge-in spotter error:", e); bargeInActive = false; };
                
                if(!isKeywordSpottingActive && !bargeInActive) { // Avoid starting if main spotter is somehow active or this one is
                   bargeInSpotter.start();
                } else {
                    console.warn("Could not start barge-in spotter, another spotter might be active or it failed previously.");
                    bargeInSpotter = null; 
                }
            } catch (e) { 
                console.error("Could not start barge-in spotter:", e); 
                bargeInSpotter = null; 
            }
        }

        // Cleanup previous bot audio if any (safer to always create a new Audio object)
        if (callBotAudio) { 
            callBotAudio.pause(); 
            callBotAudio.removeAttribute('src'); 
            // Remove any lingering event listeners from the *previous* callBotAudio instance
            // This requires storing references to handlers if they are not anonymous.
            // For simplicity, we rely on creating a new Audio object which won't have old listeners.
            callBotAudio.load(); 
        }
        callBotAudio = new Audio(audioUrl); // Create new Audio object for this playback
        
        // --- Define cleanup and promise finalization logic ---
        const cleanupAndFinalize = (interrupted = false, error = null) => {
            if (promiseFinalized) return; // Execute only once
            promiseFinalized = true;

            botIsPlayingInCall = false;
            isSystemBusy = false; 

            // Stop and nullify the barge-in spotter for this playback instance
            if (bargeInSpotter && bargeInActive) {
                try { bargeInSpotter.stop(); } catch(e) { console.warn("Error stopping barge-in spotter", e); }
            }
            bargeInSpotter = null; 
            bargeInActive = false;

            // Remove event listeners from the current callBotAudio instance
            callBotAudio.removeEventListener('ended', endedHandler);
            callBotAudio.removeEventListener('error', errorHandler);
            callBotAudio.removeEventListener('pause', pauseHandler); 

            if (isCallModeActive) { 
                if (interrupted) {
                    updateStatus("Interrupted. Listening..."); 
                    userIsSpeaking = false; 
                    userSpokeThisTurn = false;
                    silenceStartTime = 0;
                } else if (!error) { // Finished normally, not interrupted, no error
                    updateStatus("Listening..."); 
                } else { // An error occurred during playback
                    updateStatus("Bot audio error. Listening...");
                }
                // Conditionally restart the main keyword spotter for "bye" detection
                if (isExplicitlyListeningForKeywords && !isKeywordSpottingActive) { 
                    console.log("Bot playback finished/interrupted in call. Attempting to start 'bye' spotter.");
                    startKeywordSpotter({ forInCallByeDetection: true }); 
                }
            } else { // Call ended during or just after bot speech
                if (isExplicitlyListeningForKeywords && !isKeywordSpottingActive) {
                    startKeywordSpotter(); // Start normal idle spotter
                } else if (!isKeywordSpottingActive) { 
                    updateStatus("Ready.");
                    if (keywordListenToggleButton) keywordListenToggleButton.textContent = "👂";
                }
            }

            if (error) {
                reject(error);
            } else {
                resolve({ interrupted }); // Resolve with interruption status
            }
        };

        // --- Define event handlers for this specific Audio object ---
        const endedHandler = () => {
            console.log("Bot audio ended normally.");
            cleanupAndFinalize(false);
        };
        const errorHandler = (e) => { 
            console.error("Bot audio playback error event:", e); 
            cleanupAndFinalize(false, e); // Pass error
        };
        const pauseHandler = () => { 
            // This handler is primarily for barge-in. If audio is paused for other reasons,
            // it might lead to unintended "interruption" state if not handled carefully.
            // We assume pause here is due to barge-in stopping the audio.
            console.log("Bot audio paused (assumed barge-in or explicit stop).");
            cleanupAndFinalize(true); // Mark as interrupted
        };
        
        callBotAudio.addEventListener('ended', endedHandler);
        callBotAudio.addEventListener('error', errorHandler);
        callBotAudio.addEventListener('pause', pauseHandler);
        
        try {
            await callBotAudio.play();
        } catch (playException) {
            // If .play() itself throws an error (e.g., user hasn't interacted yet)
            console.error("Error on callBotAudio.play():", playException);
            // Call errorHandler to ensure cleanup and promise rejection
            errorHandler(playException); 
        }
    });
}
// Keyword Spotting and Barge-in (ensure updateStatus is used)
function initializeKeywordSpotter() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        updateStatus("Keyword spotting not supported by this browser.");
        console.warn("Speech Recognition API not supported.");
        return null;
    }
    const recognition = new SpeechRecognition();
    recognition.continuous = true;      
    recognition.interimResults = true;  
    recognition.lang = 'en-US';

    recognition.onstart = () => {
        console.log("Keyword Spotter: Event 'onstart' - Recognition actually started.");
        isKeywordSpottingActive = true; // Now we know it's active

        // Update UI based on why it was started
        const isForInCallBye = isCallModeActive && !botIsPlayingInCall && !isSystemBusy && isExplicitlyListeningForKeywords;
        const isForIdleWakeWord = isExplicitlyListeningForKeywords && !isCallModeActive;

        if (isForInCallBye) {
             updateStatus("Listening... (for speech or 'bye')");
        } else if (isForIdleWakeWord) { 
            updateStatus("Listening for wake word...");
            if (keywordListenToggleButton) keywordListenToggleButton.textContent = "🙉";
        } else {
            // This case might be for the temporary barge-in spotter in playBotInCall
            // Its status is usually "Bot speaking..." or similar managed by playBotInCall
            console.log("Keyword Spotter started for a temporary purpose (e.g., barge-in). Caller should manage status.");
        }
    };

    recognition.onresult = (event) => {
        let finalTranscript = ''; 
        let interimTranscript = '';
        for (let i = event.resultIndex; i < event.results.length; ++i) {
            if (event.results[i].isFinal) {
                finalTranscript += event.results[i][0].transcript;
            } else {
                interimTranscript += event.results[i][0].transcript;
            }
        }
        const currentFullTranscript = (finalTranscript + interimTranscript).trim().toLowerCase();
        const lastResultIsFinal = event.results[event.results.length-1].isFinal;

        if (currentFullTranscript.length === 0 && !lastResultIsFinal) return; // Ignore empty interim results

        console.log(`KeywordSpotter sees: "${currentFullTranscript}", Final: ${lastResultIsFinal}`);

        // Barge-in logic during bot speech (highest priority if bot is speaking)
        if (isCallModeActive && botIsPlayingInCall) { 
            if (currentFullTranscript.includes("stop")) { 
                console.log("Keyword: 'stop' (barge-in) detected during bot playback!");
                // handleBargeInInterrupt(); // This function should pause bot audio and update states
                if (callBotAudio && !callBotAudio.paused) {
                    callBotAudio.pause(); // This will trigger pauseHandler in playBotInCall
                }
            }
            return; // If bot is playing, only care about "stop" for now
        }

        // "Bye" detection during user's turn in a call
        if (isCallModeActive && !botIsPlayingInCall && !isSystemBusy) {
            // Make sure this specific spotter instance is the one intended for 'bye'
            // (This check is implicit if only one 'keywordSpotter' global is used and started correctly)
            const byeAlone = (
                lastResultIsFinal && 
                (currentFullTranscript === "bye" || currentFullTranscript === "buy" || currentFullTranscript === "by" ||
                 currentFullTranscript === "bye." || currentFullTranscript === "buy." || currentFullTranscript === "by.")
            );
            const byePhrase = currentFullTranscript.includes("bye " + WAKE_WORD) ||
                              currentFullTranscript.includes("goodbye " + WAKE_WORD) ||
                              currentFullTranscript.includes("bye, " + WAKE_WORD) ||
                              currentFullTranscript.includes("goodbye, " + WAKE_WORD) ||
                              currentFullTranscript.includes("okay bye") || 
                              currentFullTranscript.includes("ok, bye") || 
                              currentFullTranscript.includes("bye bye") ||
                              currentFullTranscript.includes("ok by") ||
                              currentFullTranscript.includes("good by"); 

            if (byePhrase || byeAlone) {
                console.log(`"Bye" detected during call! byePhrase: ${byePhrase}, byeAlone: ${byeAlone}. Transcript: "${currentFullTranscript}"`);
                // Stop this keyword spotter first before further actions
                stopKeywordSpotter(); 
                
                // Stop VAD loop and current user recording in call mode immediately
                if (callAnimFrameId) cancelAnimationFrame(callAnimFrameId); callAnimFrameId = null;
                if (callMediaRecorder && callMediaRecorder.state === "recording") {
                    callMediaRecorder.onstop = null; // Prevent normal VAD onstop processing
                    callMediaRecorder.stop();
                    console.log("Stopped user's VAD recording due to 'bye' keyword during call.");
                }
                userIsSpeaking = false; userSpokeThisTurn = false; silenceStartTime = 0;

                handleByeCommandInCall(); // This sends "Bye" to server and then calls stopCall()
                return; // Bye processed, no further processing for this result
            }
        } 
        
        // Idle mode keyword detection (wake words, chanakya alone)
        if (!isCallModeActive && !manualIsRecording && !isQuickWakeWordRecording && !isQuickCommandActive) {
            if (currentFullTranscript.includes("hey " + WAKE_WORD) || currentFullTranscript.includes("hi " + WAKE_WORD) || currentFullTranscript.includes("hey, " + WAKE_WORD) || currentFullTranscript.includes("hi, " + WAKE_WORD)) {
                console.log("Keyword: 'Hey/Hi " + WAKE_WORD + "' detected. Triggering call mode.");
                stopKeywordSpotter(); 
                toggleCallMode(); // This function will handle further state changes
            } else {
                let normalizedTranscript = currentFullTranscript; // Already toLowerCase and trimmed
                if (normalizedTranscript.endsWith('.')) {
                    normalizedTranscript = normalizedTranscript.substring(0, normalizedTranscript.length - 1);
                }
                const chanakyaAloneAsKeyword = 
                    lastResultIsFinal && 
                    (normalizedTranscript === WAKE_WORD || normalizedTranscript.startsWith(WAKE_WORD + " ")) &&
                    !(currentFullTranscript.includes("hey " + WAKE_WORD) || currentFullTranscript.includes("hi " + WAKE_WORD));
                                        
                if (chanakyaAloneAsKeyword) {
                     console.log("Keyword: '" + WAKE_WORD + "' (alone, final, normalized) detected. Triggering short record.");
                     stopKeywordSpotter(); 
                     triggerQuickWakeWordRecording();
                } else if (!lastResultIsFinal && 
                           currentFullTranscript.includes(WAKE_WORD) &&
                           !(currentFullTranscript.includes("hey " + WAKE_WORD) || currentFullTranscript.includes("hi " + WAKE_WORD))) {
                    updateStatus(WAKE_WORD + " heard...");
                }
            }
        }
    };

    recognition.onerror = (event) => {
        console.error("KeywordSpotter error event:", event.error);
        const wasIntendedToBeActive = isKeywordSpottingActive || (isExplicitlyListeningForKeywords && !isCallModeActive && !manualIsRecording /*...other modes...*/);
        
        isKeywordSpottingActive = false; // Recognition definitely stopped or failed to start

        if (event.error === 'no-speech' || event.error === 'audio-capture') {
            if (wasIntendedToBeActive) { // If it was supposed to be running
                 // Attempt to restart only if conditions are still valid for it to run
                const shouldBeIdleSpotting = isExplicitlyListeningForKeywords && !isCallModeActive && !manualIsRecording && !isQuickWakeWordRecording && !isQuickCommandActive;
                const shouldBeInCallByeSpotting = isExplicitlyListeningForKeywords && isCallModeActive && !botIsPlayingInCall && !isSystemBusy;

                if (shouldBeIdleSpotting) {
                    console.log("Attempting to restart idle keyword spotter after 'no-speech' or 'audio-capture'.");
                    setTimeout(() => { if (isExplicitlyListeningForKeywords && !isKeywordSpottingActive && !isCallModeActive) startKeywordSpotter(); }, 500);
                } else if (shouldBeInCallByeSpotting) {
                    console.log("Attempting to restart in-call 'bye' spotter after 'no-speech' or 'audio-capture'.");
                    setTimeout(() => { if (isExplicitlyListeningForKeywords && isCallModeActive && !botIsPlayingInCall && !isSystemBusy && !isKeywordSpottingActive) startKeywordSpotter({ forInCallByeDetection: true }); }, 500);
                }
            }
        } else if (event.error === 'not-allowed') {
            alert("Microphone permission denied. Keyword spotting disabled.");
            updateStatus("Mic permission denied.");
            isExplicitlyListeningForKeywords = false; 
            if(keywordListenToggleButton) keywordListenToggleButton.textContent = "👂";
        } else {
            updateStatus("Keyword spotter error: " + event.error);
            if (keywordListenToggleButton && !isCallModeActive && isExplicitlyListeningForKeywords) { // If it was supposed to be on
                 keywordListenToggleButton.textContent = "👂"; // Reset button
                 isExplicitlyListeningForKeywords = false; // Mark user intent as off due to error
            }
        }
    };

    recognition.onend = () => {
        console.log("Keyword Spotter: Event 'onend' - Recognition actually ended.");
        const wasOurLogicExpectingItToBeActive = isKeywordSpottingActive; 
        isKeywordSpottingActive = false; 

        const shouldBeIdleSpotting = isExplicitlyListeningForKeywords && !isCallModeActive && !manualIsRecording && !isQuickWakeWordRecording && !isQuickCommandActive && !isSystemBusy;
        const shouldBeInCallByeSpotting = isExplicitlyListeningForKeywords && isCallModeActive && !botIsPlayingInCall && !isSystemBusy;

        if (wasOurLogicExpectingItToBeActive && (shouldBeIdleSpotting || shouldBeInCallByeSpotting)) {
            console.log("KeywordSpotter service ended. Conditions met for restart.");
            setTimeout(() => {
                if (shouldBeIdleSpotting && !isKeywordSpottingActive) {
                     startKeywordSpotter();
                } else if (shouldBeInCallByeSpotting && !isKeywordSpottingActive) {
                     startKeywordSpotter({ forInCallByeDetection: true });
                }
            }, 250);
        } else {
            console.log("KeywordSpotter service ended. Not restarting (was deliberately stopped or conditions not met).");
            if (keywordListenToggleButton && !isCallModeActive) { 
                if (isExplicitlyListeningForKeywords) {
                    // This means it ended but user still wants it on - restart logic above should catch.
                    // If not, button might be out of sync.
                } else { // User explicitly turned it off or it was never on via toggle
                    keywordListenToggleButton.textContent = "👂";
                }
                if (!isCallModeActive && !isQuickWakeWordRecording && !manualIsRecording && !isKeywordSpottingActive) {
                    updateStatus("Ready.");
                }
            }
        }
    };
    return recognition;
}

function startKeywordSpotter(options = {}) {
    const forInCallByeDetection = options.forInCallByeDetection || false;

    if (isKeywordSpottingActive) { // Check our flag: if we think it's active, don't try to start again.
        console.log(`Keyword spotter already considered active (isKeywordSpottingActive=true). Purpose: ${forInCallByeDetection ? 'in-call bye' : 'idle'}. Not starting again.`);
        return false; 
    }

    // Apply other guards based on purpose
    if (!forInCallByeDetection) { 
        if (isCallModeActive || manualIsRecording || isQuickCommandActive || isQuickWakeWordRecording) {
            console.log("Cannot start idle keyword spotter: conflicting mode active.");
            return false;
        }
    } else { // For in-call "bye" detection
        if (!isCallModeActive || botIsPlayingInCall || isSystemBusy) {
            console.log("Cannot start in-call 'bye' spotter: conditions not met (call not active, bot speaking, or system busy).");
            return false;
        }
    }

    if (!keywordSpotter) {
        console.log("Keyword spotter not initialized, initializing now...");
        keywordSpotter = initializeKeywordSpotter(); 
        if (!keywordSpotter) { // Check if initialization failed
            updateStatus("Failed to initialize keyword spotter.");
            return false;
        }
    }

    // At this point, keywordSpotter exists and isKeywordSpottingActive is false.
    try {
        console.log(`Attempting to start keyword spotter (Purpose: ${forInCallByeDetection ? 'in-call bye' : 'idle'})...`);
        keywordSpotter.start(); 
        // DO NOT set isKeywordSpottingActive = true here. It's set in recognition.onstart.
        // The UI updates (button text, status) should also ideally happen in onstart.
        // For now, we can optimistically update some UI if needed by the caller.
        if (forInCallByeDetection) {
            // updateStatus("Listening... (for speech or 'bye')"); // Status set in onstart
        } else {
            // updateStatus("Listening for wake word..."); // Status set in onstart
            // if (keywordListenToggleButton) keywordListenToggleButton.textContent = "Stop Keyword Listening";
        }
        return true; // Indicate start request was made
    } catch (e) { 
        console.error(`Error calling keywordSpotter.start() (Purpose: ${forInCallByeDetection ? 'in-call bye' : 'idle'}):`, e);
        isKeywordSpottingActive = false; // Ensure flag is reset on immediate error
        updateStatus("Error starting keyword listener.");
        if (!forInCallByeDetection && keywordListenToggleButton) {
            keywordListenToggleButton.textContent = "👂";
        }
        return false; 
    }
}


function stopKeywordSpotter() {
    if (keywordSpotter && isKeywordSpottingActive) { // Check our flag
        console.log("Attempting to stop active keyword spotter (isKeywordSpottingActive=true)...");
        try {
            keywordSpotter.stop(); // Request stop
            // Actual state change to not active will be confirmed by 'onend' event
        } catch (e) {
            console.error("Error during keywordSpotter.stop():", e);
            isKeywordSpottingActive = false; // Force our flag if stop() throws
            // Update UI to reflect it's off, even if onend doesn't fire
            if (keywordListenToggleButton && !isCallModeActive) { // Only update main toggle if not in call
                 keywordListenToggleButton.textContent = "👂";
            }
            if (!isCallModeActive && !manualIsRecording && !isQuickWakeWordRecording && !isQuickCommandActive) {
                updateStatus("Keyword listening stopped (error).");
            }
        }
    } else {
        // If our flag is already false, but make sure the button is correct if idle
        if (keywordListenToggleButton && !isCallModeActive && !isExplicitlyListeningForKeywords) {
            keywordListenToggleButton.textContent = "👂";
        }
        console.log("stopKeywordSpotter called, but spotter not considered active by flag or not initialized.");
    }
    // We don't set isKeywordSpottingActive = false here directly anymore.
    // It should be set in the 'onend' or 'onerror' of the recognition object.
    // However, if stop() itself errors, we might need to force it.
    // For now, let's assume onend will handle it. If not, we can add isKeywordSpottingActive = false here.
}


// handleToggleKeywordListening (from previous response) should correctly call these
async function handleToggleKeywordListening() {
    console.log("handleToggleKeywordListening called. Current isExplicitlyListeningForKeywords:", isExplicitlyListeningForKeywords);
    
    if (!audioPlaybackUnlocked) {
        const unlocked = await unlockAudioPlayback();
        if (!unlocked) {
            updateStatus("Audio unlock failed. Cannot change listening state.");
            return; 
        }
    }
    
    if (isExplicitlyListeningForKeywords) { // If user intended it to be ON, now turn it OFF
        isExplicitlyListeningForKeywords = false; 
        stopKeywordSpotter(); // This will update button and status via onend or directly
        // updateStatus("Keyword listening OFF by user."); // More specific status
    } else { // If user intended it to be OFF (or it's the first time), now turn it ON
        // Attempt to start idle keyword spotter
        if (startKeywordSpotter()) { // No options needed, it's for idle by default
            // isExplicitlyListeningForKeywords will be set if start is successful
            // and onstart fires to set isKeywordSpottingActive
            // For now, we can assume if startKeywordSpotter doesn't immediately fail (returns true),
            // the intent is set. The actual state is isKeywordSpottingActive.
            isExplicitlyListeningForKeywords = true; 
            // Button text and status are updated by startKeywordSpotter via onstart
        } else {
            console.log("handleToggleKeywordListening: Idle startKeywordSpotter failed to initiate.");
            isExplicitlyListeningForKeywords = false; // Ensure intent is off if start fails
            if (keywordListenToggleButton) keywordListenToggleButton.textContent = "👂";
            // Status potentially updated by startKeywordSpotter on failure
        }
    }
    console.log("handleToggleKeywordListening finished. New isExplicitlyListeningForKeywords:", isExplicitlyListeningForKeywords);
}

async function triggerQuickWakeWordRecording() {
    // ... (ensure updateStatus is used)
    // ... (ensure isQuickCommandActive is reset in all paths of onstop)
    if (isQuickCommandActive || isCallModeActive || manualIsRecording || isQuickWakeWordRecording) return;
    await unlockAudioPlayback(); // Ensure audio is unlocked
    isQuickWakeWordRecording = true; updateStatus(WAKE_WORD + " listening for command...");
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        quickWakeWordAudioChunks = [];
        quickWakeWordRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
        quickWakeWordRecorder.ondataavailable = (e) => { if (e.data.size > 0) quickWakeWordAudioChunks.push(e.data); };
        quickWakeWordRecorder.onstop = async () => {
            isQuickWakeWordRecording = false;
            stream.getTracks().forEach(track => track.stop());
            if (quickWakeWordAudioChunks.length === 0) {
                appendMessage("You (Audio to " + WAKE_WORD + "): (No speech detected)", "user");
                updateStatus("No command heard."); 
                isQuickCommandActive = false; 
                if (!isCallModeActive && !manualIsRecording && isExplicitlyListeningForKeywords) startKeywordSpotter();
                else if (!isCallModeActive && !manualIsRecording) updateStatus("Ready.");
                return;
            }
            updateStatus(WAKE_WORD + " processing command...");
            const audioBlob = new Blob(quickWakeWordAudioChunks, { type: quickWakeWordRecorder.mimeType });
            quickWakeWordAudioChunks = [];
            const formData = new FormData(); formData.append('audio', audioBlob, WAKE_WORD + '_command.webm');
            try {
                const serverResponse = await fetch('/record', { method: 'POST', body: formData });
                const data = await serverResponse.json();
                if (data.error) { appendMessage(`${WAKE_WORD} Error: ${data.error}`, "bot"); updateStatus("Error processing command.");}
                else {
                    if (data.transcription) appendMessage(data.transcription, "user");
                    appendMessage(data.response, "bot");
                    if (data.audio_data_url) {
                        const commandResponseAudio = new Audio(data.audio_data_url);
                        updateStatus(WAKE_WORD + " speaking...");
                        try {
                            await commandResponseAudio.play();
                            commandResponseAudio.onended = () => { 
                                updateStatus("Listening for wake word..."); 
                                isQuickCommandActive = false; 
                                if (!isCallModeActive && !manualIsRecording && isExplicitlyListeningForKeywords) startKeywordSpotter(); 
                                else if (!isCallModeActive && !manualIsRecording) updateStatus("Ready.");
                            };
                            commandResponseAudio.onerror = (e) => { console.error(e); updateStatus(WAKE_WORD + " audio error."); isQuickCommandActive = false; if (!isCallModeActive && !manualIsRecording) startKeywordSpotter(); };
                        } catch (playError) {
                             console.error("Quick command play error:", playError); updateStatus("Audio play blocked."); isQuickCommandActive = false; if (!isCallModeActive && !manualIsRecording) startKeywordSpotter();
                        }
                    } else { 
                        updateStatus(WAKE_WORD + " processed (no speech).");
                        isQuickCommandActive = false; 
                        if (!isCallModeActive && !manualIsRecording && isExplicitlyListeningForKeywords) startKeywordSpotter(); 
                        else if (!isCallModeActive && !manualIsRecording) updateStatus("Ready.");
                    }
                }
            } catch (error) { console.error(error); appendMessage(WAKE_WORD + " Error: Network issue.", "bot"); updateStatus("Network error."); isQuickCommandActive = false; if (!isCallModeActive && !manualIsRecording && isExplicitlyListeningForKeywords) startKeywordSpotter(); else if (!isCallModeActive && !manualIsRecording) updateStatus("Ready.");}
            isQuickCommandActive = false; // Ensure reset
        };
        quickWakeWordRecorder.start(); clearTimeout(quickWakeWordSilenceTimer);
        let lastSpeechTime = Date.now();
        let qcraContext = callAudioContext && callAudioContext.state === 'running' ? callAudioContext : new (window.AudioContext || window.webkitAudioContext)();
        if (qcraContext.state === 'suspended') await qcraContext.resume();
        const qcraMicSource = qcraContext.createMediaStreamSource(stream);
        const qcraAnalyser = qcraContext.createAnalyser();
        qcraAnalyser.fftSize = 512; qcraMicSource.connect(qcraAnalyser);
        function monitorQuickCommandAudio() {
            if (!isQuickWakeWordRecording) { qcraMicSource.disconnect(); return; }
            const dataArray = new Uint8Array(qcraAnalyser.frequencyBinCount);
            qcraAnalyser.getByteTimeDomainData(dataArray);
            let sum = 0; dataArray.forEach(val => sum += Math.abs(val - 128));
            if (sum / dataArray.length > SPEECH_LVL_THRESHOLD / 2) lastSpeechTime = Date.now();
            if (Date.now() - lastSpeechTime > QUICK_WAKE_WORD_SILENCE_TIMEOUT_MS) {
                if (quickWakeWordRecorder.state === "recording") quickWakeWordRecorder.stop();
            } else requestAnimationFrame(monitorQuickCommandAudio);
        }
        requestAnimationFrame(monitorQuickCommandAudio);
    } catch (error) { console.error(error); updateStatus("Mic error for " + WAKE_WORD + "."); isQuickWakeWordRecording = false; isQuickCommandActive = false; if (!isCallModeActive && !manualIsRecording) startKeywordSpotter(); }
}

async function testVoiceClone() {
    const text = messageInput.value.trim();
    if (!text) {
        alert("Please enter some text to test the voice clone.");
        return;
    }

    const voiceFileInput = document.getElementById('voice-file-input');
    const voicePresetSelect = document.getElementById('voice-preset-select');

    if (voiceFileInput.files.length === 0 && !voicePresetSelect.value) {
        alert("Please select a voice file or a preset to test.");
        return;
    }

    const formData = new FormData();
    formData.append('text', text);

    if (voiceFileInput.files.length > 0) {
        formData.append('voice_file', voiceFileInput.files[0]);
    } else if (voicePresetSelect.value) {
        formData.append('voice_preset', voicePresetSelect.value);
    }

    try {
        updateStatus("Cloning voice...");
        const response = await fetch('/voice_clone', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (data.audio_data_url) {
            const audio = new Audio(data.audio_data_url);
            audio.play();
            updateStatus("Voice test complete.");
        } else {
            alert("Failed to clone voice: " + (data.error || "Unknown error"));
            updateStatus("Voice test failed.");
        }
    } catch (error) {
        console.error("Error testing voice clone:", error);
        alert("An error occurred while testing the voice clone.");
        updateStatus("Voice test failed.");
    }
}

// Initial Load
window.addEventListener('load', () => {
    // DOM Elements are assigned in DOMContentLoaded, so this is fine.
    // applyDarkModePreference(); // This will be called inside DOMContentLoaded
    // startKeywordSpotter(); // This will be called inside DOMContentLoaded with a delay
});

// === DOMContentLoaded to initialize elements and status updater ===
async function populateVoiceList() {
    try {
        const response = await fetch('/list_voices');
        if (!response.ok) {
            console.error('Failed to fetch voice list');
            return;
        }
        const voices = await response.json();
        const selectElement = document.getElementById('voice-preset-select');
        if (selectElement) {
            voices.forEach(voice => {
                const option = document.createElement('option');
                option.value = voice;
                option.textContent = voice;
                selectElement.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Error populating voice list:', error);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // Assign DOM elements
    chatArea = document.getElementById("chat-area");
    messageInput = document.getElementById("message-input");
    sendButton = document.getElementById("sendButton");
    recordButton = document.getElementById("recordButton");
    playResponseButton = document.getElementById("playResponseButton");
    darkModeButton = document.getElementById("darkModeButton");
    callModeButton = document.getElementById("callModeButton");
    statusIndicator = document.getElementById("statusIndicator"); 
    toggleChatButton = document.getElementById("toggleChatButton");
    chatAreaWrapper = document.getElementById("chatAreaWrapper");
    animationArea = document.getElementById("animationArea");
    orb = document.getElementById('orb');
    orbCore = document.getElementById('orbCore');

    keywordListenToggleButton = document.getElementById('keywordListenToggleButton'); // Assign new button

    // Initial UI setup
    applyDarkModePreference(); 
    applyChatVisibilityPreference();
    populateVoiceList();

    if (orb && orbCore) { 
        if (!isChatVisible) initializeParticles(); 
    } else {
        console.error("Orb elements not found!");
    }
    updateStatus("Ready. Click 'Start Keyword Listening'."); // Initial status for the new button

    // Add event listeners
    if(toggleChatButton) toggleChatButton.addEventListener('click', toggleChatAreaVisibility);
    if(sendButton) sendButton.addEventListener('click', sendMessage);
    if(playResponseButton) playResponseButton.addEventListener('click', playResponse);
    if(callModeButton) callModeButton.addEventListener('click', toggleCallMode);
    if(recordButton) recordButton.addEventListener('click', toggleRecord);
    if(darkModeButton) darkModeButton.addEventListener('click', toggleDarkMode); // Added listener if not using onclick
    if(keywordListenToggleButton) keywordListenToggleButton.addEventListener('click', handleToggleKeywordListening);
    const testVoiceButton = document.getElementById('test-voice-button');
    if(testVoiceButton) testVoiceButton.addEventListener('click', testVoiceClone);

    // Remove the old general unlock logic that starts keyword spotting automatically.
    // The new button will handle unlocking and starting.
    // Your existing unlock logic within other buttons (send, record) via unlockAudioPlayback() is fine.
    // The 'setTimeout(() => { startKeywordSpotter(); }, 1000);' should be removed.
    // The app should now wait for the user to click the "Start Keyword Listening" button.

    // If you still want a general unlock on first interaction for OTHER audio features
    // (not keyword spotting), that's fine, but keyword spotting is now explicitly controlled.
    const generalUnlockHandler = async (event) => {
        console.log("General Unlock handler triggered by:", event.type, event.currentTarget ? event.currentTarget.id : 'unknown');
        await unlockAudioPlayback(); // Just unlock, don't start spotter
        // Remove listeners once unlocked
        const mainButtons = [sendButton, recordButton, playResponseButton, callModeButton, toggleChatButton, darkModeButton]; // Redefine if not global
        mainButtons.forEach(btn => { if(btn) btn.removeEventListener('click', generalUnlockHandler);});
        if(messageInput) messageInput.removeEventListener('keydown', generalUnlockHandler);
        if(document.body) document.body.removeEventListener('click', generalUnlockHandler); 
    };

    if (!audioPlaybackUnlocked) {
        console.log("Setting up general audio unlock event listeners (not for autostarting keyword spotter).");
        const mainButtons = [sendButton, recordButton, playResponseButton, callModeButton, toggleChatButton, darkModeButton]; // Redefine if not global
        mainButtons.forEach(btn => { 
            if (btn && btn !== keywordListenToggleButton) btn.addEventListener('click', generalUnlockHandler, { once: true }); 
        });
        if (messageInput) {
            messageInput.addEventListener('keydown', generalUnlockHandler, { once: true });
        }
    }
});
