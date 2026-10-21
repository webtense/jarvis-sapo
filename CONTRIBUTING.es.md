# Contribuir a JARVIS

> [English version](CONTRIBUTING.md)

¡Gracias por tu interés en contribuir! Así puedes participar.

## Primeros pasos

1. Haz un fork del repositorio
2. Clona tu fork
3. Sigue las instrucciones de instalación del README ([en castellano](README.es.md))
4. Haz tus cambios
5. Comprueba que JARVIS sigue funcionando (arranca el servidor y habla con él)
6. Envía un PR

## Qué buscamos

- **Corrección de errores**: si algo está roto, arréglalo
- **Integraciones nuevas**: Spotify, Slack, Notion, etc.
- **Soporte para Windows/Linux**: las integraciones con AppleScript son solo para macOS; se agradecen alternativas multiplataforma
- **Mejor gestión de errores**: en algunos sitios las cosas fallan en silencio
- **Mejoras de voz**: proveedores de TTS alternativos, mejor reconocimiento de voz
- **Acciones nuevas**: amplía lo que JARVIS puede hacer

## Estilo de código

Sí, `server.py` es un monolito de 5.600 líneas. Funciona. Si quieres refactorizar partes en módulos, será bienvenido, siempre que no se rompa nada.

- Mantén las respuestas habladas cortas (1-2 frases como máximo)
- No añadas dependencias salvo que sea necesario
- Prueba tus cambios hablando de verdad con JARVIS
- Mantén la personalidad coherente: mayordomo británico, ingenio seco, economía de lenguaje

## Qué NO hacer

- No añadas telemetría ni analítica
- No envíes datos a servicios externos más allá de las llamadas de API ya existentes (Fish Audio)
- No añadas funciones que modifiquen o borren datos del usuario en los servicios que haya conectado
- No rompas el bucle de voz existente

## Informar de problemas

Abre una incidencia (issue) con:
- Lo que esperabas que ocurriera
- Lo que ocurrió en realidad
- Tu sistema operativo y la versión de Python
- Cualquier mensaje de error del terminal

## ¿Preguntas?

Abre una incidencia o inicia una discusión. Sin complicaciones.
