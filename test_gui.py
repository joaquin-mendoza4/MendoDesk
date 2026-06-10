import sys
sys.path.insert(0, r"F:\perso\AnyMendo")
import host
import tkinter as tk

# GUI se construye sin errores (ventana oculta, se destruye sola)
root = tk.Tk()
root.withdraw()
app = host.HostApp(root)
root.after(400, root.destroy)
root.mainloop()
print("OK: GUI construida y cerrada")

# Round-trip de inicio automatico en HKCU\...\Run
assert host.get_autostart() is False
host.set_autostart(True)
assert host.get_autostart() is True
host.set_autostart(False)
assert host.get_autostart() is False
print("OK: inicio automatico se activa y desactiva en el registro")
print("Comando que registra:", host._autostart_command())
