// Niklaus UI. Every text from the model, files or the system is rendered with textContent (never innerHTML).
"use strict";

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const ESTADOS = {
  cargando: ["cargando…", "Preparando la voz y el cerebro…"],
  reposo: ["en espera", "Di «Niklaus» y lo que necesites"],
  atento: ["atenta", "Te escucho, no hace falta decir mi nombre"],
  escuchando: ["escuchando", "Te escucho…"],
  pensando: ["pensando", "Pensando…"],
  hablando: ["hablando", ""],
  actuando: ["actuando", "Trabajando en tu computadora…"],
  confirmando: ["esperando tu ok", "Necesito tu permiso"],
  error: ["error", "Algo falló al arrancar; mira el chat"],
};
let estado = "cargando";
let respuesta = null;          // { burbuja, pantalla, texto }

function nodo(tag, clase, texto) {
  const n = document.createElement(tag);
  if (clase) n.className = clase;
  if (texto !== undefined) n.textContent = String(texto);
  return n;
}
function alFinal() { chat.scrollTop = chat.scrollHeight; }
function agregar(n) { chat.appendChild(n); alFinal(); return n; }
function limpiar(t) { return t.replace(/\*\*|__|`/g, ""); }   // plain text: no markdown markup on screen

const cuenta = { ordenes: 0, acciones: 0, permisos: 0 };
function tele(id, v) { const n = $(id); if (n) n.textContent = String(v); }
function ponerEstado(e) {
  estado = e in ESTADOS ? e : "reposo";
  $("estado-texto").textContent = ESTADOS[estado][0];
  tele("t-estado", ESTADOS[estado][0]);
  if (ESTADOS[estado][1]) $("frase").textContent = ESTADOS[estado][1];
}

function pintarRespuesta() {
  if (!respuesta) return;
  const [hablado, pantalla] = respuesta.texto.split("---PANTALLA---");
  respuesta.burbuja.textContent = limpiar(hablado.trim());
  if (pantalla !== undefined) {
    if (!respuesta.pantalla) respuesta.pantalla = agregar(nodo("div", "pantalla"));
    respuesta.pantalla.textContent = limpiar(pantalla.trim());
  }
  alFinal();
}

const api = () => (window.pywebview && window.pywebview.api) || null;

// Never a silent failure: any JS error is shown in the chat and logged by Python.
function fallo(msg) {
  try { agregar(nodo("div", "error", "Error en la ventana: " + msg)); } catch (_) { /* nothing else to do */ }
  try { api() && api().registrar_error(String(msg)); } catch (_) { /* bridge not ready */ }
}
window.addEventListener("error", (e) => fallo(e.message || "desconocido"));
window.addEventListener("unhandledrejection", (e) => fallo((e.reason && e.reason.message) || String(e.reason)));
function llamar(promesa, alFallar) {
  return Promise.resolve(promesa).catch((err) => { fallo((err && err.message) || String(err)); if (alFallar) alFallar(); });
}

window.niklaus = {
  recibir(ev) {
    try { manejar(ev); } catch (err) { fallo(`evento ${ev && ev.tipo}: ${err.message}`); }
  },
};

function manejar(ev) {
    switch (ev.tipo) {
      case "estado": ponerEstado(ev.estado); break;
      case "usuario":
        agregar(nodo("div", "burbuja usuario", ev.texto));
        tele("t-ordenes", ++cuenta.ordenes); tele("t-ultima", ev.texto); break;
      case "respuesta_inicio":
        respuesta = { burbuja: agregar(nodo("div", "burbuja niklaus", "…")), pantalla: null, texto: "" }; break;
      case "texto":
        if (!respuesta) manejar({ tipo: "respuesta_inicio" });
        respuesta.texto += ev.texto; pintarRespuesta(); break;
      case "respuesta_fin":
        if (respuesta && !respuesta.texto.trim()) respuesta.burbuja.remove();
        respuesta = null; break;
      case "accion": {
        tele("t-acciones", ++cuenta.acciones);
        const t = agregar(nodo("div", "tarjeta accion" + (ev.error ? " error-accion" : "")));
        t.appendChild(nodo("span", "", ev.texto));
        if (ev.lote) {                                  // undoes THIS card's batch, not just the last one
          const b = nodo("button", "", "Deshacer");
          b.onclick = () => {
            b.disabled = true;
            llamar(api().deshacer(ev.lote).then((ok) => { if (!ok) b.disabled = false; }), () => (b.disabled = false));
          };
          t.appendChild(b);
        }
        break;
      }
      case "permiso": {
        tele("t-permisos", ++cuenta.permisos);
        const t = agregar(nodo("div", "tarjeta permiso"));
        t.dataset.id = ev.id;
        t.appendChild(nodo("strong", "", "Niklaus pide permiso para " + ev.descripcion));
        t.appendChild(nodo("div", "detalle", ev.detalle));
        const botones = t.appendChild(nodo("div", "botones"));
        const si = botones.appendChild(nodo("button", "si", "Sí, hazlo"));
        const no = botones.appendChild(nodo("button", "no", "No"));
        const responder = (v) => {
          si.disabled = no.disabled = true;
          llamar(api().responder_permiso(ev.id, v), () => (si.disabled = no.disabled = false));
        };
        si.onclick = () => responder(true);
        no.onclick = () => responder(false);
        llamar(api().permiso_visto(ev.id));            // without this ack Python denies within seconds
        break;
      }
      case "permiso_cerrado": {
        const t = chat.querySelector(`.tarjeta.permiso[data-id="${CSS.escape(String(ev.id))}"]`);
        if (t) {
          t.querySelectorAll("button").forEach((b) => (b.disabled = true));
          t.appendChild(nodo("div", "sistema", ev.decision ? "✅ Permitido" : "⛔ Negado"));
        }
        break;
      }
      case "sistema": agregar(nodo("div", "sistema", ev.texto)); break;
      case "microfono": {
        const b = $("microfono");
        b.classList.toggle("silenciado", !!ev.silenciado);
        b.textContent = ev.silenciado ? "Micrófono silenciado" : "Micrófono activo";
        tele("t-mic", ev.silenciado ? "silenciado" : "activo");
        break;
      }
      case "error": agregar(nodo("div", "error", ev.texto)); break;
    }
}

// ----- microphone (always listening for «Niklaus»; this button silences it) -----
let silenciado = false;
$("microfono").onclick = () => {
  if (!api()) return;
  llamar(api().silenciar(!silenciado).then((v) => (silenciado = !!v)));
};
$("texto").addEventListener("keydown", (e) => {
  const campo = $("texto"), valor = campo.value;
  if (e.key !== "Enter" || !valor.trim()) return;
  if (!api()) { fallo("todavía no estoy lista; tu texto sigue en el campo"); return; }
  campo.value = "";
  llamar(api().enviar_texto(valor).then((ok) => {
    if (!ok) { campo.value = valor; agregar(nodo("div", "sistema", "Todavía no estoy lista; tu texto sigue en el campo.")); }
  }), () => (campo.value = valor));
});
$("detener").onclick = () => api() && llamar(api().interrumpir());
$("deshacer").onclick = () => {
  const b = $("deshacer");
  if (!api() || b.disabled) return;
  b.disabled = true;
  llamar(api().deshacer(null)).finally(() => (b.disabled = false));
};

window.addEventListener("pywebviewready", () => {
  // events that arrived before this script was ready
  (window.__pendientes || []).splice(0).forEach((ev) => window.niklaus.recibir(ev));
});

// ----- second screen + session clock -----
if (new URLSearchParams(location.search).get("solo")) document.body.classList.add("solo");
$("pantalla2").onclick = () => api() && llamar(api().segunda_pantalla());
const t0 = Date.now();
setInterval(() => {
  const s = Math.floor((Date.now() - t0) / 1000), p = (n) => String(n).padStart(2, "0");
  tele("t-sesion", (s >= 3600 ? p(Math.floor(s / 3600)) + ":" : "") + p(Math.floor(s / 60) % 60) + ":" + p(s % 60));
}, 1000);

// ----- the living cloud (canvas 2D, no libraries). 1 500 points that MORPH between formations: a brain, the
// map of what Niklaus knows (real data from the app), a data city of light blocks, and a network globe. Motion carries
// meaning: activation waves sweep it at a rate set by the state, a shockwave marks every state change, it pulses
// while speaking, and each formation change is a swirl of every point to its new place. -----
const lienzo = $("orbe"), ctx = lienzo.getContext("2d");
const CALMA = matchMedia("(prefers-reduced-motion: reduce)").matches;
let W = 0, H = 0, raton = [0, 0];
function medir() {
  const r = lienzo.getBoundingClientRect(), k = Math.min(2, window.devicePixelRatio || 1);
  W = Math.max(50, r.width); H = Math.max(50, r.height);
  lienzo.width = W * k; lienzo.height = H * k; ctx.setTransform(k, 0, 0, k, 0, 0);
}
window.addEventListener("resize", medir);
window.addEventListener("pointermove", (e) => { raton = [e.clientX / innerWidth - 0.5, e.clientY / innerHeight - 0.5]; });
const N = 1500, rnd = Math.random;
const F = { cerebro: [], ciudad: [], esfera: [], mapa: null };   // formation -> N target positions
const calido = new Uint8Array(N);                               // warm-lit blocks of the data city
for (let i = 0; i < N; i++) {                                   // brain: two folded hemispheres plus a cerebellum
  const cereb = i > N * 0.9, u = rnd() * 2 - 1, th = rnd() * 6.283, r = Math.sqrt(1 - u * u);
  const x = r * Math.cos(th), y = u, z = r * Math.sin(th);
  const pl = 1 + 0.08 * Math.sin(9 * x + 4 * z) * Math.cos(8 * y + 3 * x) + 0.045 * Math.sin(17 * z);
  const dn = rnd() < 0.32 ? 0.5 + rnd() * 0.45 : 1, lado = i % 2 ? 1 : -1;
  F.cerebro.push(cereb ? [x * 0.30, y * 0.20 - 0.50, z * 0.26 - 0.62]
    : [Math.abs(x) * 0.50 * pl * dn * lado + lado * 0.035, y * 0.62 * pl * dn + 0.06, z * 0.88 * pl * dn]);
}
{                                                               // data city: blocks of light drawn by their edges
  const G = 7, paso = 0.27, lado = 0.085, torres = [];
  for (let a = 0; a < G; a++) for (let b = 0; b < G; b++) {
    const d = Math.hypot(a - (G - 1) / 2, b - (G - 1) / 2) / (G / 2);
    torres.push([(a - (G - 1) / 2) * paso, (b - (G - 1) / 2) * paso, 0.12 + rnd() * 0.85 * (1 - d * 0.55), rnd() < 0.12]);
  }
  for (let i = 0; i < N; i++) {
    const t = torres[i % torres.length], sx = rnd() < 0.5 ? 1 : -1, sz = rnd() < 0.5 ? 1 : -1, base = -0.45, q = rnd();
    let x, y, z;
    if (q < 0.5) { x = sx * lado; z = sz * lado; y = base + rnd() * t[2]; }                    // the 4 vertical edges
    else if (q < 0.85) { y = base + t[2]; if (rnd() < 0.5) { x = sx * lado; z = (rnd() * 2 - 1) * lado; } else { z = sz * lado; x = (rnd() * 2 - 1) * lado; } }  // the roof outline
    else { y = base; x = (rnd() * 2 - 1) * lado; z = sz * lado; }                              // the footprint
    F.ciudad.push([t[0] + x, y, t[1] + z]);
    calido[i] = t[3] ? 1 : 0;
  }
}
for (let i = 0; i < N; i++) {                                   // network globe: an even shell with a few rings
  const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = Math.PI * (3 - Math.sqrt(5)) * i;
  const anillo = i % 9 === 0 ? 1.18 : 0.78;
  F.esfera.push(i % 9 === 0 ? [Math.cos(th * 7) * anillo, (i % 27 === 0 ? 0.25 : -0.1), Math.sin(th * 7) * anillo]
                            : [Math.cos(th) * r * anillo, y * anillo, Math.sin(th) * r * anillo]);
}
let nodos = [];                                                 // knowledge map: [{nombre, x, y, z}]
function armarMapa(lista) {
  if (!Array.isArray(lista) || !lista.length) return;
  const total = lista.reduce((s, n) => s + Math.max(1, +n.peso || 1), 0), K = lista.length;
  nodos = lista.map((n, k) => {
    const a = (k / K) * 6.283 + 0.3, R = 0.78 + (k % 2) * 0.34;
    return { nombre: String(n.nombre).slice(0, 44), peso: Math.max(1, +n.peso || 1), x: Math.cos(a) * R,
             y: (k % 3 - 1) * 0.22, z: Math.sin(a) * R };
  });
  const T = [], enNodos = Math.floor(N * 0.62);
  let k = 0, cupo = 0;
  for (let i = 0; i < N; i++) {
    if (i < 110) { const u = rnd() * 2 - 1, th = rnd() * 6.283, r = Math.sqrt(1 - u * u) * 0.11;   // the hub: Niklaus
      T.push([r * Math.cos(th), u * 0.11, r * Math.sin(th)]); continue; }
    if (i < enNodos) {                                          // a cluster per thing it knows, sized by weight
      while (cupo <= 0) { k = (k + 1) % K; cupo = Math.ceil((nodos[k].peso / total) * (enNodos - 110)); }
      cupo--;
      const n = nodos[k], u = rnd() * 2 - 1, th = rnd() * 6.283, rr = (0.07 + Math.sqrt(n.peso) * 0.028) * Math.cbrt(rnd());
      const r = Math.sqrt(1 - u * u) * rr;
      T.push([n.x + r * Math.cos(th), n.y + u * rr, n.z + r * Math.sin(th)]); continue;
    }
    const n = nodos[i % K], f = rnd();                          // the links from the hub to each cluster
    T.push([n.x * f + (rnd() - 0.5) * 0.012, n.y * f + (rnd() - 0.5) * 0.012, n.z * f + (rnd() - 0.5) * 0.012]);
  }
  F.mapa = T;
}
window.addEventListener("pywebviewready", () => { llamar(api().conocimiento().then(armarMapa)); });
const C = F.cerebro.map((p) => [p[0], p[1], p[2], 0, rnd() * 6.283, 0.5 + rnd(), 0.025 + rnd() * 0.06]);  // x y z glow phase speed ease
const E = [];                                                   // brain wiring: 2 nearest neighbours each
for (let i = 0; i < N; i++) {
  let a = -1, b = -1, da = 9, db = 9;
  for (let j = 0; j < N; j++) {
    if (j === i) continue;
    const p = F.cerebro[i], q = F.cerebro[j], d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2;
    if (d < da) { b = a; db = da; a = j; da = d; } else if (d < db) { b = j; db = d; }
  }
  E.push([i, a], [i, b]);
}
// tilt, spin, size, point size. Sizes measured on screen (2026-11-16): at 0.9 the map's clusters left the window.
const VISTA = { cerebro: [-0.22, 1, 0.80, 1], mapa: [-0.55, 0.45, 0.44, 2.0], ciudad: [-0.52, 0.32, 0.50, 1.9], esfera: [-0.25, 1.2, 0.46, 1.9] };
const PERFIL = {            // spin, impulses, glow, waves/s, wobble, [r,g,b]
  cargando: [0.14, 0.6, 0.55, 0.25, 0.6, [154, 140, 255]], reposo: [0.16, 1.0, 0.7, 0.35, 0.7, [91, 231, 255]],
  atento: [0.22, 2.5, 0.9, 0.7, 1.0, [92, 242, 176]], escuchando: [0.26, 5.0, 1.05, 1.3, 1.4, [92, 242, 176]],
  pensando: [0.75, 12.0, 1.05, 2.2, 1.6, [154, 140, 255]], hablando: [0.30, 6.0, 1.2, 1.6, 1.2, [91, 231, 255]],
  actuando: [0.55, 14.0, 1.15, 2.6, 1.8, [255, 200, 107]], confirmando: [0.12, 3.0, 1.25, 0.9, 0.8, [255, 200, 107]],
  error: [0.06, 0.3, 0.75, 0.15, 0.4, [255, 132, 151]],
};
const act = { giro: 0.16, imp: 1, brillo: 0.7, olas: 0.35, vaiven: 0.7, c: [91, 231, 255], cab: -0.22, tam: 0.8, punto: 1 };
const impulsos = [], olas = [];
let forma = "cerebro", cambio = 0, remolino = 0, ang = 0, deuda = 0, deudaOla = 0, previo = "", antes = 0, golpe = 0;
const RONDA = ["cerebro", "mapa", "ciudad", "esfera"];
function ponerForma(f) {
  if (f === forma || !F[f]) return;
  forma = f; remolino = 1; golpe = Math.max(golpe, 0.7); olas.push([0, 0, 0, 0, 1.9]);
}
window.niklausForma = ponerForma;
function ola(radial) {
  if (radial) { olas.push([0, 0, 0, 0, 1.9]); return; }
  const a = rnd() * 6.283, b = rnd() * 2 - 1, c = Math.sqrt(1 - b * b);
  olas.push([c * Math.cos(a), b, c * Math.sin(a), -1.2, 0.9 + rnd() * 0.7]);
}
function dibujar(t) {
  const dt = Math.min(0.05, (t - antes) / 1000 || 0.016); antes = t;
  const m = PERFIL[estado] || PERFIL.reposo, k = CALMA ? 0.35 : 1;
  if (estado !== previo) {                          // every state change is felt, and work picks its formation
    previo = estado; ola(true); golpe = 1;
    if (estado === "pensando") ponerForma("cerebro"); else if (estado === "actuando") ponerForma("ciudad");
    cambio = t;
  }
  if ((estado === "reposo" || estado === "atento" || estado === "cargando") && t - cambio > 16000) {   // idle: it wanders
    cambio = t; let n = RONDA[(RONDA.indexOf(forma) + 1) % RONDA.length]; if (!F[n]) n = "ciudad"; ponerForma(n);
  }
  const v = VISTA[forma], T = F[forma];
  act.giro += (m[0] * v[1] - act.giro) * 0.05; act.imp += (m[1] - act.imp) * 0.06; act.brillo += (m[2] - act.brillo) * 0.06;
  act.olas += (m[3] - act.olas) * 0.06; act.vaiven += (m[4] - act.vaiven) * 0.06;
  act.cab += (v[0] - act.cab) * 0.035; act.tam += (v[2] - act.tam) * 0.04; act.punto += (v[3] - act.punto) * 0.04;
  for (let i = 0; i < 3; i++) act.c[i] += (m[5][i] - act.c[i]) * 0.06;
  golpe *= 0.92; remolino *= 0.965;
  const esCerebro = forma === "cerebro" ? 1 - remolino : 0, esMapa = forma === "mapa" ? 1 - remolino : 0, esCiudad = forma === "ciudad" ? 1 : 0;
  ang += act.giro * dt * 0.75 * k;
  deudaOla += act.olas * dt * k; while (deudaOla >= 1) { deudaOla -= 1; ola(false); }
  for (let i = olas.length - 1; i >= 0; i--) { olas[i][3] += olas[i][4] * dt; if (olas[i][3] > 1.6) olas.splice(i, 1); }
  const voz = estado === "hablando" ? 0.03 * (Math.sin(t / 70) * Math.sin(t / 190) + Math.sin(t / 43) * 0.5) : 0;
  const S = Math.min(W, H) * act.tam * (1 + 0.014 * Math.sin(t / 1100) + voz + golpe * 0.05);
  const cab = act.cab + 0.08 * Math.sin(t / 3700) + raton[1] * 0.25, lad = 0.07 * Math.sin(t / 2900) + raton[0] * 0.35;
  const co = Math.cos(ang + lad), si = Math.sin(ang + lad), ci = Math.cos(cab), sn = Math.sin(cab);
  const cx = W / 2, cy = H / 2 - 12, [r, g, b] = act.c.map(Math.round);
  ctx.globalCompositeOperation = "source-over"; ctx.clearRect(0, 0, W, H);
  const halo = ctx.createRadialGradient(cx, cy, S * 0.04, cx, cy, S * 0.8);
  halo.addColorStop(0, `rgba(${r},${g},${b},${(0.16 + golpe * 0.12) * act.brillo})`); halo.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = halo; ctx.fillRect(0, 0, W, H);
  ctx.globalCompositeOperation = "lighter";
  const Q = new Array(N), wob = 0.012 * act.vaiven * k * (esCiudad ? 0.25 : 1), tt = t / 1000, rs = remolino * 0.09;
  const proy = (x, y, z) => { const X = x * co - z * si, Z0 = x * si + z * co, Y = y * ci - Z0 * sn, Z = y * sn + Z0 * ci, f = 1 / (2.1 - Z * 0.7);
    return [cx + X * S * f * 2.1, cy - Y * S * f * 2.1, Z, f]; };
  for (let i = 0; i < N; i++) {
    const p = C[i], d = T[i];
    p[0] += (d[0] - p[0]) * p[6] + (-p[2]) * rs * p[6] * 6;      // ease to the target, with a swirl while morphing
    p[1] += (d[1] - p[1]) * p[6];
    p[2] += (d[2] - p[2]) * p[6] + p[0] * rs * p[6] * 6;
    const x = p[0] + wob * Math.sin(tt * p[5] + p[4]), y = p[1] + wob * Math.cos(tt * p[5] * 0.8 + p[4] * 1.7),
          z = p[2] + wob * Math.sin(tt * p[5] * 1.3 + p[4] * 0.6);
    for (const o of olas) {
      const dd = o[0] || o[1] || o[2] ? x * o[0] + y * o[1] + z * o[2] : Math.sqrt(x * x + y * y + z * z) - 0.1;
      const q = 1 - Math.abs(dd - o[3]) * 9; if (q > p[3]) p[3] = q;
    }
    Q[i] = proy(x, y, z);
    p[3] *= 0.90;
  }
  if (esCerebro > 0.05) {                           // the wiring only exists as a brain
    ctx.lineWidth = 0.6;
    for (const [i, j] of E) {
      const a = Q[i], c = Q[j], e = Math.max(C[i][3], C[j][3]);
      const al = (0.035 + (a[2] + 1) * 0.05 + e * 0.55) * act.brillo * esCerebro; if (al < 0.03) continue;
      ctx.strokeStyle = `rgba(${r},${g},${b},${Math.min(0.85, al)})`;
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(c[0], c[1]); ctx.stroke();
    }
  }
  for (let i = 0; i < N; i++) {
    const q = Q[i], e = C[i][3], al = Math.min(1, (0.22 + (q[2] + 1) * 0.28 + e * 0.9) * act.brillo * (v[3] > 1 ? 1.5 : 1)),
          d = (1.0 + e * 2.6) * q[3] * 2.1 * act.punto;
    ctx.fillStyle = e > 0.5 ? `rgba(255,255,255,${al})`
      : esCiudad && calido[i] ? `rgba(255,196,120,${Math.min(1, al * 1.5)})` : `rgba(${r},${g},${b},${al})`;
    ctx.fillRect(q[0] - d / 2, q[1] - d / 2, d, d);
  }
  if (esMapa > 0.25 && nodos.length) {              // the knowledge map names what each cluster is
    ctx.globalCompositeOperation = "source-over"; ctx.textBaseline = "middle";
    ctx.font = `${Math.max(11, Math.min(15, W / 70))}px -apple-system, system-ui, sans-serif`;
    const hub = proy(0, 0, 0);
    for (const n of nodos) {
      const q = proy(n.x, n.y, n.z), al = Math.min(1, (esMapa - 0.25) * 1.6) * (0.55 + (q[2] + 1) * 0.25);
      ctx.strokeStyle = `rgba(${r},${g},${b},${al * 0.25})`; ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(hub[0], hub[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
      const der = q[0] >= cx; ctx.textAlign = der ? "left" : "right";
      const ancho = ctx.measureText(n.nombre).width, tx = q[0] + (der ? 14 : -14) * q[3] * 2;   // never cut at the edge
      ctx.fillStyle = `rgba(228,236,255,${al})`;
      ctx.fillText(n.nombre, der ? Math.min(tx, W - ancho - 12) : Math.max(tx, ancho + 12), Math.max(16, Math.min(H - 90, q[1])));
    }
    ctx.textAlign = "center"; ctx.fillStyle = `rgba(228,236,255,${Math.min(1, esMapa)})`;
    ctx.fillText("Niklaus", hub[0], hub[1] - 26 * hub[3] * 2);
    ctx.globalCompositeOperation = "lighter";
  }
  if (esCerebro > 0.3) {                            // impulses racing along the wiring
    deuda += act.imp * dt * 10 * k;
    while (deuda >= 1 && impulsos.length < 420) { deuda -= 1; impulsos.push([Math.floor(rnd() * E.length), 0]); }
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    for (let n = impulsos.length - 1; n >= 0; n--) {
      const im = impulsos[n], [i, j] = E[im[0]], a = Q[i], c = Q[j];
      im[1] += dt * 7;
      if (im[1] >= 1) {
        C[j][3] = Math.max(C[j][3], 0.9); impulsos.splice(n, 1);
        if (rnd() < 0.62 && impulsos.length < 420) impulsos.push([j * 2 + (rnd() < 0.5 ? 0 : 1), 0]);
        continue;
      }
      ctx.fillRect(a[0] + (c[0] - a[0]) * im[1] - 1.3, a[1] + (c[1] - a[1]) * im[1] - 1.3, 2.6, 2.6);
    }
  } else { impulsos.length = 0; }
  requestAnimationFrame(dibujar);
}
medir();
requestAnimationFrame(dibujar);
ponerEstado("cargando");
(window.__pendientes || []).splice(0).forEach((ev) => window.niklaus.recibir(ev));
