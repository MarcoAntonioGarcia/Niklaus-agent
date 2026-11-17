#!/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 -I
"""Fake `claude -p --input-format stream-json` for the voice app tests (no quota, deterministic).

Speaks the same stream-json protocol: init on start, text deltas, a result per user message, and the
interrupt control request. Special messages: "LENTO" streams slowly until interrupted, "MUERE" exits.
Prints its own argv once to stderr so the tests can check --resume.
"""
import json
import os
import sys
import threading
import time

SID = "sesion-falsa-" + str(int(time.time() * 1000) % 100000)
interrumpir = threading.Event()
lock = threading.Lock()


def emitir(obj):
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def delta(texto):
    emitir({"type": "stream_event", "event": {"type": "content_block_delta",
                                              "delta": {"type": "text_delta", "text": texto}}})


permisos = {}                                   # request_id -> threading.Event + decision


def pedir_permiso(comando):
    rid = f"perm-{len(permisos) + 1}"
    permisos[rid] = {"evento": threading.Event(), "decision": None}
    emitir({"type": "control_request", "request_id": rid,
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": comando}}})
    permisos[rid]["evento"].wait(120)
    return permisos[rid]["decision"]


def responder(contenido):
    interrumpir.clear()
    if contenido == "PERMISO":
        d = pedir_permiso("touch /tmp/prueba-niklaus")
        delta("permitido." if (d or {}).get("behavior") == "allow" else "negado.")
        emitir({"type": "result", "subtype": "success", "is_error": False, "session_id": SID})
        return
    if contenido == "MUERE":
        sys.exit(3)
    if contenido == "LENTO":
        for _ in range(600):
            if interrumpir.wait(0.05):
                emitir({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": SID})
                return
            delta("bla ")
    delta("Respuesta a: ")
    delta(contenido + ".")
    emitir({"type": "result", "subtype": "success", "is_error": False, "session_id": SID})


def main():
    print(json.dumps(sys.argv[1:]), file=sys.stderr)
    if "--resume" in sys.argv and os.environ.get("FALSO_FALLA_RESUME") == "1":
        print("No conversation found with session ID", file=sys.stderr)
        sys.exit(4)
    emitir({"type": "system", "subtype": "init", "session_id": SID})
    trabajo = None
    for linea in sys.stdin:
        msg = json.loads(linea)
        if msg.get("type") == "control_response":
            r = msg.get("response") or {}
            p = permisos.get(r.get("request_id"))
            if p:
                p["decision"] = r.get("response")
                p["evento"].set()
            continue
        if msg.get("type") == "control_request" and (msg.get("request") or {}).get("subtype") == "interrupt":
            interrumpir.set()
            emitir({"type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id")}})
        elif msg.get("type") == "user":
            if trabajo:
                trabajo.join()
            contenido = msg["message"]["content"]
            if contenido == "MUERE":
                sys.exit(3)
            trabajo = threading.Thread(target=responder, args=(contenido,))
            trabajo.start()


if __name__ == "__main__":
    main()
