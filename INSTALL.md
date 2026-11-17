# Installing Niklaus

Niklaus is a desktop voice assistant for macOS. You say its name, it answers aloud and acts on your
computer through Claude Code. Speech-to-text and text-to-speech run on your Mac; your audio never
leaves it.

## What you need first

The installer checks these and tells you which one is missing. It does not install them for you.

| Requirement | Why | How |
|---|---|---|
| A Mac with Apple Silicon | The voice runs on MLX. There is no Intel, Linux or Windows version. | — |
| Python 3.11 from python.org | The app and its workers are pinned to it, at `/Library/Frameworks/Python.framework/Versions/3.11`. | <https://www.python.org/downloads/macos/> |
| ffmpeg from Homebrew | Reads the microphone. | `brew install ffmpeg` |
| Claude Code, signed in | It is the brain. Niklaus uses your Claude subscription; it never asks for an API key. | <https://claude.com/claude-code> |
| Darkroom | Niklaus hears with Darkroom's local Whisper models. | Clone it to `~/Apps/darkroom` and follow its `INSTALL.md`: <https://github.com/MarcoAntonioGarcia/Darkroom> |

If Darkroom lives somewhere else, change `ruta` in `herramientas.toml`.

## Install

```bash
git clone https://github.com/MarcoAntonioGarcia/Niklaus-agent.git ~/Apps/Niklaus
cd ~/Apps/Niklaus
scripts/instalar.sh
```

The script writes only inside the folder (it refuses to run if one of its folders is a link) and never
uses `sudo`. It:

1. builds two Python environments in `venv/` from the pinned lists in `requirements/`
   (wheels only, except one small package that is published without a wheel);
2. downloads the voice model (about 1.8 GB) into `models/`, at a fixed revision;
3. creates `data/`, and gives you a starting `skills.toml` and a `local/` folder with its README.

It uses the network only to download from PyPI and Hugging Face (and their file servers), without
your tokens, and only while it runs. Run it again at any
time: it adds what is missing and leaves the rest alone. `scripts/instalar.sh --comprobar` only
reports what is in place.

## Open it

Double-click `app/escritorio/abrir.command`. It opens in Terminal so macOS can apply the microphone
permission; the first time, macOS asks you to allow the microphone for Terminal.

Say **"Niklaus"** followed by what you need, or type in the box. It answers in Spanish.

What it does without asking: reading, searching, opening apps and pages, and moving, renaming or
trashing files (all of that can be undone; deleting always means the macOS Trash). Anything hard to
undo — installing, `sudo`, overwriting, sending, killing processes — shows a card and waits for
your yes, by click or by voice.

## Make it yours

Everything personal lives in files that git ignores:

- **`local/`** — your job, your tools and your preferences. Start from
  [`local.ejemplo/`](local.ejemplo/README.md): a prompt section, permission rules for your own
  command-line tools, the names it wakes up to, and extra nodes for the map on screen.
- **`skills.toml`** — your catalog of Claude Code skills.
- **`CLAUDE.md`** — your standing instructions for Claude Code in this folder.

## Known limits

- The assistant speaks and is prompted in Spanish. Changing the language means editing
  `app/voz/prompt-manos.md` and the texts in `app/escritorio/ui/`.
- With background noise or a quiet microphone the transcription gets worse. Raise the input volume
  in System Settings → Sound, or use an external microphone.
- The on-screen name stays "Niklaus" even if you change the wake names.
- Read [SECURITY.md](SECURITY.md) before giving it access to anything you care about.

## Uninstall

`scripts/desinstalar.sh` removes the folder, the links it placed in `~/.claude` and the one line it
added to `~/.claude/CLAUDE.md`, asking first.
