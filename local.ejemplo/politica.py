"""Example: permission rules for one of your own command-line tools.

CLASIFICADORES maps a program name to f(tokens) -> "permitir" | "preguntar" | "negar" | None.
`tokens` is the command already split (tokens[0] is the program). Keep it an allowlist: name what only reads,
ask for everything else, refuse what touches credentials.
"""
_LEE = {("tickets", "list"), ("tickets", "view"), ("logs", "show")}
_NUNCA = {"auth", "login", "token", "config", "secret", "password"}


def _mi_cli(toks):
    palabras = [x.lower() for x in toks[1:] if not x.startswith("-")]
    if set(palabras) & _NUNCA:
        return "negar"
    return "permitir" if tuple(palabras[:2]) in _LEE else "preguntar"


CLASIFICADORES = {"mi-cli": _mi_cli}
