"""AnyMendo - Host de escritorio remoto.

Se ejecuta en el equipo que quiere ser controlado (la notebook).
Desde otro equipo se accede con el navegador a http://IP:PUERTO,
se ingresa la contraseña y se ve/controla la pantalla.
"""

import asyncio
import ctypes
import hashlib
import io
import json
import os
import secrets
import socket
import sys
import threading
import concurrent.futures
import tkinter as tk
from tkinter import ttk, messagebox
import winreg

APP_NAME = "AnyMendo"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", BASE_DIR), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
VIEWER_PATH = os.path.join(BASE_DIR, "viewer.html")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULT_PORT = 8550
JPEG_QUALITY = 60
TARGET_FPS = 20

# DPI awareness: sin esto, en pantallas con escalado (125%/150%) la captura
# sale recortada y las coordenadas del mouse no coinciden.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from aiohttp import web, WSMsgType
import mss
from PIL import Image
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyController, Key, KeyCode


# ---------------------------------------------------------------- config

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------- autostart

def _autostart_command():
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{os.path.abspath(__file__)}" --autostart'


def get_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ,
                              _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- helpers

def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


SPECIAL_KEYS = {
    "Enter": Key.enter, "Backspace": Key.backspace, "Tab": Key.tab,
    "Escape": Key.esc, "Delete": Key.delete, "Insert": Key.insert,
    "Home": Key.home, "End": Key.end, "PageUp": Key.page_up,
    "PageDown": Key.page_down, "ArrowUp": Key.up, "ArrowDown": Key.down,
    "ArrowLeft": Key.left, "ArrowRight": Key.right, "Shift": Key.shift,
    "Control": Key.ctrl, "Alt": Key.alt, "AltGraph": Key.alt_gr,
    "Meta": Key.cmd, "CapsLock": Key.caps_lock, "NumLock": Key.num_lock,
    "ContextMenu": Key.menu, "PrintScreen": Key.print_screen,
    "Pause": Key.pause, " ": Key.space,
    **{f"F{i}": getattr(Key, f"f{i}") for i in range(1, 13)},
}

MOUSE_BUTTONS = {0: Button.left, 1: Button.middle, 2: Button.right}


def browser_key_to_pynput(name):
    if name in SPECIAL_KEYS:
        return SPECIAL_KEYS[name]
    if len(name) == 1:
        return KeyCode.from_char(name)
    return None


# ---------------------------------------------------------------- server

class RemoteServer:
    """Servidor HTTP + WebSocket en un hilo propio con su loop asyncio."""

    def __init__(self, on_status=None):
        self.on_status = on_status or (lambda msg: None)
        self.loop = None
        self.thread = None
        self.runner = None
        self.password = ""
        self.port = DEFAULT_PORT
        self.running = False
        self.mouse = MouseController()
        self.keyboard = KeyController()
        self._capture_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="capture")
        self._sct = None  # mss vive en el hilo de captura
        self._monitor = None

    # ---- ciclo de vida -------------------------------------------------

    def start(self, port, password):
        self.port = port
        self.password = password
        self.loop = asyncio.new_event_loop()
        started = threading.Event()
        error = []

        def run():
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._start_app())
            except Exception as exc:
                error.append(exc)
                started.set()
                return
            self.running = True
            started.set()
            self.loop.run_forever()
            self.loop.run_until_complete(self.runner.cleanup())
            self.loop.close()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        started.wait(timeout=10)
        if error:
            raise error[0]

    def stop(self):
        if self.loop and self.running:
            self.running = False
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)

    async def _start_app(self):
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()

    # ---- HTTP ----------------------------------------------------------

    async def _handle_index(self, request):
        with open(VIEWER_PATH, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")

    # ---- captura de pantalla -------------------------------------------

    def _grab_frame(self):
        # Se ejecuta siempre en el mismo hilo del pool (mss lo requiere).
        if self._sct is None:
            self._sct = mss.mss()
            self._monitor = self._sct.monitors[1]
        shot = self._sct.grab(self._monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()

    # ---- WebSocket -----------------------------------------------------

    async def _handle_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=1 << 20)
        await ws.prepare(request)
        self.on_status(f"Conexión entrante desde {request.remote}")

        # Primer mensaje: autenticación.
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=15)
        except asyncio.TimeoutError:
            await ws.close()
            return ws
        if msg.type != WSMsgType.TEXT:
            await ws.close()
            return ws
        try:
            data = json.loads(msg.data)
        except ValueError:
            data = {}
        if not secrets.compare_digest(str(data.get("password", "")),
                                      self.password):
            await ws.send_json({"type": "error",
                                "message": "Contraseña incorrecta"})
            await ws.close()
            self.on_status(f"Contraseña incorrecta desde {request.remote}")
            return ws

        loop = asyncio.get_running_loop()
        # Tamaño real de la pantalla para que el visor escale coordenadas.
        await loop.run_in_executor(self._capture_pool, self._grab_frame)
        mon = self._monitor
        await ws.send_json({"type": "ok", "w": mon["width"],
                            "h": mon["height"]})
        self.on_status(f"Cliente conectado: {request.remote}")

        pressed_keys = set()
        sender = asyncio.ensure_future(self._send_frames(ws, loop))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    ev = json.loads(msg.data)
                except ValueError:
                    continue
                self._handle_event(ev, mon, pressed_keys)
        finally:
            sender.cancel()
            for key in pressed_keys:
                try:
                    self.keyboard.release(key)
                except Exception:
                    pass
            self.on_status(f"Cliente desconectado: {request.remote}")
        return ws

    async def _send_frames(self, ws, loop):
        last_hash = None
        unchanged = 0
        interval = 1.0 / TARGET_FPS
        try:
            while not ws.closed:
                frame = await loop.run_in_executor(self._capture_pool,
                                                   self._grab_frame)
                digest = hashlib.md5(frame).digest()
                # Si no cambió nada, no reenviamos (salvo keyframe cada ~2 s).
                if digest != last_hash or unchanged >= TARGET_FPS * 2:
                    await ws.send_bytes(frame)
                    last_hash = digest
                    unchanged = 0
                else:
                    unchanged += 1
                await asyncio.sleep(interval)
        except (asyncio.CancelledError, ConnectionResetError):
            pass

    # ---- eventos de entrada ---------------------------------------------

    def _handle_event(self, ev, mon, pressed_keys):
        t = ev.get("t")
        try:
            if t == "mv":
                x = mon["left"] + ev["x"] * mon["width"]
                y = mon["top"] + ev["y"] * mon["height"]
                self.mouse.position = (int(x), int(y))
            elif t == "md":
                self.mouse.press(MOUSE_BUTTONS.get(ev.get("b", 0), Button.left))
            elif t == "mu":
                self.mouse.release(MOUSE_BUTTONS.get(ev.get("b", 0), Button.left))
            elif t == "wh":
                self.mouse.scroll(int(ev.get("dx", 0)), int(ev.get("dy", 0)))
            elif t == "kd":
                key = browser_key_to_pynput(ev.get("k", ""))
                if key is not None:
                    self.keyboard.press(key)
                    pressed_keys.add(key)
            elif t == "ku":
                key = browser_key_to_pynput(ev.get("k", ""))
                if key is not None:
                    self.keyboard.release(key)
                    pressed_keys.discard(key)
            elif t == "ra":  # soltar todo (el visor perdió el foco)
                for key in list(pressed_keys):
                    self.keyboard.release(key)
                pressed_keys.clear()
        except Exception:
            pass  # un evento inválido no debe tirar la conexión


# ---------------------------------------------------------------- GUI

class HostApp:
    def __init__(self, root, autostarted=False):
        self.root = root
        self.server = RemoteServer(on_status=self._queue_status)
        self.cfg = load_config()
        self._status_lock = threading.Lock()
        self._pending_status = []

        root.title(f"{APP_NAME} – Escritorio remoto")
        root.resizable(False, False)
        try:
            root.iconbitmap(default="")
        except Exception:
            pass

        pad = {"padx": 12, "pady": 6}
        frame = ttk.Frame(root, padding=14)
        frame.grid(sticky="nsew")

        self.status_var = tk.StringVar(value="● Detenido")
        self.status_label = ttk.Label(frame, textvariable=self.status_var,
                                      font=("Segoe UI", 12, "bold"),
                                      foreground="#b00020")
        self.status_label.grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(frame, text="Puerto:").grid(row=1, column=0, sticky="w", **pad)
        self.port_var = tk.StringVar(value=str(self.cfg.get("port", DEFAULT_PORT)))
        self.port_entry = ttk.Entry(frame, textvariable=self.port_var, width=10)
        self.port_entry.grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Contraseña:").grid(row=2, column=0, sticky="w", **pad)
        default_pw = self.cfg.get("password") or secrets.token_urlsafe(6)
        self.pw_var = tk.StringVar(value=default_pw)
        self.pw_entry = ttk.Entry(frame, textvariable=self.pw_var, width=24)
        self.pw_entry.grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Conectate desde tu PC en:").grid(
            row=3, column=0, sticky="w", **pad)
        self.url_var = tk.StringVar(value="—")
        url_entry = ttk.Entry(frame, textvariable=self.url_var, width=28,
                              state="readonly")
        url_entry.grid(row=3, column=1, sticky="w", **pad)

        self.toggle_btn = ttk.Button(frame, text="Iniciar servidor",
                                     command=self.toggle_server)
        self.toggle_btn.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)

        self.auto_var = tk.BooleanVar(value=get_autostart())
        ttk.Checkbutton(
            frame,
            text="Iniciar AnyMendo automáticamente al encender el equipo",
            variable=self.auto_var, command=self.toggle_autostart,
        ).grid(row=5, column=0, columnspan=2, sticky="w", **pad)

        self.log_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.log_var, foreground="#555",
                  wraplength=320).grid(row=6, column=0, columnspan=2,
                                       sticky="w", **pad)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(300, self._drain_status)

        if autostarted:
            self.toggle_server()
            root.iconify()

    # ---- acciones -------------------------------------------------------

    def toggle_server(self):
        if self.server.running:
            self.server.stop()
            self.status_var.set("● Detenido")
            self.status_label.configure(foreground="#b00020")
            self.toggle_btn.configure(text="Iniciar servidor")
            self.url_var.set("—")
            self.port_entry.configure(state="normal")
            self.pw_entry.configure(state="normal")
            return

        try:
            port = int(self.port_var.get())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Puerto inválido.")
            return
        password = self.pw_var.get().strip()
        if not password:
            messagebox.showerror(APP_NAME, "La contraseña no puede estar vacía.")
            return

        try:
            self.server.start(port, password)
        except Exception as exc:
            messagebox.showerror(
                APP_NAME,
                f"No se pudo iniciar el servidor en el puerto {port}:\n{exc}")
            return

        self.cfg.update({"port": port, "password": password})
        save_config(self.cfg)
        self.status_var.set("● Activo – esperando conexiones")
        self.status_label.configure(foreground="#1b7e3c")
        self.toggle_btn.configure(text="Detener servidor")
        self.url_var.set(f"http://{local_ip()}:{port}")
        self.port_entry.configure(state="disabled")
        self.pw_entry.configure(state="disabled")

    def toggle_autostart(self):
        try:
            set_autostart(self.auto_var.get())
        except OSError as exc:
            messagebox.showerror(APP_NAME,
                                 f"No se pudo cambiar el inicio automático:\n{exc}")
            self.auto_var.set(get_autostart())

    def on_close(self):
        if self.server.running:
            self.server.stop()
        self.root.destroy()

    # ---- estado desde el hilo del servidor -------------------------------

    def _queue_status(self, msg):
        with self._status_lock:
            self._pending_status.append(msg)

    def _drain_status(self):
        with self._status_lock:
            pending, self._pending_status = self._pending_status, []
        if pending:
            self.log_var.set(pending[-1])
        self.root.after(300, self._drain_status)


def main():
    autostarted = "--autostart" in sys.argv
    root = tk.Tk()
    HostApp(root, autostarted=autostarted)
    root.mainloop()


if __name__ == "__main__":
    main()
