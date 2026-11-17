import os, sys, http.server, threading
from functools import partial
from playwright.sync_api import sync_playwright
from PIL import Image
D = sys.argv[1]
src = open(os.path.join(D, "gebizli-daire-ici.html"), encoding="utf-8").read()
os.makedirs(os.path.join(D, "_prev"), exist_ok=True)
open(os.path.join(D, "_prev", "index.html"), "w", encoding="utf-8").write('<!doctype html><html><head><meta charset="utf-8"></head><body>' + src + "</body></html>")
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Q, directory=os.path.join(D, "_prev")))
threading.Thread(target=srv.serve_forever, daemon=True).start()
views = sys.argv[2].split(",")
shots = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True, args=["--ignore-gpu-blocklist", "--enable-gpu"])
    pg = b.new_page(viewport={"width": 800, "height": 600})
    pg.add_init_script("window.RENDER_MODE = true;")
    pg.on("pageerror", lambda e: print("pageerror:", e))
    pg.on("console", lambda m: print("console:", m.text) if m.type == "error" else None)
    pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
    pg.wait_for_function("window.READY === true", timeout=60000)
    for v in views:
        pg.evaluate(f"window.showView('{v}')")
        f = os.path.join(D, "_prev", v + ".jpg"); pg.locator("canvas").screenshot(path=f, type="jpeg", quality=85); shots.append(f)
    b.close()
cols = 3; rows = (len(shots) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 800, rows * 600))
for i, f in enumerate(shots): sheet.paste(Image.open(f), ((i % cols) * 800, (i // cols) * 600))
sheet.save(os.path.join(D, "_prev", "sheet.jpg"), quality=80)
print("ok")
