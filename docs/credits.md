# Credits and sources

## Code and tools we build on

| Project | License | How it is used |
|---|---|---|
| [bradautomates/claude-video](https://github.com/bradautomates/claude-video) | MIT | Starting point of the video analysis. Audited at commit `03ceb42`; it is **not copied** into this repository — the wrapper calls a pinned, verified clone. |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | Downloads (Phase A, sandboxed). |
| [FFmpeg](https://ffmpeg.org) | LGPL/GPL | Frames and audio (Phases M and B). Installed by the user; not redistributed. |
| [WhisperX](https://github.com/m-bain/whisperX) / faster-whisper | BSD-2 / MIT | Local speech-to-text (Phase B, no network). |
| [Deno](https://deno.com) | MIT | JS challenge for YouTube inside the download sandbox (pinned by SHA-256). Not redistributed. |
| [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MIT | Local MCP server. |
| [Claude](https://www.anthropic.com/claude) (Claude Code, desktop app) | — | The agent's brain, through a flat subscription. Not affiliated. |

## Projects and ideas we reviewed

We analyze public videos and projects, keep what fits the principles and document what we reject
and why. **No code was taken from these projects** unless listed in the table above.

| Source | What we took (idea) | What we rejected and why |
|---|---|---|
| [Jakeschincariol/youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill) (read-only audit) | Ideas for a future viral-content skill (performance relative to a channel's median, hook formulas). | — |
| [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) (Stanford, Apache-2.0) | Idea: double-clap activation; local-first personal AI. | Its install-by-prompt approach that disables Homebrew's tap trust check. No code used. |
| Hermes Agent (Nous Research) | Ideas: memory as markdown files, skills, messaging channels. | Using a Claude subscription through a third-party harness (terms risk); cloud voice. |
| Short videos on "build your own AI assistant" (Instagram, 2026) | Voice, channels (Telegram), a node-graph status panel, plan mode, hooks for auditing, loops with a stop condition, per-task model choice. | Cloud brokers for email/WhatsApp, multi-provider routers that send code to unknown providers, context-compression proxies, cookie-based scrapers. |
| [ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | Design guidance for the future panel. | To be audited and pinned before use. |
| Anthropic official plugins (`claude-code-setup`, `ralph-loop`, `hookify`) | Read-only automation recommender; loop and hook patterns. | Plugins that install SDKs at session start, until audited. |
