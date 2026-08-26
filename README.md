# MendoDesk

Escritorio remoto simple estilo AnyDesk para tu red local: ves y controlás la
pantalla de tu notebook desde tu PC usando solo el navegador.

## Cómo funciona

- **En la notebook** (el equipo a controlar) corre la app **AnyMendo** (`host.py`).
  Captura la pantalla, la transmite por WebSocket y aplica los movimientos de
  mouse y teclas que le llegan.
- **En tu PC** no se instala nada: abrís el navegador (Chrome/Edge/Firefox) en
  la dirección que muestra la app, ponés la contraseña y ya ves y controlás la
  notebook.

## Instalación (en la notebook)

1. Copiá esta carpeta completa a la notebook.
2. Instalá Python 3.12 si no lo tiene (con `winget install -e --id Python.Python.3.12`
   o desde python.org).
3. Hacé doble clic en **`instalar.bat`** (instala las dependencias, una sola vez).

## Uso

1. En la notebook, doble clic en **`AnyMendo.bat`**.
2. En la ventana: elegí contraseña (viene una generada), tocá **Iniciar servidor**.
   La app muestra la dirección, por ejemplo `http://192.168.1.34:8550`.
3. En tu PC, abrí esa dirección en el navegador, ingresá la contraseña y
   **Conectar**. Movés el mouse y escribís como si estuvieras en la notebook.
   - La barra superior aparece al acercar el mouse al borde de arriba:
     pantalla completa, desconectar y FPS.

> La primera vez que inicies el servidor, Windows puede preguntar si permitís
> el acceso de red para Python: aceptá en **redes privadas**.

## Inicio automático

En la ventana de AnyMendo marcá la casilla
**"Iniciar AnyMendo automáticamente al encender el equipo"**.
Eso registra la app en el inicio de Windows (registro `HKCU\...\Run`); al
prender la notebook, AnyMendo arranca minimizado con el servidor ya activo.
Desmarcá la casilla para quitarlo.

## Notas y límites

- Pensado para **red local** (ambos equipos en el mismo WiFi/router). Para
  usarlo por internet necesitarías abrir el puerto en el router o una VPN
  (p. ej. Tailscale); no lo expongas directo a internet sin más protección.
- Algunos atajos los captura el navegador y no llegan al remoto (Ctrl+W,
  Alt+Tab, Win). En pantalla completa la mayoría sí funciona.
- Transmite el monitor principal de la notebook.
- Archivos:
  - `host.py` — app servidor (GUI + captura + control).
  - `viewer.html` — visor que se sirve al navegador.
  - `instalar.bat` / `AnyMendo.bat` — instalación y lanzador.
  - Configuración guardada en `%APPDATA%\AnyMendo\config.json`.
