# How Niklaus was built

A record of where the project came from, what was decided and why, and how each piece was
reviewed. Dates are when the work happened. The project was built by Marco Antonio Garcia working
with Claude (Claude Code) as a pair, with a separate security-review agent auditing every change.

## The idea

A personal AI agent you talk to and that acts on your computer, built the "engineering" way:
private by design, contained in one folder, sandboxed, and audited — as opposed to the
"build your own assistant in a weekend" tutorials that wire cloud services together without
reviewing what runs. Fixed decisions from day one:

- The brain is Claude through a flat monthly subscription. No API keys, nothing billed per use.
- The owner's video and audio never leave the machine; no cloud speech or vision engines.
- Everything lives in one folder; whatever must live outside is inventoried and uninstallable.
- Ask the owner before installing outside the folder, deleting anything or using browser cookies.

## 2026-11-11 — Video analysis, from audit to a sandboxed tool

1. **Audit of `bradautomates/claude-video`** at commit `03ceb42`. No malicious code. Two risks
   neutralized: an `auto` engine that silently uses Gemini or Groq/OpenAI if it finds an API key
   (even in a local `.env`), and a `setup.py` that installs outside the project folder (never run).
2. **Local stack:** FFmpeg, a Python virtual environment with WhisperX (CPU, int8) and the `small`
   model, all caches inside the project folder.
3. **Telemetry incident.** `onnxruntime` (a dependency of pyannote, pulled in by WhisperX) ships
   telemetry enabled by default and sent usage metadata (not audio) on the first run. It was found
   in macOS's CFNetwork cache and fixed in layers: environment flags, pyannote metrics off, a dead
   proxy and, finally, a kernel-enforced no-network sandbox. Lesson: inspect compiled binaries of
   every new dependency, not just its Python code.
4. **Two-phase sandbox wrapper.** Download with network but confined reads/writes; analysis with
   no network at all. Clean environment, pinned upstream, integrity checks.
5. **Four security review passes**, each rejecting and each fixed:
   - pass 1: broad write access and tokens leaking through the environment;
   - pass 2: the download phase could read the whole home folder;
   - pass 3: it could read outside home, a child process could escape the kill, and a
     shared-memory/pasteboard channel existed between phases;
   - pass 4: a persistent channel through extended attributes and file sizes in the output folder,
     and through preferences and notifications.

   A test suite grew to 93 checks. Residual covert channels (disk space, CPU load, timing) were
   explicitly accepted.
6. **Token-lean routine mode:** deduplicated scene frames packed into contact sheets, a read-only
   report-writer subagent, a mandatory report format, and an automatic cleanup that only deletes
   what each run recorded.
7. **More hardening after review:** symlink-proof writes, per-platform URL index, a pinned and
   hash-checked Deno for YouTube, read-only quarantine of a GitHub repo mentioned by a coding
   tutorial, and an orphan-process sweep that fails closed. Tests: 127.

## 2026-11-13 — Accuracy, the desktop app and auditability

1. **Transcription accuracy.** A long initial prompt made Whisper echo the video description and
   drop real speech. Fix: a short prompt (title and technical terms only) plus automatic
   re-transcription without the prompt of gaps, sparse segments and echoes, marked in the
   transcript. In a real test, a 16 s segment went from 5 to 61 recovered words.
2. **Key frames.** Saved frames sometimes showed a different scene than described. Root cause:
   contact-sheet labels are rounded to the second. Fix: pick the moment that matches the scene the
   writer saw, reject near-empty frames, and verify every saved frame against its description.
3. **Local MCP server** for the Claude desktop app (7 tools, one analysis at a time, untrusted
   content marked, symlink-proof reads), plus a tool to discard abandoned runs.
4. **Session log** for the project's orchestrator, hardened after review: answers are quoted
   between nonce markers so they cannot forge entries.
5. **Every change reviewed:** each of these steps got a security review that rejected the first
   version (findings such as a symlink-planting path, a wrong root-cause assumption, a run being
   attributed to the wrong job, a config file losing its permissions), and each finding was fixed
   and re-tested.
6. **Code freeze** on the video tool (bug fixes only) until the desktop integration was finished.
7. **Publication:** this repository, with documentation first and code after generalization and
   review.

## Ideas reviewed along the way

Short videos and open-source projects about AI assistants were analyzed with the tool itself and
judged against the principles. What was adopted and what was rejected is listed in
[credits.md](credits.md).
