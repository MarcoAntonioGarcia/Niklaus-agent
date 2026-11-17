---
name: buscar-skill
description: Busca en el catálogo de skills del dueño (skills.toml de Niklaus) la skill adecuada para una tarea, dice si ya está cargada y propone activarla. Úsala cuando haga falta una capacidad que no sabes si existe ("¿tenemos algo para…?", "necesito una skill de…") antes de instalar nada de internet.
---

# buscar-skill

El catálogo vive en `~/Apps/Niklaus/skills.toml` y es la fuente de verdad: dice qué skills existen, de dónde vienen,
qué riesgos tienen y **dónde se cargan**. Cargarlas todas en cada sesión cuesta contexto y confunde, así que muchas
quedan en "biblioteca" hasta que se necesitan.

## Pasos

1. Busca en el catálogo:
   `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 -I ~/Apps/Niklaus/tools/skills.py buscar <palabras>`
   (también `listar --categoria <c>` o `listar --ambito biblioteca`).
2. Si aparece una skill **ya cargada** en esta sesión (ámbito `global`, o `niklaus` dentro de Niklaus), úsala.
3. Si está en **biblioteca** o en otro ámbito:
   - propón al dueño el cambio: cuál, por qué y en qué ámbito (`niklaus` antes que `global`);
   - con su OK, edita su `ambito` en `skills.toml` y corre `tools/skills.py aplicar`;
   - para `global` hace falta además `--global`, porque toca `~/.claude/skills`.
   - El cambio se nota en la siguiente sesión.
4. Si **no existe**: no instales nada de internet directamente (ni con `find-skills`). Propón buscarla, **auditarla**
   como se hizo con ECC o agent-skills (clon en cuarentena, sin ejecutar, `auditorias/`) y catalogarla.
5. Las skills con otro `gestor` (por ejemplo, las del trabajo): nunca las muevas ni las borres.

`tools/skills.py estado` compara el catálogo con lo que de verdad está cargado y avisa de skills sin catalogar.
