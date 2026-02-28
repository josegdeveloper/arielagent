# ARIEL — Guía de Instalación

## Requisitos previos

- **Windows 10/11** (64-bit)
- **Conexión a Internet**
- **API Key de Anthropic** (https://console.anthropic.com)

---

## Paso 1 — Instalar Python 3.11+

1. Descargar desde https://www.python.org/downloads/
2. Al instalar, **marcar la casilla "Add Python to PATH"** (MUY IMPORTANTE)
3. Click en "Install Now"
4. Verificar abriendo CMD o PowerShell:
   ```
   python --version
   ```
   Debe mostrar `Python 3.11.x` o superior.

---

## Paso 2 — Instalar Git (opcional pero recomendado)

1. Descargar desde https://git-scm.com/download/win
2. Instalar con opciones por defecto
3. Si usas Git, clonar el repositorio. Si no, copiar la carpeta del proyecto manualmente.

---

## Paso 3 — Copiar el proyecto

Copiar la carpeta `ARIEL/` completa a la ubicación deseada, por ejemplo:
```
C:\Users\TuUsuario\Desktop\ARIEL\
```

La estructura debe ser:
```
ARIEL/
├── core/
│   ├── agent.py
│   ├── executor.py
│   ├── gui.py
│   ├── logger.py
│   ├── memory.py
│   ├── security.py
│   └── utils.py
├── gateways/
│   ├── scheduler.py
│   └── telegram_bot.py
├── languages/
│   ├── en.json
│   └── es.json
├── laws/
│   └── laws.json
├── logs/
├── memory/
│   ├── embeddings.json
│   ├── longtermmemory.json
│   └── shorttermmemory.json
├── profiles/
│   ├── agent.json
│   ├── user.json
│   ├── ariel-logo.png
├── settings/
│   ├── config.json
│   ├── security.json
│   └── tasks.json
├── tmp/
├── tools/
│   ├── tools.json
│   └── toolindex.json
├── uploads/
├── ariel.py
├── requirements.txt
├── setup.bat
├── INSTALL_es.md
└── INSTALL_en.md
```

---

## Paso 4 — Ejecutar setup.bat

Hacer doble click en `setup.bat` dentro de la carpeta ARIEL, o desde CMD:
```
cd C:\Users\TuUsuario\Desktop\ARIEL
setup.bat
```

Este script:
1. Verifica que Python esté instalado
2. Actualiza pip
3. Instala todas las dependencias
4. Crea las carpetas necesarias (tmp/, logs/, uploads/, memory/)
5. Verifica que todo se instaló correctamente

---

## Paso 5 — Configurar API Key

Al ejecutar ARIEL por primera vez, la interfaz te pedirá la API Key de Anthropic en la pantalla de Settings (⚙️).

Alternativamente, puedes editar `settings/config.json` manualmente:
```json
{
  "api": {
    "api_key": "sk-ant-api03-TU_KEY_AQUI"
  }
}
```

---

## Paso 6 — Ejecutar ARIEL

```
cd C:\Users\TuUsuario\Desktop\ARIEL
python ariel.py
```

Se abrirá el navegador automáticamente con la interfaz de ARIEL en http://localhost:8501.

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `python` no se reconoce | Reinstalar Python marcando "Add to PATH" |
| Error de `pip install` | Ejecutar CMD como Administrador |
| `sentence-transformers` tarda mucho | Es normal, descarga modelos grandes (~400MB la primera vez) |
| Streamlit no abre el navegador | Abrir manualmente http://localhost:8501 |
| Error de `pyautogui` en screenshots | Verificar que `Pillow` está instalado: `pip install Pillow` |
| Puerto 8501 ocupado | Cerrar otra instancia de Streamlit o usar: `streamlit run gui.py --server.port 8502` |
