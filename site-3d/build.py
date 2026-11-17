"""3D site model + drone video for an existing housing complex.

Satellite mosaic as ground, apartment blocks extruded from hand-traced
footprints, trees detected from canopy pixels, listed unit highlighted.
Writes web/ (interactive three.js page) and cikti/<name>.mp4 (1080x1920).

usage:
  python build.py                 # ground + scene.json + video
  python build.py --no-video      # only refresh web/
"""
import argparse
import http.server
import json
import math
import os
import random
import shutil
import subprocess
import sys
import threading
from functools import partial

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from drone import build_best_mosaic, to_local  # noqa: E402

FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
FONT_REG = "C:/Windows/Fonts/segoeui.ttf"
W, H, FPS = 1080, 1920, 30


def corners(b):
    c, s = math.cos(b["rot"]), math.sin(b["rot"])
    out = []
    for lx, lz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, z = lx * b["w"] / 2, lz * b["d"] / 2
        # three.js rotation.y: local +x -> world (cos, -sin)
        out.append((b["x"] + x * c + z * s, b["z"] - x * s + z * c))
    return out


def convex_hull(pts):
    pts = sorted(set(pts))
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def inflate(poly, m):
    cx = sum(x for x, _ in poly) / len(poly)
    cz = sum(z for _, z in poly) / len(poly)
    out = []
    for x, z in poly:
        d = math.hypot(x - cx, z - cz) or 1
        out.append((round(x + (x - cx) / d * m, 2), round(z + (z - cz) / d * m, 2)))
    return out


def inside_any(x, z, blds, pad):
    for b in blds:
        c, s = math.cos(b["rot"]), math.sin(b["rot"])
        dx, dz = x - b["x"], z - b["z"]
        lx, lz = dx * c - dz * s, dx * s + dz * c
        if abs(lx) < b["w"] / 2 + pad and abs(lz) < b["d"] / 2 + pad:
            return True
    return False


def detect_trees(img, bounds, lon0, lat0, blds, extra_blocks, limit):
    west, north, east, south = bounds
    gx0, gz0 = to_local(west, north, lon0, lat0)
    gx1, gz1 = to_local(east, south, lon0, lat0)
    a = np.asarray(img).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = (r + g + b) / 3
    canopy = (g > r + 6) & (g >= b) & (lum < 78)
    h, w = canopy.shape
    ppm = w / (gx1 - gx0)
    cell = 5.0
    step = int(cell * ppm)
    rnd = random.Random(7)
    trees = []
    for py in range(0, h - step, step):
        for px in range(0, w - step, step):
            if canopy[py:py + step, px:px + step].mean() < 0.55:
                continue
            x = gx0 + (px + step / 2) / ppm + rnd.uniform(-1.2, 1.2)
            z = gz0 + (py + step / 2) / ppm + rnd.uniform(-1.2, 1.2)
            if inside_any(x, z, blds, 3) or inside_any(x, z, extra_blocks, 0):
                continue
            trees.append([round(x, 1), round(z, 1), round(rnd.uniform(0.8, 1.35), 2)])
    rnd.shuffle(trees)
    return trees[:limit]


def make_overlay(path, s):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shade = Image.new("RGBA", (W, 560), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(560):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(175 * (1 - y / 560) ** 1.5)))
    ov.alpha_composite(shade, (0, 0))
    d = ImageDraw.Draw(ov)
    f_loc = ImageFont.truetype(FONT_BOLD, 36)
    f_sub = ImageFont.truetype(FONT_BOLD, 44)
    f_title = ImageFont.truetype(FONT_BOLD, 96)
    f_feat = ImageFont.truetype(FONT_BOLD, 30)
    f_wm = ImageFont.truetype(FONT_BOLD, 30)

    def center(y, text, font, fill, stroke=3):
        tw = d.textlength(text, font=font)
        d.text(((W - tw) / 2, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 170))

    center(120, s["location"].upper(), f_loc, (255, 196, 64))
    center(178, s["subtitle"].upper(), f_sub, "white")
    center(240, s["title"].upper(), f_title, "white", 4)
    center(370, "  |  ".join(f.upper() for f in s["features"]), f_feat, (240, 240, 240), 2)

    wm = "made with NovaDijitalTR"
    tw = d.textlength(wm, font=f_wm)
    d.rounded_rectangle([(W - tw - 70, H - 120), (W - 40, H - 66)], radius=14, fill=(0, 0, 0, 90))
    d.text((W - tw - 55, H - 116), wm, font=f_wm, fill=(255, 255, 255, 150))
    d.text((40, H - 50), "Uydu görüntüsü: Esri, Maxar · 3D bloklar temsilidir", font=ImageFont.truetype(FONT_REG, 22), fill=(255, 255, 255, 120))
    ov.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.path.join(HERE, "site.json"))
    ap.add_argument("--seconds", type=float, default=16)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--preview", help="Virgülle t değerleri (örn. 0,0.5,1): sadece bu kareleri _work/ altına çizer")
    a = ap.parse_args()

    site = json.load(open(a.site, encoding="utf-8"))
    lat0, lon0 = site["center"]
    web = os.path.join(HERE, "web")
    os.makedirs(web, exist_ok=True)
    cache = os.path.join(os.path.dirname(HERE), "_tiles")
    os.makedirs(cache, exist_ok=True)

    img, bounds = build_best_mosaic(lon0, lat0, site.get("half", 210), 19, cache)
    if max(img.size) > 4096:
        img.thumbnail((4096, 4096))
    img.save(os.path.join(web, "ground.jpg"), quality=88)
    west, north, east, south = bounds
    gx0, gz0 = to_local(west, north, lon0, lat0)
    gx1, gz1 = to_local(east, south, lon0, lat0)

    blds = site["buildings"]
    own = [b for b in blds if b.get("site")]
    hull_pts = [p for b in own for p in corners(b)]
    px, pz, pr = site["pool"]["x"], site["pool"]["z"], site["pool"]["r"]
    hull_pts += [(px + pr * math.cos(t / 8 * math.tau), pz + pr * math.sin(t / 8 * math.tau)) for t in range(8)]
    boundary = inflate(convex_hull([(round(x, 2), round(z, 2)) for x, z in hull_pts]), 5)

    trees = detect_trees(img, bounds, lon0, lat0, blds, site.get("low", []), 700)
    print("ağaç:", len(trees), flush=True)

    scene = {
        "ground": {"texture": "ground.jpg", "width": gx1 - gx0, "height": gz1 - gz0, "cx": (gx0 + gx1) / 2, "cz": (gz0 + gz1) / 2},
        "buildings": blds,
        "low": site.get("low", []),
        "pool": site["pool"],
        "unit": site["unit"],
        "boundary": boundary,
        "trees": trees,
        "info": {k: site[k] for k in ("title", "subtitle", "location", "features")},
    }
    json.dump(scene, open(os.path.join(web, "scene.json"), "w", encoding="utf-8"), ensure_ascii=False)
    shutil.copy(os.path.join(HERE, "site.html"), os.path.join(web, "index.html"))
    print("WEB:", web)
    if a.no_video:
        return

    work = os.path.join(HERE, "_work")
    frames = os.path.join(work, "frames")
    shutil.rmtree(frames, ignore_errors=True)
    os.makedirs(frames, exist_ok=True)

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=web)
    handler.log_message = lambda *args: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    total = int(a.seconds * FPS)
    ts = [float(t) for t in a.preview.split(",")] if a.preview else [i / (total - 1) for i in range(total)]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--ignore-gpu-blocklist", "--enable-gpu"])
        page = browser.new_page(viewport={"width": W, "height": H})
        page.add_init_script("window.RENDER_MODE = true;")
        page.on("pageerror", lambda e: print("pageerror:", e))
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_function("window.READY === true", timeout=180000)
        canvas = page.locator("canvas")
        for i, t in enumerate(ts):
            page.evaluate(f"window.renderAt({t})")
            name = f"preview_{t:.2f}.jpg" if a.preview else f"f{i:04d}.jpg"
            canvas.screenshot(path=os.path.join(work if a.preview else frames, name), type="jpeg", quality=92)
            if i % 60 == 0:
                print(f"kare {i}/{len(ts)}", flush=True)
        browser.close()
    srv.shutdown()
    if a.preview:
        print("ÖNİZLEME:", work)
        return

    overlay = os.path.join(work, "overlay.png")
    make_overlay(overlay, site)
    out = os.path.join(HERE, "cikti", site.get("name", "site") + "_drone.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", os.path.join(frames, "f%04d.jpg"), "-i", overlay,
        "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-movflags", "+faststart", out,
    ], check=True)
    print("VIDEO:", out)


if __name__ == "__main__":
    main()
