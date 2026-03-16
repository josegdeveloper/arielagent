<p align="center">
  <img src="profiles/ariel-logo.png" alt="ARIEL Logo" width="100">
</p>

<h1 align="center">ARIEL</h1>
<p align="center"><b>Advanced Reasoning & Intelligent Execution Layer</b></p>

<p align="center">
  An open-source AI agent that controls your PC through natural language.<br>
  It reasons, plans, executes — and remembers.
</p>

<p align="center">
  <a href="https://arielagent.ai">🌐 Website</a> · 
  <a href="#-quick-start">🚀 Quick Start</a> · 
  <a href="INSTALL_en.md">📖 Full Guide</a> · 
  <a href="#-español">🇪🇸 Español</a>
</p>

---

## ✨ Features

**🖥️ Full PC Control** — Mouse, keyboard, screenshots, file management, and system commands, all through natural conversation.

**🧠 Dual Memory** — Short-term memory for the current session and persistent long-term memory with semantic search across sessions. ARIEL remembers you.

**🔐 Security First** — Password-protected from day one. API keys and tokens are encrypted on disk with Fernet. Destructive actions require explicit confirmation.

**🧰 Dynamic Tools** — ARIEL can create its own tools on the fly. Ask it to build a new capability and it writes, registers, and uses the tool — no code required from you.

**🔌 Central Orchestrator** — A single agent instance serves all interfaces via IPC socket. Shared memory, unified logs, and efficient resource usage.

**🤖 Model-Agnostic** — Works with Anthropic (Claude), OpenAI, LM Studio, Ollama, and any OpenAI-compatible API. Switch providers from the GUI with zero code changes.

**📱 Telegram & WhatsApp** — Talk to ARIEL from your phone via Telegram or WhatsApp. Dual-layer security for WhatsApp (contact verification + passphrase).

**⏰ Scheduled Tasks** — Automate routines with a visual scheduler. Define what ARIEL should do, when, and on which days.

**📜 Constitution** — A set of laws the agent never violates. Define boundaries and principles — ARIEL respects them always, no matter what.

**💰 Prompt Caching** — Reduces API costs up to 90% by caching the system prompt. Real savings, visible in the logs.

**🌍 Multilingual** — Full i18n support. The UI and agent responses adapt to your language. English and Spanish out of the box.

**👤 User Profile** — ARIEL learns about you through conversation and builds a personal profile automatically. No forms to fill.

**📊 Dual Logging** — Human-readable `.log` files plus structured `.json` for analysis and debugging. All channels (GUI, Telegram, WhatsApp, Scheduler) in one unified log.

**🖼️ Web GUI** — A clean Streamlit-based dashboard. Settings, memory viewer, tool inspector, task scheduler, connector management — all visual, no config files to edit.

---

<p align="center">
  <img src="https://arielagent.ai/images/ariel-agent-chat.png" alt="ARIEL Chat Interface" width="700">
</p>

---

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/josegdeveloper/arielagent.git
cd arielagent

# Install dependencies
pip install -r requirements.txt

# Launch ARIEL
python ariel.py
```

Your browser will open at `http://localhost:8501`. On first launch, ARIEL will ask you to create a password and set up your API key.

> **Windows users**: You can also double-click `setup.bat` for an automated installation.

> ⚠️ **Important Safety Recommendation**: ARIEL is an AI agent with the ability to control your mouse, keyboard, execute system commands, and manage files. AI behavior can be unpredictable. We strongly recommend running ARIEL inside a **virtual machine** (e.g., VirtualBox, VMware, Hyper-V) or on a **dedicated computer** that does not contain sensitive personal data. Use at your own risk.

For detailed instructions, see **[INSTALL_en.md](INSTALL_en.md)** (English) or **[INSTALL_es.md](INSTALL_es.md)** (Spanish).

---

## 📋 Requirements

- **Python 3.11+**
- **Windows 10/11** (64-bit)
- **LLM API Key** — One of:
  - [Anthropic API Key](https://console.anthropic.com) (Claude) — recommended
  - [OpenAI API Key](https://platform.openai.com) (GPT)
  - Local server: [LM Studio](https://lmstudio.ai) or [Ollama](https://ollama.com) (free, no API key needed)

---

## 🏗️ Project Structure

```
arielagent/
├── core/               # Brain: agent, GUI, memory, security, IPC, LLM providers
├── gateways/           # Telegram bot, WhatsApp bot & task scheduler
├── languages/          # i18n files (en.json, es.json)
├── laws/               # Constitution — rules the agent never breaks
├── logs/               # Dual logging output
├── memory/             # Short-term, long-term & embeddings
├── profiles/           # User and agent profiles
├── settings/           # Config, security settings, scheduled tasks
├── tools/              # Dynamic tool registry
├── uploads/            # User file uploads
├── ariel.py            # Central orchestrator (entry point + IPC server)
├── setup.bat           # Automated Windows installer
└── requirements.txt    # Python dependencies
```

---

## 🤝 Contributing

ARIEL is open source and contributions are welcome. Feel free to open an issue or submit a pull request.

---

## 📄 License

This project is open source. See the [LICENSE](LICENSE) file for details.

---

## 📝 Changelog

### v1.20.0
- **Model-Agnostic**: New LLM provider abstraction layer (`core/llm_provider.py`). ARIEL now works with Anthropic (Claude), OpenAI, LM Studio, Ollama, and any OpenAI-compatible API. Switch providers from the GUI — zero code changes.
- GUI: Provider selector (Anthropic / OpenAI Compatible) and Base URL field in Settings.
- Thinking tag cleanup: Automatically strips `<think>...</think>` reasoning blocks from local models (Qwen, DeepSeek, etc.) so users only see the final answer.
- New `send_whatsapp_message` tool for proactive outbound messaging via IPC queue.
- WhatsApp "Revoke all authorizations" now fully resets: stops bot, deletes session files, and cleans up status.
- All user-facing strings migrated to translation files — zero hardcoded Spanish/English in code.
- Added `openai` dependency.

### v1.19.0
- **Central Orchestrator**: `ariel.py` is now an IPC socket server that owns the single `ARIELAgent` instance. All other processes (GUI, Telegram, WhatsApp, Scheduler) are thin IPC clients.
- New `core/ipc.py` module with `ArielServer` (socket server) and `ArielClient` (lightweight client).
- Eliminates 4× duplicated agent/API-client instances — shared memory, unified logs.
- Session key exchange via IPC (`set_session_key`) — no more temporary key files on disk.
- Token decryption via IPC (`decrypt_token`) — bot processes never touch the session key.
- Transport: Unix domain socket (Linux/macOS) or TCP localhost:19420 (Windows).

### v1.18.0
- **WhatsApp Gateway**: New gateway using WhatsApp Web protocol (neonize). QR code pairing from the GUI, session persistence, and dual-layer security (contact check + passphrase authorization). Authorized devices managed from the Connectors panel.
- Fixed session key propagation to bot subprocesses (Telegram and WhatsApp).
- Added `neonize` and `qrcode[pil]` dependencies.

### v1.17.0
- **Hybrid Screen Control**: New UI Automation (Accessibility Tree) as primary method + optional Computer Use (Anthropic vision) as fallback.
- New tools: `ui_snapshot`, `ui_click`, `ui_type` — fast, cheap, and reliable desktop control via pywinauto.
- Removed old screenshot+grid tools (replaced by UI Automation).
- Settings toggle to enable/disable Computer Use fallback (controls API cost).
- Added `pywinauto` dependency.

### v1.16.0
- Initial public release.

---

<br>

<a name="-español"></a>

<p align="center">
  <img src="profiles/ariel-logo.png" alt="ARIEL Logo" width="100">
</p>

<h1 align="center">ARIEL</h1>
<p align="center"><b>Advanced Reasoning & Intelligent Execution Layer</b></p>

<p align="center">
  Un agente de IA open source que controla tu PC mediante lenguaje natural.<br>
  Razona, planifica, ejecuta — y recuerda.
</p>

<p align="center">
  <a href="https://arielagent.ai">🌐 Web</a> · 
  <a href="#-inicio-rápido">🚀 Inicio Rápido</a> · 
  <a href="INSTALL_es.md">📖 Guía Completa</a> · 
  <a href="#-features">🇬🇧 English</a>
</p>

---

## ✨ Características

**🖥️ Control total del PC** — Ratón, teclado, capturas de pantalla, gestión de archivos y comandos del sistema, todo mediante conversación natural.

**🧠 Memoria dual** — Memoria a corto plazo para la sesión actual y memoria a largo plazo persistente con búsqueda semántica entre sesiones. ARIEL te recuerda.

**🔐 Seguridad ante todo** — Protegido con contraseña desde el primer día. Las API keys y tokens se cifran en disco con Fernet. Las acciones destructivas requieren confirmación explícita.

**🧰 Herramientas dinámicas** — ARIEL puede crear sus propias herramientas sobre la marcha. Pídele que construya una nueva capacidad y la escribirá, registrará y usará — sin que toques una línea de código.

**🔌 Orquestador central** — Una única instancia del agente sirve a todas las interfaces vía socket IPC. Memoria compartida, logs unificados y uso eficiente de recursos.

**🤖 Agnóstico de modelo** — Funciona con Anthropic (Claude), OpenAI, LM Studio, Ollama y cualquier API compatible con OpenAI. Cambia de proveedor desde la GUI sin tocar código.

**📱 Telegram y WhatsApp** — Habla con ARIEL desde tu móvil vía Telegram o WhatsApp. Seguridad de doble capa en WhatsApp (verificación de contacto + frase secreta).

**⏰ Tareas programadas** — Automatiza rutinas con un programador visual. Define qué debe hacer ARIEL, cuándo y qué días.

**📜 Constitución** — Un conjunto de leyes que el agente nunca viola. Define límites y principios — ARIEL los respeta siempre, pase lo que pase.

**💰 Caché de prompts** — Reduce los costes de API hasta un 90% cacheando el system prompt. Ahorro real, visible en los logs.

**🌍 Multilingüe** — Soporte i18n completo. La interfaz y las respuestas del agente se adaptan a tu idioma. Inglés y español de serie.

**👤 Perfil de usuario** — ARIEL aprende sobre ti a través de la conversación y construye un perfil personal automáticamente. Sin formularios que rellenar.

**📊 Logging dual** — Archivos `.log` legibles para humanos más `.json` estructurado para análisis y depuración. Todos los canales (GUI, Telegram, WhatsApp, Scheduler) en un log unificado.

**🖼️ Interfaz web** — Un panel limpio basado en Streamlit. Ajustes, visor de memoria, inspector de herramientas, programador de tareas, gestión de conectores — todo visual, sin archivos de configuración que editar.

---

<p align="center">
  <img src="https://arielagent.ai/images/ariel-agent-chat.png" alt="Interfaz de chat de ARIEL" width="700">
</p>

---

## 🚀 Inicio Rápido

```bash
# Clona el repositorio
git clone https://github.com/josegdeveloper/arielagent.git
cd arielagent

# Instala las dependencias
pip install -r requirements.txt

# Lanza ARIEL
python ariel.py
```

Tu navegador se abrirá en `http://localhost:8501`. En el primer arranque, ARIEL te pedirá que crees una contraseña y configures tu API key.

> **Usuarios de Windows**: También puedes hacer doble clic en `setup.bat` para una instalación automática.

> ⚠️ **Recomendación de seguridad importante**: ARIEL es un agente de IA con capacidad para controlar tu ratón, teclado, ejecutar comandos del sistema y gestionar archivos. El comportamiento de la IA puede ser impredecible. Recomendamos encarecidamente ejecutar ARIEL dentro de una **máquina virtual** (p. ej., VirtualBox, VMware, Hyper-V) o en un **ordenador dedicado** que no contenga datos personales sensibles. Úsalo bajo tu propia responsabilidad.

Para instrucciones detalladas, consulta **[INSTALL_es.md](INSTALL_es.md)** (Español) o **[INSTALL_en.md](INSTALL_en.md)** (Inglés).

---

## 📋 Requisitos

- **Python 3.11+**
- **Windows 10/11** (64-bit)
- **API Key de un LLM** — Una de:
  - [Anthropic API Key](https://console.anthropic.com) (Claude) — recomendado
  - [OpenAI API Key](https://platform.openai.com) (GPT)
  - Servidor local: [LM Studio](https://lmstudio.ai) o [Ollama](https://ollama.com) (gratis, sin API key)

---

## 🏗️ Estructura del Proyecto

```
arielagent/
├── core/               # Cerebro: agente, GUI, memoria, seguridad, IPC, proveedores LLM
├── gateways/           # Bot de Telegram, bot de WhatsApp y programador de tareas
├── languages/          # Archivos i18n (en.json, es.json)
├── laws/               # Constitución — reglas que el agente nunca rompe
├── logs/               # Salida de logging dual
├── memory/             # Memoria corto plazo, largo plazo y embeddings
├── profiles/           # Perfiles de usuario y agente
├── settings/           # Configuración, seguridad, tareas programadas
├── tools/              # Registro dinámico de herramientas
├── uploads/            # Archivos subidos por el usuario
├── ariel.py            # Orquestador central (punto de entrada + servidor IPC)
├── setup.bat           # Instalador automático para Windows
└── requirements.txt    # Dependencias de Python
```

---

## 🤝 Contribuir

ARIEL es open source y las contribuciones son bienvenidas. No dudes en abrir un issue o enviar un pull request.

---

## 📄 Licencia

Este proyecto es open source. Consulta el archivo [LICENSE](LICENSE) para más detalles.

---

## 📝 Changelog

### v1.20.0
- **Agnóstico de modelo**: Nueva capa de abstracción de proveedores LLM (`core/llm_provider.py`). ARIEL ahora funciona con Anthropic (Claude), OpenAI, LM Studio, Ollama y cualquier API compatible con OpenAI. Cambia de proveedor desde la GUI — sin tocar código.
- GUI: Selector de proveedor (Anthropic / OpenAI Compatible) y campo Base URL en Configuración.
- Limpieza de etiquetas de razonamiento: Elimina automáticamente los bloques `<think>...</think>` de modelos locales (Qwen, DeepSeek, etc.) para que el usuario solo vea la respuesta final.
- Nueva herramienta `send_whatsapp_message` para envío proactivo de mensajes vía cola IPC.
- "Revocar todas las autorizaciones" de WhatsApp ahora resetea completamente: detiene el bot, elimina archivos de sesión y limpia el estado.
- Todos los textos de interfaz migrados a archivos de traducción — cero español/inglés hardcodeado en el código.
- Añadida dependencia `openai`.

### v1.19.0
- **Orquestador Central**: `ariel.py` es ahora un servidor IPC por socket que posee la única instancia de `ARIELAgent`. Todos los demás procesos (GUI, Telegram, WhatsApp, Scheduler) son clientes IPC ligeros.
- Nuevo módulo `core/ipc.py` con `ArielServer` (servidor socket) y `ArielClient` (cliente ligero).
- Elimina 4× instancias duplicadas de agente/cliente API — memoria compartida, logs unificados.
- Intercambio de session key vía IPC (`set_session_key`) — sin archivos temporales de clave en disco.
- Descifrado de tokens vía IPC (`decrypt_token`) — los procesos de bots nunca tocan la session key.
- Transporte: Unix domain socket (Linux/macOS) o TCP localhost:19420 (Windows).

### v1.18.0
- **Gateway WhatsApp**: Nuevo gateway usando el protocolo de WhatsApp Web (neonize). Vinculación por QR desde la GUI, sesión persistente y seguridad dual (verificación de contacto + frase secreta). Dispositivos autorizados gestionados desde el panel de Conectores.
- Corregida la propagación de la session key a los subprocesos de bots (Telegram y WhatsApp).
- Añadidas dependencias `neonize` y `qrcode[pil]`.

### v1.17.0
- **Control de Pantalla Híbrido**: UI Automation (Árbol de Accesibilidad) como método principal + Computer Use (visión Anthropic) opcional como respaldo.
- Nuevas herramientas: `ui_snapshot`, `ui_click`, `ui_type` — control de escritorio rápido, barato y fiable vía pywinauto.
- Eliminadas las herramientas antiguas de screenshot+grid (sustituidas por UI Automation).
- Toggle en Ajustes para activar/desactivar el respaldo Computer Use (controla coste de API).
- Añadida dependencia `pywinauto`.

### v1.16.0
- Versión pública inicial.
