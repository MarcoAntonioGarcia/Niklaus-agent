# Architecture and security model

This describes the first capability, **video analysis (`darkroom`)**, and the local MCP server
that exposes it. Everything else in Niklaus will follow the same pattern.

## Overview

```
 you / Claude ──► wrapper (trusted shell) ──► Phase A: download   [sandbox WITH network]
                                          ├─► Phase M: merge      [sandbox, NO network]
                                          ├─► Phase B: analyze    [sandbox, NO network]
                                          │      frames (FFmpeg) + speech-to-text (WhisperX, local)
                                          ├─► report writer       [read-only subagent]
                                          └─► save + cleanup      [symlink-proof writes]
```

- **One entry point.** A single wrapper script is the only way to run the analysis. It forces the
  local engine, strips cloud API keys and tokens from the environment (`env -i` with an explicit
  allow-list), pins the upstream code to an audited commit and refuses to run if it changed.
- **Phase A (download)** — `yt-dlp` runs inside a macOS sandbox (`sandbox-exec`) that may use the
  network but can only read the system, its own Python environment and its download folder, and can
  only write there. It can start only its own Python and a pinned, hash-checked Deno binary (needed
  for YouTube's JS challenge). No cookies unless the owner explicitly approves.
- **Phase M (merge)** and **Phase B (analysis)** — FFmpeg and WhisperX run in a second sandbox with
  **no network at all** (TCP, UDP and Unix sockets are denied by the kernel). Phase B can only read
  the models, the tools and the one file it analyzes, and can only write its output folder.
- **Both phases**: no shared memory, semaphores, pasteboard, Apple Events or preference reads;
  signals and process inspection limited to themselves; an explicit list of allowed executables.
- **Orphan sweep** — after every sandboxed step, a sweeper kills processes that carry the
  sandboxes' unique fingerprint and **fails closed** (aborts the run) if they keep reappearing.
- **Symlink-proof writes** — Phase B can plant links in its output folder, so every trusted write
  there walks the path with `O_NOFOLLOW` and creates files with `O_EXCL|O_NOFOLLOW`.

## Transcription quality

- WhisperX runs on CPU (int8), `small` by default and `medium` on demand.
- An initial prompt with only the title plus a short list of technical terms helps spelling
  without injecting whole sentences.
- **Automatic rescue:** gaps longer than 3 s, sparse segments and echoes of the prompt are cut and
  re-transcribed without the prompt, and the recovered text is marked in the transcript.

## Reports

- The report is written by a **read-only subagent** (it can only read files), because the video's
  content may carry prompt injection. Standard format: YAML front matter, claims, resources,
  structure, key frames and full transcript. Extraction only, never judgment.
- Key frames are picked at the exact moment that matches the scene the writer saw (contact-sheet
  labels are rounded to the second), and near-empty frames are rejected; the saved frames are then
  checked against their descriptions.
- Cleanup deletes **only** what the run recorded in its own manifest, in its exact folder, never
  through a link, and never a user's local video.

## Local MCP server (Claude desktop app)

- Python, official MCP SDK, stdio transport. A thin layer: it never duplicates logic; it calls the
  wrapper with fixed argument lists (no shell, no cookies).
- Tools: start an analysis, status, materials (metadata, transcript, contact sheets),
  save report (and clean up), discard a run, list reports and read a report. One analysis at a time.
- Sources are limited to https URLs from Instagram, YouTube and TikTok with known path shapes, or
  files inside folders the owner authorizes. Everything returned from a video or report is wrapped
  in "untrusted data" markers.

## Auditability

- Every change to code or configuration goes through an independent security review (a separate
  agent with its own instructions) before it is reported as done; findings are classified
  (high / medium / low), fixed and re-tested.
- A test suite of more than 120 sandbox checks proves each block (each denial must return `EPERM`).
- A session log records each final answer of the agent for the project's orchestrator.

## Known limitations

- `sandbox-exec` is deprecated by Apple (still functional); a future macOS could remove it.
- The sandbox profiles use `(allow default)` plus deny-lists and allow-lists; an unlisted system
  service that does networking on a process's behalf would not be covered.
- Residual covert channels between phases (free disk space, CPU load, timing) are accepted as very
  low-bandwidth risks.
- The orphan sweep reliably kills processes that fork every few milliseconds, but not a process
  deliberately designed to fork at maximum speed. The planned fix is to run the phases under a
  dedicated macOS user.
