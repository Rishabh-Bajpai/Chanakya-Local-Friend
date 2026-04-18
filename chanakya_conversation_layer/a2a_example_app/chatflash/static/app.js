const state = {
  sessions: window.__CHATFLASH__.sessions,
  selectedSession: window.__CHATFLASH__.selectedSession,
  messages: window.__CHATFLASH__.messages,
  agents: window.__CHATFLASH__.agents,
  sending: false,
};

const els = {
  sessionsList: document.getElementById("sessions-list"),
  transcript: document.getElementById("chat-transcript"),
  sessionTitle: document.getElementById("session-title"),
  agentSelect: document.getElementById("agent-select"),
  messageInput: document.getElementById("message-input"),
  composer: document.getElementById("composer"),
  newSession: document.getElementById("new-session"),
  error: document.getElementById("flash-error"),
};

function showError(message) {
  if (!message) {
    els.error.classList.add("hidden");
    els.error.textContent = "";
    return;
  }
  els.error.textContent = message;
  els.error.classList.remove("hidden");
}

function activeAgent() {
  return els.agentSelect.value || state.selectedSession.agent_id;
}

function renderAgents() {
  els.agentSelect.innerHTML = "";
  for (const agent of state.agents) {
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.label;
    els.agentSelect.appendChild(option);
  }
  els.agentSelect.value = state.selectedSession.agent_id;
}

function renderSessions() {
  els.sessionsList.innerHTML = "";
  for (const session of state.sessions) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `session-item ${session.id === state.selectedSession.id ? "active" : ""}`;
    item.innerHTML = `
      <div class="session-title">${session.title}</div>
      <div class="session-subtitle">${labelForAgent(session.agent_id)}</div>
    `;
    item.addEventListener("click", () => loadSession(session.id));
    els.sessionsList.appendChild(item);
  }
}

function labelForAgent(agentId) {
  const agent = state.agents.find((item) => item.id === agentId);
  return agent ? agent.label : agentId;
}

function renderMessages() {
  els.transcript.innerHTML = "";
  for (const message of state.messages) {
    const article = document.createElement("article");
    article.className = `message ${message.role}`;
    article.innerHTML = `
      <span class="message-meta">${message.role}</span>
      <div>${escapeHtml(message.content)}</div>
    `;
    els.transcript.appendChild(article);
  }
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function render() {
  els.sessionTitle.textContent = state.selectedSession.title;
  renderAgents();
  renderSessions();
  renderMessages();
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;")
    .replaceAll("\n", "<br />");
}

async function loadSession(sessionId) {
  showError("");
  const response = await fetch(`/api/sessions/${sessionId}`);
  if (!response.ok) {
    showError("Failed to load the selected session.");
    return;
  }
  const payload = await response.json();
  state.selectedSession = payload.session;
  state.messages = payload.messages;
  render();
}

async function createSession() {
  showError("");
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id: activeAgent() || "local:assistant" }),
  });
  if (!response.ok) {
    showError("Failed to create a new session.");
    return;
  }
  const payload = await response.json();
  state.selectedSession = payload.session;
  state.messages = payload.messages;
  state.sessions = [payload.session, ...state.sessions.filter((item) => item.id !== payload.session.id)];
  render();
  els.messageInput.focus();
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.sending) return;
  const content = els.messageInput.value.trim();
  if (!content) return;

  state.sending = true;
  showError("");
  els.messageInput.value = "";

  try {
    const response = await fetch(`/api/sessions/${state.selectedSession.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, agent_id: activeAgent() }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Failed to send message.");
    }

    state.selectedSession = payload.session;
    state.messages.push(payload.user_message, payload.assistant_message);
    state.sessions = [payload.session, ...state.sessions.filter((item) => item.id !== payload.session.id)];
    render();
    showError(payload.error || "");
  } catch (error) {
    showError(error.message || "Unexpected error while sending message.");
  } finally {
    state.sending = false;
    els.messageInput.focus();
  }
}

els.newSession.addEventListener("click", createSession);
els.composer.addEventListener("submit", sendMessage);
els.agentSelect.addEventListener("change", () => {
  state.selectedSession.agent_id = els.agentSelect.value;
  renderSessions();
});

els.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});

render();
