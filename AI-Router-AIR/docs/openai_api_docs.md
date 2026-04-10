# OpenAI API Reference Documentation
AIR will have its own APIs, which will be a superset of the OpenAI APIs.

---

## 1. API Reference

- [ ] **[Introduction](https://developers.openai.com/api/reference/overview)** - General overview.
- [ ] **[Authentication](https://developers.openai.com/api/reference/overview#authentication)** - API keys and security.
- [ ] **[Debugging Requests](https://developers.openai.com/api/reference/overview#debugging-requests)** - Troubleshooting tools.
- [ ] **[Backwards Compatibility](https://developers.openai.com/api/reference/overview#backwards-compatibility)** - Policy on changes.

---

## 2. Responses API

### Responses

- [ ] **[Create a response](https://developers.openai.com/api/reference/resources/responses/methods/create)** (`POST /v1/responses`) - Creates a model response with text, images, and tool usage.
- [ ] **[Retrieve a response](https://developers.openai.com/api/reference/resources/responses/methods/retrieve)** (`GET /v1/responses/{id}`) - Retrieves a model response with the given ID.
- [ ] **[Delete a response](https://developers.openai.com/api/reference/resources/responses/methods/delete)** (`DELETE /v1/responses/{id}`) - Deletes a model response with the given ID.
- [ ] **[List input items](https://developers.openai.com/api/reference/resources/responses/subresources/input_items/methods/list)** (`GET /v1/responses/{id}/input_items`) - Returns a list of input items for a given response.
- [ ] **[Count input tokens](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count)** (`POST /v1/responses/input_tokens`) - Returns input token counts of the request.
- [ ] **[Cancel a response](https://developers.openai.com/api/reference/resources/responses/methods/cancel)** (`POST /v1/responses/{id}/cancel`) - Cancels an active model response.
- [ ] **[Compact a response](https://developers.openai.com/api/reference/resources/responses/methods/compact)** (`POST /v1/responses/{id}/compact`) - Returns a compacted version of the response.

### Conversations

- [ ] **[Create a conversation](https://developers.openai.com/api/reference/resources/conversations/methods/create)** (`POST /v1/conversations`) - Creates a new conversation.
- [ ] **[Retrieve a conversation](https://developers.openai.com/api/reference/resources/conversations/methods/retrieve)** (`GET /v1/conversations/{id}`) - Retrieves a conversation.
- [ ] **[Update a conversation](https://developers.openai.com/api/reference/resources/conversations/methods/update)** (`POST /v1/conversations/{id}`) - Updates a conversation.
- [ ] **[Delete a conversation](https://developers.openai.com/api/reference/resources/conversations/methods/delete)** (`DELETE /v1/conversations/{id}`) - Deletes a conversation.

### Items

- [ ] **[Create items](https://developers.openai.com/api/reference/resources/conversations/subresources/items/methods/create)** (`POST /v1/conversations/{id}/items`) - Creates items in a conversation.
- [ ] **[Retrieve an item](https://developers.openai.com/api/reference/resources/conversations/subresources/items/methods/retrieve)** (`GET /v1/conversations/{id}/items/{item_id}`) - Gets a single item from a conversation.
- [ ] **[Delete an item](https://developers.openai.com/api/reference/resources/conversations/subresources/items/methods/delete)** (`DELETE /v1/conversations/{id}/items/{item_id}`) - Deletes an item from a conversation.
- [ ] **[List items](https://developers.openai.com/api/reference/resources/conversations/subresources/items/methods/list)** (`GET /v1/conversations/{id}/items`) - Lists all items for a conversation.

---

## 3. Webhooks

- [ ] **[Events](https://developers.openai.com/api/reference/resources/webhooks)** - Overview of webhook events and signatures.

---

## 4. Platform APIs

### Audio

- [ ] **[Create transcription](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)** (`POST /v1/audio/transcriptions`) - Transcribes audio into the input language.
- [ ] **[Create translation](https://developers.openai.com/api/reference/resources/audio/subresources/translations/methods/create)** (`POST /v1/audio/translations`) - Translates audio into English.
- [ ] **[Create speech](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)** (`POST /v1/audio/speech`) - Generates audio from the input text.
- [ ] **[Create voice](https://developers.openai.com/api/reference/resources/audio/subresources/voices/methods/create)** (`POST /v1/audio/voices`) - Creates a custom voice.

#### Voice Consents

- [ ] **[Create voice consent](https://developers.openai.com/api/reference/resources/audio/subresources/voice_consents/methods/create)** (`POST /v1/audio/voice_consents`) - Upload a voice consent recording.
- [ ] **[Retrieve voice consent](https://developers.openai.com/api/reference/resources/audio/subresources/voice_consents/methods/retrieve)** (`GET /v1/audio/voice_consents/{id}`) - Retrieves voice consent details.
- [ ] **[Update voice consent](https://developers.openai.com/api/reference/resources/audio/subresources/voice_consents/methods/update)** (`POST /v1/audio/voice_consents/{id}`) - Updates metadata of a consent recording.
- [ ] **[Delete voice consent](https://developers.openai.com/api/reference/resources/audio/subresources/voice_consents/methods/delete)** (`DELETE /v1/audio/voice_consents/{id}`) - Deletes a consent recording.
- [ ] **[List voice consents](https://developers.openai.com/api/reference/resources/audio/subresources/voice_consents/methods/list)** (`GET /v1/audio/voice_consents`) - Lists all voice consents.

### Videos

- [ ] **[Create video](https://developers.openai.com/api/reference/resources/videos/methods/create)** (`POST /v1/videos`) - Creates a new video generation job.
- [ ] **[Retrieve video](https://developers.openai.com/api/reference/resources/videos/methods/retrieve)** (`GET /v1/videos/{id}`) - Fetches metadata for a video.
- [ ] **[Delete video](https://developers.openai.com/api/reference/resources/videos/methods/delete)** (`DELETE /v1/videos/{id}`) - Deletes a video and its assets.
- [ ] **[List videos](https://developers.openai.com/api/reference/resources/videos/methods/list)** (`GET /v1/videos`) - Lists recently generated videos.
- [ ] **[Retrieve content](https://developers.openai.com/api/reference/resources/videos/methods/download_content)** (`GET /v1/videos/{id}/content`) - Downloads video bytes or preview.
- [ ] **[Remix video](https://developers.openai.com/api/reference/resources/videos/methods/remix)** (`POST /v1/videos/{id}/remix`) - Creates a remix of a completed video.

### Images

- [ ] **[Create image](https://developers.openai.com/api/reference/resources/images/methods/generate)** (`POST /v1/images/generations`) - Generates an image given a prompt.
- [ ] **[Create image edit](https://developers.openai.com/api/reference/resources/images/methods/edit)** (`POST /v1/images/edits`) - Edits or extends an image.
- [ ] **[Create image variation](https://developers.openai.com/api/reference/resources/images/methods/create_variation)** (`POST /v1/images/variations`) - Creates variations of an image.

### Embeddings

- [ ] **[Create embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create)** (`POST /v1/embeddings`) - Represent text as a vector.

### Evals

- [ ] **[Create eval](https://developers.openai.com/api/reference/resources/evals/methods/create)** (`POST /v1/evals`) - Structure an evaluation.
- [ ] **[Retrieve eval](https://developers.openai.com/api/reference/resources/evals/methods/retrieve)** (`GET /v1/evals/{id}`) - Gets evaluation details.
- [ ] **[Update eval](https://developers.openai.com/api/reference/resources/evals/methods/update)** (`POST /v1/evals/{id}`) - Updates evaluation properties.
- [ ] **[Delete eval](https://developers.openai.com/api/reference/resources/evals/methods/delete)** (`DELETE /v1/evals/{id}`) - Deletes an evaluation.
- [ ] **[List evals](https://developers.openai.com/api/reference/resources/evals/methods/list)** (`GET /v1/evals`) - Lists evals in a project.
- [ ] **[Create run](https://developers.openai.com/api/reference/resources/evals/subresources/runs/methods/create)** (`POST /v1/evals/{id}/runs`) - Starts an evaluation run.
- [ ] **[List output items](https://developers.openai.com/api/reference/resources/evals/subresources/runs/subresources/output_items/methods/list)** (`GET /v1/evals/runs/{id}/output_items`) - Lists items from a run.



### Batches

- [ ] **[Create batch](https://developers.openai.com/api/reference/resources/batches/methods/create)** (`POST /v1/batches`) - Executes a batch of requests.
- [ ] **[Retrieve batch](https://developers.openai.com/api/reference/resources/batches/methods/retrieve)** (`GET /v1/batches/{id}`) - Gets batch status.
- [ ] **[List batches](https://developers.openai.com/api/reference/resources/batches/methods/list)** (`GET /v1/batches`) - Lists organization batches.
- [ ] **[Cancel batch](https://developers.openai.com/api/reference/resources/batches/methods/cancel)** (`POST /v1/batches/{id}/cancel`) - Cancels an active batch.

### Files

- [ ] **[List files](https://developers.openai.com/api/reference/resources/files/methods/list)** (`GET /v1/files`) - Lists uploaded files.
- [ ] **[Upload file](https://developers.openai.com/api/reference/resources/files/methods/create)** (`POST /v1/files`) - Uploads a file for use in APIs.
- [ ] **[Retrieve file](https://developers.openai.com/api/reference/resources/files/methods/retrieve)** (`GET /v1/files/{id}`) - Gets file metadata.
- [ ] **[Delete file](https://developers.openai.com/api/reference/resources/files/methods/delete)** (`DELETE /v1/files/{id}`) - Deletes a file.
- [ ] **[Retrieve content](https://developers.openai.com/api/reference/resources/files/methods/content)** (`GET /v1/files/{id}/content`) - Downloads file contents.

### Uploads

- [ ] **[Create upload](https://developers.openai.com/api/reference/resources/uploads/methods/create)** (`POST /v1/uploads`) - Starts a multi-part upload.
- [ ] **[Cancel upload](https://developers.openai.com/api/reference/resources/uploads/methods/cancel)** (`POST /v1/uploads/{id}/cancel`) - Cancels an upload.
- [ ] **[Complete upload](https://developers.openai.com/api/reference/resources/uploads/methods/complete)** (`POST /v1/uploads/{id}/complete`) - Finalizes an upload.
- [ ] **[Add upload part](https://developers.openai.com/api/reference/resources/uploads/subresources/parts/methods/create)** (`POST /v1/uploads/{id}/parts`) - Adds a part to an upload.

### Models

- [ ] **[Retrieve model](https://developers.openai.com/api/reference/resources/models/methods/retrieve)** (`GET /v1/models/{id}`) - Gets model details.
- [ ] **[Delete model](https://developers.openai.com/api/reference/resources/models/methods/delete)** (`DELETE /v1/models/{id}`) - Deletes a fine-tuned model.
- [ ] **[List models](https://developers.openai.com/api/reference/resources/models/methods/list)** (`GET /v1/models`) - Lists available models.

### Moderations

- [ ] **[Create moderation](https://developers.openai.com/api/reference/resources/moderations/methods/create)** (`POST /v1/moderations`) - Checks content for violations.

### Vector Stores

- [ ] **[Create vector store](https://developers.openai.com/api/reference/resources/vector_stores/methods/create)** (`POST /v1/vector_stores`) - Creates a vector store.
- [ ] **[Retrieve vector store](https://developers.openai.com/api/reference/resources/vector_stores/methods/retrieve)** (`GET /v1/vector_stores/{id}`) - Gets store details.
- [ ] **[Update vector store](https://developers.openai.com/api/reference/resources/vector_stores/methods/update)** (`POST /v1/vector_stores/{id}`) - Modifies a store.
- [ ] **[Delete vector store](https://developers.openai.com/api/reference/resources/vector_stores/methods/delete)** (`DELETE /v1/vector_stores/{id}`) - Deletes a store.
- [ ] **[List vector stores](https://developers.openai.com/api/reference/resources/vector_stores/methods/list)** (`GET /v1/vector_stores`) - Lists stores.
- [ ] **[Create store file](https://developers.openai.com/api/reference/resources/vector_stores/subresources/files/methods/create)** (`POST /v1/vector_stores/{id}/files`) - Adds a file to a store.
- [ ] **[Create file batch](https://developers.openai.com/api/reference/resources/vector_stores/subresources/file_batches/methods/create)** (`POST /v1/vector_stores/{id}/file_batches`) - Adds multiple files.

### ChatKit

- [ ] **[Create session](https://developers.openai.com/api/reference/resources/beta/subresources/chatkit/subresources/sessions/methods/create)** (`POST /v1/chatkit/sessions`) - Starts a ChatKit session.
- [ ] **[Cancel session](https://developers.openai.com/api/reference/resources/beta/subresources/chatkit/subresources/sessions/methods/cancel)** (`POST /v1/chatkit/sessions/{id}/cancel`) - Ends a session.
- [ ] **[Retrieve thread](https://developers.openai.com/api/reference/resources/beta/subresources/chatkit/subresources/threads/methods/retrieve)** (`GET /v1/chatkit/threads/{id}`) - Fetches thread info.
- [ ] **[Delete thread](https://developers.openai.com/api/reference/resources/beta/subresources/chatkit/subresources/threads/methods/delete)** (`DELETE /v1/chatkit/threads/{id}`) - Deletes a thread.
- [ ] **[List items](https://developers.openai.com/api/reference/resources/beta/subresources/chatkit/subresources/threads/methods/list_items)** (`GET /v1/chatkit/threads/{id}/items`) - Lists thread items.

### Containers

- [ ] **[Create container](https://developers.openai.com/api/reference/resources/containers/methods/create)** (`POST /v1/containers`) - Creates a container.
- [ ] **[Retrieve container](https://developers.openai.com/api/reference/resources/containers/methods/retrieve)** (`GET /v1/containers/{id}`) - Gets container details.
- [ ] **[List containers](https://developers.openai.com/api/reference/resources/containers/methods/list)** (`GET /v1/containers`) - Lists containers.

### Skills

- [ ] **[Create skill](https://developers.openai.com/api/reference/resources/skills/methods/create)** (`POST /v1/skills`) - Creates a new skill.
- [ ] **[Retrieve skill](https://developers.openai.com/api/reference/resources/skills/methods/retrieve)** (`GET /v1/skills/{id}`) - Gets skill details.
- [ ] **[List skills](https://developers.openai.com/api/reference/resources/skills/methods/list)** (`GET /v1/skills`) - Lists organization skills.
- [ ] **[Create version](https://developers.openai.com/api/reference/resources/skills/subresources/versions/methods/create)** (`POST /v1/skills/{id}/versions`) - Creates a skill version.

### Realtime

- [ ] **[Create secret](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create)** (`POST /v1/realtime/client_secrets`) - Creates a client secret.
- [ ] **[Accept call](https://developers.openai.com/api/reference/resources/realtime/subresources/calls/methods/accept)** (`POST /v1/realtime/calls/{id}/accept`) - Accepts a SIP call.
- [ ] **[Hang up call](https://developers.openai.com/api/reference/resources/realtime/subresources/calls/methods/hangup)** (`POST /v1/realtime/calls/{id}/hangup`) - Ends an active call.

---

## 5. Administration

- [ ] **[Audit Logs](https://developers.openai.com/api/reference/resources/organization/subresources/audit_logs/methods/list)**
- [ ] **[Usage/Costs](https://developers.openai.com/api/reference/resources/organization/subresources/audit_logs/methods/get_costs)**
- [ ] **[Users](https://developers.openai.com/api/reference/resources/organization/subresources/users/methods/list)**
- [ ] **[Invites](https://developers.openai.com/api/reference/resources/organization/subresources/invites/methods/list)**
- [ ] **[Groups](https://developers.openai.com/api/reference/resources/organization/subresources/groups/methods/list)**
- [ ] **[Certificates](https://developers.openai.com/api/reference/resources/organization/subresources/certificates/methods/list)**
- [ ] **[Projects](https://developers.openai.com/api/reference/resources/organization/subresources/projects/methods/list)**
- [ ] **[API Keys](https://developers.openai.com/api/reference/resources/organization/subresources/projects/subresources/api_keys/methods/list)**
- [ ] **[Rate Limits](https://developers.openai.com/api/reference/resources/organization/subresources/projects/subresources/rate_limits/methods/update_rate_limit)**

---

## 6. Chat Completions

- [ ] **[Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)** (`POST /v1/chat/completions`) - Generates a model response.

---
