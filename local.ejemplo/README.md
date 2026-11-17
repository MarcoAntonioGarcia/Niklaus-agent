# local/ — your private layer

Copy this folder to `local/` (git-ignored, mode 700, files 600). Everything in it is optional and stays on
your machine: it is where your job, your tools and your preferences go, so the public code stays generic.

| File | What it does |
|---|---|
| `prompt.md` | Appended to the assistant's prompt: how to work with *your* tools, tickets and conventions. |
| `politica.py` | Permission rules for *your* command-line tools: which subcommands run without asking. |
| `ajustes.json` | `{"nombres": ["Viernes", "Vier"], "atajos_libres": ["Modo reunión"], "notas_excluidas": ["Privado"]}`: the names it wakes up to (single words; the first is its name), the Shortcuts it may run without asking (only when they get no input), and the Notes folders it must never see. |
| `mapa.json` | Extra nodes for the knowledge map on screen. |

Rules the app enforces: the folder and its files must be yours, not links, and not writable by anyone else;
otherwise they are ignored. A classifier that fails or returns anything unexpected means "ask". The hands
(file tools) cannot write here.
