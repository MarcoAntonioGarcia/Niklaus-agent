# Niklaus

**Your personal voice AI agent. It acts on your computer — local, sandboxed and audited.**

> **Status: early, work in progress.** This repository currently holds the project's documentation and
> history. The code is being cleaned up, generalized and security-reviewed before it is published here.

Niklaus is a personal AI agent you talk to and that can actually do things on your computer:
watch and analyze videos, read and write files, run tools and, step by step, connect to the rest
of your digital life. Its difference is **how** it does it: every capability runs locally, inside
macOS sandboxes, with untrusted content treated as data (never as instructions), and every change
goes through an independent security review before it ships.

## Principles

1. **One brain, no pay-per-use.** The agent's reasoning runs on Claude through a flat monthly
   subscription (Claude Code and the Claude desktop app). No API keys, nothing billed per call.
2. **Your media never leaves your machine.** Video and audio are processed locally (FFmpeg,
   WhisperX). No cloud speech or vision engines.
3. **Sandboxed by default.** Anything that touches untrusted content runs in a kernel-enforced
   macOS sandbox: downloads in one phase with network, analysis in another phase with no network
   at all.
4. **Untrusted content is data.** Transcripts, captions, titles, web pages and reports are marked
   as untrusted and handled by read-only components, so a malicious video cannot steer the agent.
5. **Audited.** Every new tool, dependency or configuration change gets an independent security
   review; findings are fixed and re-tested before they are reported as done. Dependencies are
   pinned and inspected for network calls and telemetry.
6. **Contained.** Everything lives in one folder. Anything that must live outside it is recorded in
   an inventory and removed by the uninstaller.
7. **Yours.** Each user chooses the agent's name and, on the roadmap, enrolls the voices allowed
   to activate it (local speaker verification).

## What it can do today

- **Video analysis (`darkroom`)** — give it an Instagram, YouTube or TikTok link (or a local
  file) and it downloads, extracts key frames, transcribes speech locally and produces a structured,
  extraction-only report (claims, resources, structure, key frames, full transcript), then cleans up
  after itself. the video tool, [Darkroom](https://github.com/MarcoAntonioGarcia/darkroom), lives in its own repository; Niklaus uses it
  through `herramientas.toml`, its registry of tools.
- **Local MCP server** — exposes the video analysis to the Claude desktop app (stdio, 7 tools,
  one analysis at a time, untrusted content clearly marked).

- **Desktop voice app** — say its name and talk: it answers aloud and acts on the computer through
  reversible file tools (everything can be undone, deleting means the macOS Trash) and commands gated by
  an allowlist policy; anything hard to undo asks first, by voice and on screen. Speech-to-text and
  text-to-speech are local.

### Install

macOS on Apple Silicon. See [INSTALL.md](INSTALL.md): prerequisites, then `scripts/instalar.sh`.

### Make it yours

The repository holds only the generic agent. What is personal stays out of git:

- `local/` — your job, your tools and your preferences (start from [`local.ejemplo/`](local.ejemplo/README.md)).
- `skills.toml` — your catalog of skills (start from `skills.ejemplo.toml`).
- `CLAUDE.md` — your own standing instructions for Claude Code in this folder.

See [docs/architecture.md](docs/architecture.md) for how it works and
[docs/history.md](docs/history.md) for how it was built.

## Roadmap

- Voice activation (wake word and/or double clap) with configurable name and enrolled voices.
- Read-only status panel (desktop app).
- Messaging channel (e.g. Telegram) restricted to its owner, with confirmation for any action.
- Research tools (simulation jobs, paper index, arXiv watcher) and content skills.
- Hooks that enforce the rules automatically and keep an action audit trail.
- Packaging each capability as an installable Claude Code plugin, usable on its own or together.

## Repository layout (planned)

```
niklaus-agent/
├── herramientas.toml         registry of the tools Niklaus uses (adding a tool = adding an entry)
└── docs/                     architecture, history, credits
```

Tools such as the video analysis (skill, sandboxed wrapper, tools, MCP server) live in their own
repositories and folders.

## Credits

Built on the shoulders of open-source work — see [docs/credits.md](docs/credits.md). In particular,
the video analysis started from an audit of
[bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT).

## Disclaimer

- Provided **"as is"**, without warranty of any kind (see [LICENSE](LICENSE)).
- Downloading content from third-party platforms may be subject to their terms of service; use it
  for personal analysis and respect creators' rights. You are responsible for how you use it.
- Not affiliated with or endorsed by Anthropic. "Claude" is a trademark of Anthropic.
- Security issues: please follow [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 Marco Antonio Garcia
