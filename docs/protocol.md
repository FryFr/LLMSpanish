# ElectronBot-ES — WebSocket Protocol v1

**Status: FROZEN (Day 2).** This contract is immutable. Firmware, MockESP32, and orchestrator all depend on it. Breaking changes require `v2` and a version negotiation on `session.start`.

## Transport

- Single persistent WebSocket connection per device session
- Endpoint: `ws://<host>:<port>/ws/voice` (TLS `wss://` in production)
- Frame types are mixed:
  - **Text frames** → JSON control messages (UTF-8)
  - **Binary frames** → raw PCM audio (int16 little-endian, mono)
- There is no framing header on binary frames. The **stream they belong to** is declared by the most recent control message (`audio.start` from client, `tts.start` from server).

## Audio format

- PCM 16-bit signed little-endian, mono
- **Client → server (mic)**: 16000 Hz
- **Server → client (TTS)**: 16000 Hz nominal. Server MAY send other sample rates if declared in `tts.start.sample_rate`. Client must honor that field.
- Chunk size guidance: 20–100 ms per frame (320–1600 samples at 16kHz). Smaller chunks = lower latency, more overhead.

## Session lifecycle

```
client                                  server
  │                                       │
  ├── session.start ────────────────────▶ │
  │ ◀──────────────────── session.ready ──┤
  │                                       │
  ├── audio.start ─────────────────────▶ │
  ├── [binary PCM frame] ──────────────▶ │
  ├── [binary PCM frame] ──────────────▶ │
  │ ◀──────────────── stt.partial ───────┤  (interim transcripts)
  ├── [binary PCM frame] ──────────────▶ │
  ├── audio.end ───────────────────────▶ │
  │ ◀──────────────── stt.final ─────────┤
  │ ◀──────────────── llm.status ────────┤  ("thinking")
  │ ◀──────────────── tts.start ─────────┤
  │ ◀──────────────── [binary PCM] ──────┤
  │ ◀──────────────── [binary PCM] ──────┤
  │ ◀──────────────── tts.end ───────────┤
  │ ◀──────────────── metrics.turn ──────┤
  │                                       │
  ├── (user barges in)                    │
  ├── tts.cancel ──────────────────────▶ │
  ├── audio.start ─────────────────────▶ │
  │         ... next turn ...             │
  │                                       │
  ├── session.end ─────────────────────▶ │
  │ ◀─────────────── (close) ─────────────┤
```

## Message reference

Every JSON control message has `"type"` as the discriminator. All other fields are namespaced by convention.

### Client → Server

#### `session.start`
First message on the connection. Server must reply with `session.ready` before the client sends audio.

```json
{
  "type": "session.start",
  "protocol_version": "1.0",
  "device_id": "mock-esp32-001",
  "user_id": null,
  "auth_token": null,
  "locale": "es-419",
  "audio_in": {"sample_rate": 16000, "encoding": "pcm_s16le", "channels": 1},
  "audio_out": {"sample_rate": 16000, "encoding": "pcm_s16le", "channels": 1},
  "capabilities": {
    "ack_filler_local": true,
    "barge_in": true,
    "display": false,
    "servos": false
  }
}
```

- `user_id`, `auth_token`: **optional in Week 1** (local dev). **Required from Week 5** when Supabase auth is wired. Server validates only if `auth.enabled: true` in `config.yaml`.
- `ack_filler_local`: client played a local "mmh/beep" sound right after wake word detection — server can skip its own acknowledge.
- `capabilities.display`, `servos`: reserved for Week 3 firmware; ignored by Week 1 backend.

#### `audio.start`
Declares that binary frames arriving next belong to a mic audio stream.

```json
{"type": "audio.start", "turn_id": "t_01HXYZ", "timestamp_ms": 1712345678000}
```

Binary frames MUST follow until `audio.end`. Server interprets every binary frame between `audio.start` and `audio.end` as PCM from the mic for that turn.

#### `audio.end`
End of mic stream for the current turn. Server will finalize STT, run LLM, and respond with TTS.

```json
{"type": "audio.end", "turn_id": "t_01HXYZ"}
```

#### `tts.cancel`
Barge-in. Client MUST send this the moment its local VAD detects user speech while TTS is playing. Server propagates cancel to TTS provider and stops sending `tts.chunk` binary frames for that `turn_id`.

```json
{"type": "tts.cancel", "turn_id": "t_01HXYZ", "reason": "barge_in"}
```

`reason` ∈ `{"barge_in", "user_cancel", "timeout"}`.

#### `session.end`
Graceful disconnect. Server flushes metrics and closes.

```json
{"type": "session.end"}
```

### Server → Client

#### `session.ready`
Reply to `session.start`. Client must not send audio until this arrives.

```json
{
  "type": "session.ready",
  "session_id": "s_01HABC",
  "protocol_version": "1.0",
  "server_version": "0.1.0"
}
```

#### `stt.partial`
Interim transcript (not final). Client can display it in real time but should not act on it.

```json
{
  "type": "stt.partial",
  "turn_id": "t_01HXYZ",
  "text": "hola cómo",
  "confidence": null
}
```

#### `stt.final`
Final transcript for the turn. This is what feeds the intent router.

```json
{
  "type": "stt.final",
  "turn_id": "t_01HXYZ",
  "text": "hola cómo estás",
  "confidence": 0.93,
  "language": "es"
}
```

#### `llm.status`
Lifecycle signal for the LLM step (drives face animations on device).

```json
{"type": "llm.status", "turn_id": "t_01HXYZ", "state": "thinking"}
```

`state` ∈ `{"thinking", "generating", "done"}`.

#### `tts.start`
Declares that binary frames arriving next belong to a TTS playback stream.

```json
{
  "type": "tts.start",
  "turn_id": "t_01HXYZ",
  "sample_rate": 16000,
  "encoding": "pcm_s16le",
  "channels": 1,
  "tier": "T3",
  "source": "cartesia"
}
```

`tier` ∈ `{"T1", "T2", "T3"}` — router decision (canned / template / LLM).
`source` identifies the adapter used (for logs/debugging).

#### `tts.end`
End of TTS stream for the turn.

```json
{"type": "tts.end", "turn_id": "t_01HXYZ"}
```

#### `metrics.turn`
Per-turn observability payload. Sent after `tts.end` OR after `tts.cancel`.

```json
{
  "type": "metrics.turn",
  "turn_id": "t_01HXYZ",
  "tier": "T3",
  "latencies_ms": {
    "stt_final": 420,
    "llm_first_token": 280,
    "tts_first_chunk": 150,
    "wake_to_first_audio": 870
  },
  "tokens": {"in": 42, "out": 68},
  "cost_usd": 0.0012,
  "providers": {"stt": "deepgram", "llm": "groq", "tts": "cartesia"}
}
```

All fields except `turn_id` and `tier` are optional — the backend emits what it measured.

#### `error`
Any recoverable or fatal error. Client should surface to user if `fatal: true`.

```json
{
  "type": "error",
  "turn_id": "t_01HXYZ",
  "code": "stt_timeout",
  "message": "Deepgram did not return a final transcript in 3s",
  "fatal": false
}
```

Reserved codes:
- `auth_failed` (fatal) — invalid or missing token, `auth.enabled: true`
- `quota_exceeded` (fatal) — user plan limits hit (Week 5+)
- `stt_timeout` (non-fatal) — STT flush grace exceeded
- `llm_timeout` (non-fatal) — LLM provider did not respond
- `tts_failed` (non-fatal) — TTS provider dropped; server will attempt fallback
- `provider_down` (fatal) — all providers exhausted in the tier
- `protocol_violation` (fatal) — e.g. binary frame received without prior `audio.start`
- `internal` (fatal) — unexpected server error

## Invariants

1. **One turn = one `turn_id`** generated by the client at `audio.start`. Server echoes it in every subsequent message for that turn.
2. **Binary frames are always PCM for the active stream.** There is never a scenario where a binary frame is something else (image, JSON-in-bytes, metadata). If we need other binary payloads in v2, we version-bump.
3. **`audio.start` and `tts.start` cannot overlap on the same `turn_id`.** A turn has exactly one mic capture and at most one TTS playback.
4. **Server never sends audio without a prior `tts.start`.** Client MAY drop any orphan binary frame.
5. **Barge-in is client-initiated.** The server does not try to detect barge-in from the mic stream — the client has the low-latency VAD. Server trusts `tts.cancel`.
6. **Wake word is handled entirely on the device.** Backend only sees audio from `audio.start` onward. The wake word itself is never streamed.
7. **`ack_filler_local: true` means the client already played the "mmh" sound.** Server will not emit its own canned ACK for that turn.

## Multi-tenant hooks (Week 5+ preview)

Week 1 accepts `user_id = null` and `auth_token = null`. Week 5 will flip `config.yaml` `auth.enabled: true`. At that point:

- `auth_token` is a Supabase-issued JWT for the user
- `device_id` must be a paired device registered under that `user_id`
- Server rejects `session.start` with `error.code = auth_failed` if invalid
- `metrics.turn` is persisted to Supabase keyed by `(user_id, device_id, turn_id)`

Nothing else in the protocol changes. Clients written against v1 today will still work in Week 5 as long as they accept `user_id` / `auth_token` fields being filled in.

## Non-goals for v1

- Multi-channel audio (stereo, ambisonic)
- Video or image attachments
- Multi-turn batching in a single WS message
- Compressed audio (Opus) — considered for v2 once we measure LAN/WiFi bandwidth on the ESP32-S3
- Word-level timestamps back to the client
