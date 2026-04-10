# Hermes Meeting Room Architecture Plan

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task after architectural sign-off.

**Goal:** Build a Slack-launched meeting system where Hermes can participate as a live voice attendee, internal and external guests can join by link, and transcripts/notes are captured as non-ontological source material.

**Architecture:** Slack is the control plane, not the media plane. A Slack app creates and manages an external voice room, Hermes joins that room as a bot participant, a speech pipeline produces live and post-call transcripts, and the resulting artifacts are posted back into Slack plus stored in a source-material store outside the ontology graph.

**Tech Stack:** Slack app (Bolt Python or TypeScript), LiveKit for RTC room + SIP/WebRTC primitives, STT (Deepgram or Whisper), TTS (OpenAI/Cartesia/ElevenLabs-equivalent), Hermes orchestration service, Postgres for meeting metadata, object storage for transcripts/audio, optional diarization, Docker Compose for local deployment.

---

## 1. Executive summary

Native Slack huddles are useful for internal-only calls and Slack AI notes, but they do **not** satisfy the desired end state:

1. external attendees must be supported,
2. Hermes must be able to speak and listen live,
3. transcript retention must be under our control,
4. transcripts must be reusable as non-ontological source material.

The recommended design is therefore a **Slack-triggered external meeting system**:

- users start a meeting from Slack,
- Slack posts a meeting card with a join link,
- the actual media session runs in an external RTC room,
- Hermes joins the RTC room as an agent,
- all participants talk there,
- transcript and notes are written back into the originating Slack thread,
- raw artifacts are stored outside the ontology layer and referenced as source material.

---

## 2. Scope and non-goals

### In scope
- Start meetings from Slack.
- Invite internal or external participants by URL.
- Let Hermes participate live by voice.
- Produce transcript, notes, action items, and summary.
- Persist raw transcript and optional audio recording as source material.
- Keep provenance linking meeting artifacts back to Slack thread, participants, time, and room.

### Out of scope for MVP
- Joining native Slack huddles as an audio participant.
- Calendar scheduling and recurring meetings.
- Full CRM/contact syncing.
- Automatic ontology ingestion from transcript content.
- High-stakes compliance workflows (eDiscovery/legal hold) beyond basic retention controls.

---

## 3. Requirements

### Functional requirements
1. A Slack command or button creates a new meeting tied to a Slack channel/thread.
2. The system returns a browser/mobile-friendly join URL.
3. External participants can join without being members of the Slack workspace.
4. Hermes can hear, speak, and respond in real time.
5. A transcript is generated with timestamps and speaker labels where possible.
6. A post-call summary, decisions, and action items are posted into Slack.
7. The transcript is marked and stored as **non-ontological source material**.
8. Access to historical artifacts is permissioned and auditable.

### Non-functional requirements
- Mobile-friendly join flow.
- Tailnet/private deployment where feasible for internal control surfaces.
- Clear consent indicators for recording/transcription.
- Recoverable if Slack is unavailable after room creation.
- Low enough latency for interactive voice conversation with Hermes.

---

## 4. Recommended system design

## 4.1 Control plane vs media plane

### Control plane: Slack
Slack handles:
- meeting creation intent,
- posting join cards and updates,
- optional slash commands and buttons,
- meeting summaries and links,
- the primary collaboration thread.

Slack does **not** carry the primary call audio for Hermes.

### Media plane: external RTC provider
An external provider handles:
- room creation,
- participant connectivity,
- browser/mobile media transport,
- optional recording,
- Hermes bot media ingress/egress.

This is the key architectural split that makes external guests and live Hermes participation possible.

---

## 4.2 Recommended vendor choice

### Recommendation: LiveKit first
Use **LiveKit** as the primary RTC layer.

Why:
- built for real-time audio/video agents,
- good server-side participant support,
- strong OSS/self-hosting path if desired later,
- straightforward room/token model,
- good fit for a programmable voice bot.

### Alternatives
- **Daily**: simpler hosted product, fast to MVP, less infra burden.
- **Twilio Voice/Video**: enterprise-grade, but usually more expensive and operationally heavier.

### Proposed decision
- **MVP:** Hosted LiveKit Cloud or Daily.
- **Preferred longer-term default:** LiveKit due to agent-friendliness and migration flexibility.

---

## 4.3 Core components

### A. Slack app service
Responsibilities:
- slash command `/hermes-meeting`
- optional message action / shortcut
- creates meeting record in DB
- requests room creation from meeting backend
- posts join message into channel/thread
- posts lifecycle updates (started, active, ended, summary ready)

Suggested stack:
- Bolt for Python if we want close alignment with existing Python-oriented orchestration
- or Bolt JS/TS if we want tighter frontend/shared-schema ergonomics

### B. Meeting backend API
Responsibilities:
- create room
- mint participant tokens
- manage invites
- expose webhooks for join/leave/start/end
- coordinate transcript, summary, artifact storage
- generate signed artifact URLs

Suggested endpoints:
- `POST /api/meetings`
- `POST /api/meetings/:id/invites`
- `POST /api/meetings/:id/end`
- `GET /api/meetings/:id`
- `GET /api/meetings/:id/artifacts`

### C. RTC/voice room
Responsibilities:
- browser join URL for humans
- server-side or agent-side connection for Hermes
- optional video/screenshare later
- recording hooks if enabled

### D. Hermes voice agent service
Responsibilities:
- join room as a participant named Hermes
- consume audio stream
- perform turn detection / interruption handling
- call STT and LLM reasoning
- synthesize speech back to room
- optionally answer text-side prompts from the Slack thread during call

### E. Transcript pipeline
Responsibilities:
- live partial transcription
- finalized transcript with timestamps
- diarization or participant labeling
- transcript normalization and redaction hooks
- chunking into source-material records

### F. Artifact store
Responsibilities:
- raw transcript JSON/text
- optional audio recording
- generated summary and action items
- provenance metadata
- retention lifecycle rules

Suggested storage split:
- **Postgres**: meeting metadata, participant records, artifact index, source-material registry
- **Object storage**: transcript blobs, recording files, summary exports

---

## 5. End-to-end user flow

## 5.1 Meeting creation flow
1. User runs `/hermes-meeting Strategy review with supplier` in Slack.
2. Slack app creates a meeting record.
3. Meeting backend creates RTC room and invite token/link.
4. Slack app posts into channel/thread:
   - topic/title
   - join button/link
   - note that external guests are allowed by URL
   - note that meeting is transcribed and summarized
5. Hermes pre-joins or joins on first human attendee.

## 5.2 External invite flow
1. Host clicks “Copy guest link” or runs `/hermes-meeting invite`.
2. Backend mints a guest join URL.
3. Guest opens link in browser on phone/laptop.
4. Guest enters display name and consents to transcription.
5. Guest joins room without joining Slack.

## 5.3 In-call flow
1. Participant speaks.
2. Audio flows to RTC provider.
3. STT produces interim/final transcript.
4. Hermes consumes transcript + optional live audio cues.
5. Hermes responds by TTS into the room.
6. Notes/action items accumulate in backend state.
7. Slack thread can optionally receive periodic updates or a “live notes” link.

## 5.4 Post-call flow
1. Room ends.
2. Transcript finalization runs.
3. Summary, action items, attendee list, and decisions are generated.
4. Raw transcript is stored in source-material storage as non-ontological input.
5. Slack thread receives:
   - concise summary
   - action items
   - links to full transcript/artifacts
   - provenance ID/reference

---

## 6. Transcript and source-material model

## 6.1 Guiding rule
Meeting transcripts are **source material**, not ontology facts.

They should be stored as:
- raw or lightly normalized text,
- provenance-rich records,
- optionally chunked for retrieval,
- explicitly separate from validated ontology assertions.

## 6.2 Proposed metadata fields
Each transcript artifact should include at least:
- `meeting_id`
- `source_type = meeting_transcript`
- `classification = non_ontological_source_material`
- `slack_channel_id`
- `slack_thread_ts`
- `room_provider`
- `room_id`
- `started_at`
- `ended_at`
- `participants`
- `external_participants_present`
- `recording_enabled`
- `transcription_provider`
- `summary_version`
- `consent_mode`
- `artifact_urls`

## 6.3 Storage pattern
Recommended:
- Store raw transcript text and JSON separately.
- Store speaker-timestamp segments for retrieval.
- Store generated summary as a derived artifact, not a replacement for transcript.
- Keep immutable raw copy plus redacted view if policy requires edits.

---

## 7. Security, privacy, and consent

### Minimum required controls
- clear transcription notice in Slack card and join page,
- explicit consent on guest join page,
- authenticated host controls to end room/remove guest,
- signed artifact URLs with expiration,
- retention policy configuration,
- audit logs for artifact access.

### Recommended posture for MVP
- transcript on by default only for host-created meetings that declare recording/transcription,
- optional audio recording disabled initially unless clearly needed,
- external guest links are long random URLs with optional one-time or expiring access,
- role split between host, internal participant, and guest.

### Key privacy decision to make before implementation
Decide whether Hermes is allowed to retain:
- transcript only,
- transcript + audio,
- transcript + summary only after redaction.

---

## 8. Accounts and external services required

This is the checklist to create before implementation.

## 8.1 Required external accounts for MVP

### 1. Slack app / Slack workspace admin access
Needed for:
- creating the Slack app,
- slash commands,
- bot installation,
- posting meeting cards,
- interactive buttons,
- event subscriptions if needed.

Artifacts to gather:
- workspace admin who can install the app,
- Slack app credentials,
- signing secret,
- bot token,
- allowed redirect URLs if OAuth is used.

### 2. RTC provider account
**Recommended:** LiveKit Cloud account.

Needed for:
- room creation,
- access tokens,
- participant connectivity,
- webhook events,
- possible recording/egress later.

Artifacts to gather:
- API key/secret,
- room region choice,
- usage/billing owner,
- webhook signing secret if used.

### 3. Speech-to-text provider account
Pick one:
- **Deepgram** for low-latency hosted STT, or
- self-hosted/open pipeline later via Whisper.

Needed for:
- live transcription,
- finalized transcript.

Artifacts to gather:
- API key,
- model choice,
- pricing/usage limits,
- data retention settings.

### 4. Text-to-speech provider account
Pick one provider that sounds acceptable for meetings.

Candidates:
- OpenAI TTS
- Cartesia
- ElevenLabs

Needed for:
- Hermes spoken responses in room.

Artifacts to gather:
- API key,
- selected voice,
- pricing/latency expectations,
- commercial usage terms.

### 5. LLM provider credentials for live reasoning
Could be existing OpenAI-compatible credentials already used by Hermes, but verify suitability for:
- low-latency turn-taking,
- tool-calling during meetings,
- stable streaming responses.

Artifacts to gather:
- model choice,
- streaming support,
- budget owner,
- fallback model.

### 6. Persistent database
**Recommended:** managed Postgres.

Needed for:
- meeting records,
- participant records,
- artifact index,
- source-material metadata.

Artifacts to gather:
- connection string,
- backup plan,
- migration workflow,
- owner.

### 7. Object storage account
**Recommended:** S3-compatible bucket (AWS S3, Cloudflare R2, MinIO, Backblaze B2).

Needed for:
- transcript files,
- summary exports,
- optional audio recordings.

Artifacts to gather:
- bucket name,
- access keys,
- lifecycle/retention rules,
- encryption settings.

## 8.2 Optional external accounts

### Email provider or transactional messaging
Needed only if guests should receive emailed invite links directly from the system.

### Auth provider
Needed only if we want participant sign-in beyond simple guest links.

### Error monitoring
Examples: Sentry, Bugsnag.

### Analytics / product telemetry
Needed only if we want room quality and adoption metrics early.

---

## 9. Recommended MVP stack decision matrix

### Fastest MVP
- Slack app: Bolt Python
- RTC: Daily
- STT: Deepgram
- TTS: OpenAI or Cartesia
- DB: Postgres
- Blob store: S3-compatible

Pros:
- quickest to first call
- fewer infra decisions

Cons:
- slightly less aligned with long-term self-hosting/control

### Best strategic fit
- Slack app: Bolt Python
- RTC: LiveKit
- STT: Deepgram initially, Whisper later if needed
- TTS: provider chosen by voice quality/latency test
- DB: Postgres
- Blob store: S3-compatible

Pros:
- strongest fit for programmable Hermes participation
- cleaner path to deeper agent behavior

Cons:
- modestly more architecture work up front

### Recommendation
Use the **Best strategic fit** stack unless speed to first demo is the only priority.

---

## 10. Deployment model

### Preferred initial deployment
Deploy the app backend on the Lenovo host with Docker Compose, keeping admin surfaces private and exposing only what external guests need.

Suggested split:
- internal ops/admin UI: Tailnet/private only
- public guest join page + webhook endpoints: public HTTPS endpoint
- media transport: managed RTC provider handles difficult networking

### Why this split
- preserves your preference for private internal software,
- minimizes raw internet exposure on the host,
- lets external guests still join meetings reliably.

### Exposure requirement
A purely Tailnet-only deployment will **not** work for outside guests unless they are also on the Tailnet. Therefore, guest join traffic must use either:
- a managed RTC provider join URL, or
- a public reverse proxy/domain for the meeting frontend.

---

## 11. Risks and mitigations

### Risk: voice latency feels awkward
Mitigation:
- select low-latency STT/TTS providers,
- stream partial transcripts,
- cap Hermes response length in live mode,
- support barge-in/interrupt handling.

### Risk: guest invite abuse
Mitigation:
- expiring links,
- waiting room or host admit control later,
- optional per-invite passcode.

### Risk: transcript privacy concerns
Mitigation:
- explicit consent,
- retention controls,
- artifact access logs,
- redaction workflow.

### Risk: provider lock-in
Mitigation:
- isolate RTC/STT/TTS adapters behind internal interfaces,
- store provider-agnostic transcript schema.

---

## 12. Architecture decisions to confirm before implementation

1. **RTC provider:** LiveKit or Daily?
2. **STT provider:** Deepgram first, or self-hosted Whisper later?
3. **TTS provider:** which voice should represent Hermes in meetings?
4. **Recording policy:** transcript only, or transcript + audio?
5. **Guest access model:** anonymous guest links or verified guest identity?
6. **Public endpoint strategy:** own domain/reverse proxy vs provider-hosted meeting frontend?
7. **Storage location:** existing Hadto infrastructure vs new isolated meeting data store?

---

## 13. Proposed phased implementation plan

### Phase 0: architectural sign-off
- confirm provider choices
- confirm retention/privacy posture
- create all required external accounts
- allocate budget owner and secrets handling path

### Phase 1: MVP meeting loop
- Slack command creates external room
- internal/external guests join by link
- Hermes joins and speaks live
- transcript captured
- summary posted to Slack thread
- transcript stored as source material

### Phase 2: workflow hardening
- artifact access controls
- better diarization
- expiring invites
- signed downloads
- retry/recovery logic
- ops dashboard card for meeting health

### Phase 3: business workflow integration
- transcript -> backlog suggestions
- transcript -> decision register
- transcript -> evidence pack for ontology review without direct ontology insertion
- recurring meeting templates

---

## 14. Concrete recommendation

Proceed with a **Slack-launched external meeting architecture** rather than native Slack huddles.

### Recommended MVP choices
- **RTC:** LiveKit Cloud
- **STT:** Deepgram
- **TTS:** test OpenAI vs Cartesia and pick lower-latency/better intelligibility
- **App backend:** Bolt Python + Python meeting backend
- **Storage:** Postgres + S3-compatible object store
- **Deployment:** Lenovo-hosted control plane, managed public media/join plane

This gives the desired outcome with the least architectural compromise:
- outside guests supported,
- live voice interaction with Hermes,
- Slack remains the coordination surface,
- transcript artifacts remain reusable non-ontological source material.

---

## 15. Account setup checklist

### Must have before coding
- [ ] Slack app admin/install access
- [ ] RTC provider account (LiveKit Cloud recommended)
- [ ] STT provider account
- [ ] TTS provider account
- [ ] LLM provider/model decision for live voice mode
- [ ] Postgres instance
- [ ] S3-compatible bucket
- [ ] Secret management path for all keys
- [ ] Public HTTPS endpoint strategy for join/webhook traffic

### Nice to have before beta
- [ ] Error monitoring account
- [ ] Product analytics/telemetry account
- [ ] Transactional email provider for guest invites

---

## 16. Sign-off questions for David

1. Are you comfortable with a **public guest join URL** if internal admin surfaces stay private?
2. Should the MVP store **transcript only**, or also **audio recordings**?
3. Do you want external guests to join by simple magic link, or should they authenticate somehow?
4. Is **LiveKit Cloud** acceptable for MVP, or do you want to stay closer to self-hosting from day one?
5. Should post-call artifacts land only in Slack, or also in a local Hadto-facing review UI?
