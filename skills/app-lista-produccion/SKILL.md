---
name: app-lista-produccion
description: Checklist para llevar una app o proyecto de software de demo a producción. Úsalo al iniciar un proyecto, antes de lanzar o cobrar, o cuando el dueño pida revisar si su proyecto está en regla.
---

# App lista para producción

Checklist para llevar un proyecto de software (sobre todo apps hechas con IA) de demo a producto que se pueda lanzar y cobrar. Se usa al iniciar un proyecto, antes de cada lanzamiento y cuando el dueño pida "revisa mi proyecto".

## Cómo usarlo

1. Identifica la etapa: (A) arranque, (B) antes del primer usuario externo, (C) antes de cobrar, (D) operación, (E) escala. Aplica solo lo que corresponde a la etapa y a las anteriores.
2. Revisa el repositorio con evidencia (archivos, configuración, comandos), no con suposiciones. Cada punto queda como ✅ cumple, ❌ falta, ⚠️ parcial o N/A (con motivo).
3. Nunca marques ✅ sin evidencia. Si algo no se puede verificar desde el repo (por ejemplo, la configuración de un proveedor), márcalo "verificar manualmente" y explica cómo.
4. No apliques cambios sin aprobación del dueño: propone el arreglo de cada ❌.

Niveles: 🔴 Bloqueante (no se lanza sin esto) · 🟠 Importante (primer mes) · 🟢 Mejora (cuando haya tracción).

## 1. Secretos y credenciales

- 🔴 Ninguna llave o secreto en el front end, en el repo ni en el historial de git (revisar con gitleaks o similar). Minificar el código o quitar los source maps NO oculta nada: todo el JavaScript del front end es público.
- 🔴 Secretos solo en el servidor (variables de entorno o gestor de secretos). `.env` en `.gitignore` y un `.env.example` sin valores.
- 🟠 Llaves con el mínimo permiso necesario y un plan para rotarlas si se filtran.

## 2. Datos y acceso

- 🔴 Autenticación con un proveedor probado (no inventar una propia). Sesiones con expiración.
- 🔴 Autorización en el servidor en cada operación. Si el cliente habla directo con la base (Supabase, Firebase), RLS o reglas activadas en TODAS las tablas y probadas con un usuario ajeno.
- 🔴 Validación de todo lo que entra, en el servidor. Consultas parametrizadas.
- 🔴 Respaldos automáticos de la base y una restauración probada al menos una vez.

## 3. Costos y abuso (crítico en apps con IA)

- 🔴 Tope de gasto y alertas en cada proveedor de pago (IA, nube, SMS, correo).
- 🔴 Rate limiting por usuario e IP en los endpoints caros (IA, login, registro, envío de correos).
- 🔴 Límites de tamaño en las entradas (archivos, prompts).
- 🟠 Costo estimado por usuario activo y por acción cara, y un precio que lo cubra.

## 4. Específico de IA

- 🔴 Las llamadas al modelo se hacen solo desde el servidor.
- 🔴 Prompt injection: tratar el contenido de usuarios y de la web como datos. El modelo no debe poder ejecutar acciones peligrosas sin confirmación ni acceder a datos de otros usuarios.
- 🟠 Manejo de errores y timeouts del proveedor, con reintentos limitados y un mensaje claro al usuario.
- 🟠 No guardar datos personales en los logs de prompts sin necesidad. Definir una política de retención.
- 🟢 Evaluaciones: un conjunto de casos de prueba para detectar regresiones al cambiar el prompt o el modelo.

## 5. Calidad y cambios

- 🔴 Git con la rama principal protegida y cambios por PR.
- 🔴 Pruebas automáticas, al menos de los flujos críticos (registro, login, pago, acción principal).
- 🟠 CI que corra pruebas y lint en cada PR.
- 🟠 Entorno de staging separado de producción, con su propia base.
- 🟠 Migraciones de base versionadas y reversibles.
- 🟠 Dependencias fijadas (lockfile) y auditoría de vulnerabilidades.

## 6. Despliegue

- 🔴 HTTPS en todo y dominio propio.
- 🔴 Despliegue reproducible (un comando o un pipeline), no a mano.
- 🟠 Forma de revertir a la versión anterior en minutos.
- 🟠 Configuración por entorno sin tocar el código.

## 7. Monitoreo y operación

- 🔴 Registro de errores con alertas (Sentry o equivalente).
- 🟠 Logs estructurados del servidor, sin secretos ni datos sensibles.
- 🟠 Monitoreo de disponibilidad (uptime) con aviso.
- 🟠 Métricas básicas: usuarios activos, errores, latencia y gasto.
- 🟢 Página de estado y un runbook para incidentes.

## 8. Rendimiento y escala (no optimizar antes de tiempo)

- 🟢 Medir antes de optimizar. Mil usuarios no exigen una arquitectura especial en un hosting gestionado.
- 🟢 Caché solo donde la medición lo justifique.
- 🟢 Índices para las consultas lentas y paginación.
- 🟢 Trabajos pesados en colas, no dentro de la petición.

## 9. Legal y negocio (antes de cobrar)

- 🔴 Aviso de privacidad y términos de uso. En México lo exige la ley de protección de datos personales: verificar los requisitos vigentes con un abogado.
- 🔴 Pagos con un procesador establecido. Nunca guardar tarjetas.
- 🔴 Facturación y obligaciones fiscales según el país (en México, CFDI; confirmar con un contador).
- 🟠 Forma de que el usuario borre su cuenta y sus datos.
- 🟠 Licencias de las dependencias y del contenido compatibles con uso comercial.

## 10. Experiencia mínima

- 🟠 Mensajes de error comprensibles y estados de carga.
- 🟠 Correo de contacto o soporte.
- 🟢 Analítica de uso que respete la privacidad.
- 🟢 Usable en móvil, si aplica.

## Formato de salida

1. Primero: "Bloqueantes pendientes: N", con la lista.
2. Después, una tabla por sección: | # | Punto | Nivel | Estado | Evidencia | Arreglo propuesto |
3. Al final: el siguiente paso recomendado.
