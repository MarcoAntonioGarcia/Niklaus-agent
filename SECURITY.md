# Security policy

Niklaus processes untrusted content (videos, captions, web pages) and runs tools on the user's
computer, so security reports are very welcome.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository. Include what you found, how to
reproduce it and its impact. You will get an acknowledgement as soon as possible.

## Scope

- Sandbox escapes or ways for downloaded content to read, write or execute outside its sandbox.
- Prompt injection through video, audio, captions, metadata or reports that makes the agent act.
- Data leaving the machine (network calls, telemetry) where the design says it must not.
- Cleanup deleting anything other than what a run recorded.

## Known limitations

These are documented openly in [docs/architecture.md](docs/architecture.md#known-limitations),
including residual covert channels between sandbox phases and an orphan-process sweep that is not
yet fully reliable against a deliberately fast-forking process.
