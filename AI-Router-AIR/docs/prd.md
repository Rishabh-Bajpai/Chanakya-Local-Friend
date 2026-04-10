Project Requirements Document: AIR (AI Router)

1. Executive Summary

AIR (AI Router) is a self-hosted, open-source AI gateway designed to unify various artificial intelligence models and providers under a single, seamless interface. By exposing a comprehensive OpenAI-compatible API, AIR allows any client application to utilize multiple AI modalities (Text, Image, Video, TTS, STT) and advanced features (RAG, Webhooks, Realtime) without requiring app-side code changes. It acts as a centralized control plane for managing API keys, local models (like Ollama and vLLM), data sources, and client application access, prioritizing enterprise-grade security and simplicity.

2. Objectives & Goals





Unified Abstraction: Serve as a strict drop-in replacement for the OpenAI API (/v1/*) while routing requests to various commercial (OpenAI, Anthropic, Google Cloud) and local (Ollama, LM Studio) providers.



Comprehensive API Coverage: Support the full spectrum of OpenAI features, including complex stateful APIs like Assistants, Threads, Vector Stores, and Realtime communication.



Centralized Management: Provide a clean UI for administrators to configure AI providers, external tools, webhooks, and connected data sources.



Secure & Self-Hosted: Ensure robust security for API key storage, strict role-based access control (RBAC) for client apps, and easy self-hosting capabilities via containerization (Docker/Kubernetes).

3. Scope: The API Contract

AIR will support the following endpoints. They are divided into two primary tiers.

Tier 1: OpenAI Compatible Endpoints (The Core Abstraction)

These endpoints must perfectly mimic OpenAI's API specifications to ensure zero-code changes for downstream applications.

3.1. Stateless Generation APIs





Chat Completions: POST /v1/chat/completions



Embeddings: POST /v1/embeddings



Images: POST /v1/images/generations, POST /v1/images/edits, POST /v1/images/variations



Audio: POST /v1/audio/transcriptions, POST /v1/audio/translations, POST /v1/audio/speech, POST /v1/audio/voices



Moderations: POST /v1/moderations

3.2. Stateful & File Management APIs





Files: GET /v1/files, POST /v1/files, GET /v1/files/{id}, DELETE /v1/files/{id}, GET /v1/files/{id}/content



Uploads (Multipart): POST /v1/uploads, POST /v1/uploads/{id}/cancel, POST /v1/uploads/{id}/complete, POST /v1/uploads/{id}/parts



Vector Stores (RAG Pipeline):





Stores: Create, Retrieve, Update, Delete, List, Search.



Files: Create, List, Retrieve, Update, Delete, Content.



Batches: Create, Retrieve, List, Cancel.

3.3. Advanced Features (Conversations, Responses, Webhooks)





Responses: Create, Retrieve, Delete, List input items, Count tokens, Cancel, Compact.



Conversations & Items: Create, Retrieve, Update, Delete, List.



Webhooks: Support for OpenAI-compatible event structures.



Batches: Create, Retrieve, List, Cancel.



Evals: Create, Retrieve, Update, Delete, List, Create Run, List Output Items.



ChatKit (Beta): Sessions (Create, Cancel), Threads (Retrieve, Delete, List Items).



Skills: Create, Retrieve, List, Create Version.



Realtime: Create Secret, Calls (Accept, Hangup).

Tier 2: AIR Custom Management Endpoints (/air/v1/)

These endpoints are unique to AIR and are used by the admin dashboard to configure the router. They are not exposed to standard client apps.

3.4. Provider & Routing Configuration





POST /air/v1/providers: Add a new upstream provider (e.g., Groq, DeepInfra, Ollama) with its base URL and API keys.



GET /air/v1/providers: List active providers and their health status.



POST /air/v1/routes: Define routing logic (e.g., "If model = 'gpt-4o', route to OpenAI; if model = 'llama3', route to Ollama").



POST /air/v1/fallbacks: Configure fallback chains (e.g., "Try LM Studio first, fallback to Together AI if timeout").

3.5. Application & Access Control





POST /air/v1/apps/register: Register a new client application and generate a unique AIR API key.



POST /air/v1/auth/refresh: Rotate or refresh client tokens.



GET /air/v1/apps/{id}/roles: Configure what models, tools, and vector stores an app is allowed to access.



POST /air/v1/limits: Set rate limits (RPM/TPM) per app.

3.6. Internal Administration





GET /air/v1/admin/audit_logs: Fetch router-level logs for debugging.



GET /air/v1/admin/usage: Monitor token usage and costs across all downstream apps and upstream providers.



4. Functional Requirements

4.1. The Backend (Routing & API Engine)





Dynamic Routing: Route requests seamlessly. The backend must intercept the model parameter in the request body and forward the payload to the appropriate provider based on admin configuration.



RAG Interceptor: When a /v1/chat/completions request includes a vector_store_id, AIR must embed the prompt, query the local PostgreSQL (pgvector) database, retrieve context, and inject it into the prompt before forwarding to the local model.



Streaming Support: Full support for Server-Sent Events (SSE) for all generative endpoints.



Asynchronous Processing: Background workers must handle file chunking, embedding, and vector store ingestion without blocking API requests.

4.2. The Frontend (Configuration UI)





Dashboard: Real-time metrics on system health, routing success rates, and token usage per app.



Data Source Management: Visual interface to upload documents, manage Vector Stores, and view embedding status.



5. Non-Functional Requirements

5.1. Security





Data Isolation: Implement strict database-level scoping so App A cannot access App B's files, vector stores, or conversations.



Encryption: All upstream provider API keys (e.g., SambaNova, Groq) must be encrypted at rest in the database.

5.2. Architecture & Data Layer





Database: PostgreSQL with the pgvector extension is required to handle both relational configuration data and vector embeddings within a single, unified state.



Deployment: Fully containerized using Docker, designed to run efficiently on local clusters alongside other self-hosted services.