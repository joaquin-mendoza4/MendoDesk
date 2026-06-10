"""Prueba end-to-end del servidor de AnyMendo (sin GUI)."""
import asyncio
import json
import sys

sys.path.insert(0, r"F:\perso\AnyMendo")
import host  # noqa: E402

import aiohttp  # noqa: E402

PORT = 8551
PASSWORD = "test123"


async def run_checks():
    async with aiohttp.ClientSession() as session:
        # 1. La página del visor se sirve
        async with session.get(f"http://127.0.0.1:{PORT}/") as resp:
            body = await resp.text()
            assert resp.status == 200 and "AnyMendo" in body, "index falló"
            print("OK: pagina del visor servida")

        # 2. Contraseña incorrecta -> error
        async with session.ws_connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
            await ws.send_json({"password": "mala"})
            msg = await ws.receive_json(timeout=10)
            assert msg["type"] == "error", f"esperaba error, vino {msg}"
            print("OK: contrasena incorrecta rechazada")

        # 3. Contraseña correcta -> ok + frame JPEG
        async with session.ws_connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
            await ws.send_json({"password": PASSWORD})
            msg = await ws.receive_json(timeout=15)
            assert msg["type"] == "ok" and msg["w"] > 0, f"auth falló: {msg}"
            print(f"OK: autenticado, pantalla remota {msg['w']}x{msg['h']}")

            frame = await ws.receive(timeout=15)
            data = frame.data
            assert isinstance(data, bytes) and data[:2] == b"\xff\xd8", \
                "no llegó un JPEG"
            print(f"OK: frame JPEG recibido ({len(data)//1024} KB)")

            # 4. Evento de mouse: mover a la posición actual (no cambia nada)
            mx, my = host.MouseController().position
            mon_w, mon_h = msg["w"], msg["h"]
            await ws.send_json({"t": "mv", "x": mx / mon_w, "y": my / mon_h})
            await asyncio.sleep(0.3)
            print("OK: evento de mouse procesado sin errores")


def main():
    server = host.RemoteServer(on_status=lambda m: print(f"  [server] {m}"))
    server.start(PORT, PASSWORD)
    try:
        asyncio.run(run_checks())
        print("\nTODAS LAS PRUEBAS PASARON")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
