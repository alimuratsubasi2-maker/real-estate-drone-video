"""Drone-style parcel video for real-estate listings, without a drone.

TKGM parcel polygon -> Esri satellite mosaic -> three.js scene (glowing parcel
outline, area label, optional 3D house) -> camera descends and orbits ->
1080x1920 MP4 with title card and watermark.

usage:
  python drone.py --il Muğla --ilce Bodrum --mahalle Bitez --ada 204 --parsel 3
  python drone.py --parcel-json ornek_parsel.json --house ev.glb --out cikti/video.mp4
"""
import argparse
import http.server
import io
import json
import math
import os
import shutil
import subprocess
import threading
import urllib.request
from functools import partial

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

import tkgm

HERE = os.path.dirname(os.path.abspath(__file__))
TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
FONT_REG = "C:/Windows/Fonts/segoeui.ttf"
W, H, FPS = 1080, 1920, 30


# ── Geo helpers ───────────────────────────────────────────────

def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def tile_to_lonlat(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def to_local(lon, lat, lon0, lat0):
    """Equirectangular metres around the parcel centre (x east, z south)."""
    return (lon - lon0) * math.cos(math.radians(lat0)) * 111320, -(lat - lat0) * 110540


def polygon_area_m2(pts):
    return abs(sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(pts, pts[1:] + pts[:1]))) / 2


# ── Satellite mosaic ──────────────────────────────────────────

def fetch_tile(z, x, y, cache):
    path = os.path.join(cache, f"{z}_{x}_{y}.jpg")
    if not os.path.exists(path):
        req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            open(path, "wb").write(r.read())
    return Image.open(path).convert("RGB")


def is_placeholder(tile):
    """Esri serves a flat grey 'Map data not yet available' tile where imagery is missing."""
    small = tile.convert("L").resize((32, 32))
    px = list(small.getdata())
    mean = sum(px) / len(px)
    return sum((v - mean) ** 2 for v in px) / len(px) < 60 and mean > 150


def build_best_mosaic(lon0, lat0, half_m, zoom, cache):
    """Highest zoom where imagery actually exists around the parcel."""
    for z in range(zoom, 14, -1):
        cx, cy = lonlat_to_tile(lon0, lat0, z)
        probe = [fetch_tile(z, int(cx) + dx, int(cy) + dy, cache) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        if sum(is_placeholder(t) for t in probe) <= 1:
            print("uydu zoom:", z, flush=True)
            return build_mosaic(lon0, lat0, half_m, z, cache)
    return build_mosaic(lon0, lat0, half_m, 15, cache)


def build_mosaic(lon0, lat0, half_m, zoom, cache):
    dlon = half_m / (111320 * math.cos(math.radians(lat0)))
    dlat = half_m / 110540
    x0, y0 = lonlat_to_tile(lon0 - dlon, lat0 + dlat, zoom)
    x1, y1 = lonlat_to_tile(lon0 + dlon, lat0 - dlat, zoom)
    tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
    img = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            img.paste(fetch_tile(zoom, tx, ty, cache), ((tx - tx0) * 256, (ty - ty0) * 256))
    west, north = tile_to_lonlat(tx0, ty0, zoom)
    east, south = tile_to_lonlat(tx1 + 1, ty1 + 1, zoom)
    return img, (west, north, east, south)


# ── Overlay (title card + watermark) ──────────────────────────

def make_overlay(path, title, subtitle, info):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shade = Image.new("RGBA", (W, 520), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(520):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(170 * (1 - y / 520) ** 1.6)))
    ov.alpha_composite(shade, (0, 0))
    d = ImageDraw.Draw(ov)
    f_title = ImageFont.truetype(FONT_BOLD, 78)
    f_sub = ImageFont.truetype(FONT_BOLD, 44)
    f_info = ImageFont.truetype(FONT_REG, 34)
    f_wm = ImageFont.truetype(FONT_BOLD, 30)
    d.text((70, 150), title, font=f_title, fill="white", stroke_width=3, stroke_fill=(0, 0, 0, 160))
    d.text((72, 255), subtitle, font=f_sub, fill=(255, 225, 77), stroke_width=2, stroke_fill=(0, 0, 0, 160))
    d.text((72, 320), info, font=f_info, fill=(235, 235, 235), stroke_width=2, stroke_fill=(0, 0, 0, 140))
    wm = "made with NovaDijitalTR"
    tw = d.textlength(wm, font=f_wm)
    d.rounded_rectangle([(W - tw - 70, H - 120), (W - 40, H - 66)], radius=14, fill=(0, 0, 0, 90))
    d.text((W - tw - 55, H - 116), wm, font=f_wm, fill=(255, 255, 255, 150))
    d.text((40, H - 50), "Uydu görüntüsü: Esri, Maxar · Parsel: TKGM", font=ImageFont.truetype(FONT_REG, 22), fill=(255, 255, 255, 120))
    ov.save(path)


# ── Main ──────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--il"); ap.add_argument("--ilce"); ap.add_argument("--mahalle")
    ap.add_argument("--ada"); ap.add_argument("--parsel")
    ap.add_argument("--parcel-json", help="Önceden kaydedilmiş TKGM GeoJSON")
    ap.add_argument("--house", help="Parselin üstüne konacak ev modeli (.glb)")
    ap.add_argument("--house-rotation", type=float, default=0.0, help="Derece")
    ap.add_argument("--title", help="Başlık (varsayılan: '<ada> Ada <parsel> Parsel')")
    ap.add_argument("--seconds", type=float, default=12)
    ap.add_argument("--zoom", type=int, default=19)
    ap.add_argument("--out", default=os.path.join(HERE, "cikti", "drone.mp4"))
    a = ap.parse_args()

    if a.parcel_json:
        feature = json.load(open(a.parcel_json, encoding="utf-8"))
    else:
        feature = tkgm.lookup(a.il, a.ilce, a.mahalle, a.ada, a.parsel)
    props = feature["properties"]
    ring = feature["geometry"]["coordinates"][0]
    if feature["geometry"]["type"] == "MultiPolygon":
        ring = feature["geometry"]["coordinates"][0][0]
    lon0 = sum(p[0] for p in ring) / len(ring)
    lat0 = sum(p[1] for p in ring) / len(ring)
    poly = [to_local(lon, lat, lon0, lat0) for lon, lat in ring]
    if poly[0] == poly[-1]:
        poly = poly[:-1]
    extent = max(max(abs(x), abs(z)) for x, z in poly)
    radius = max(45.0, extent * 1.6)

    work = os.path.join(HERE, "_work")
    frames = os.path.join(work, "frames")
    cache = os.path.join(HERE, "_tiles")
    shutil.rmtree(frames, ignore_errors=True)
    os.makedirs(frames, exist_ok=True)
    os.makedirs(cache, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)

    mosaic, (west, north, east, south) = build_best_mosaic(lon0, lat0, radius * 5, a.zoom, cache)
    if max(mosaic.size) > 8192:
        mosaic.thumbnail((8192, 8192))
    mosaic.save(os.path.join(work, "ground.jpg"), quality=92)
    gx0, gz0 = to_local(west, north, lon0, lat0)
    gx1, gz1 = to_local(east, south, lon0, lat0)

    alan_txt = props.get("alan", "")
    try:
        alan_num = float(alan_txt.replace(".", "").replace(",", "."))
    except ValueError:
        alan_num = polygon_area_m2(poly)
    area_text = f"{round(alan_num):,} m²".replace(",", ".")

    house_cfg = None
    if a.house:
        shutil.copy(a.house, os.path.join(work, "house.glb"))
        house_cfg = {"url": "house.glb", "footprint": max(8.0, math.sqrt(alan_num) * 0.45), "rotation": math.radians(a.house_rotation)}

    cfg = {
        "width": W, "height": H,
        "texture": "ground.jpg",
        "ground": {"width": gx1 - gx0, "height": gz1 - gz0, "cx": (gx0 + gx1) / 2, "cz": (gz0 + gz1) / 2},
        "polygon": poly,
        "centroid": [sum(x for x, _ in poly) / len(poly), sum(z for _, z in poly) / len(poly)],
        "radius": radius,
        "lineWidth": max(0.35, radius / 110),
        "wallHeight": max(2.0, radius / 18),
        "labelHeight": max(14.0, radius / 2.6),
        "labelSize": radius * 0.42,
        "areaText": area_text,
        "subText": props.get("nitelik", ""),
        "startYaw": math.radians(-60),
        "orbit": math.radians(200),
        "house": house_cfg,
    }
    shutil.copy(os.path.join(HERE, "render.html"), os.path.join(work, "render.html"))

    # Local server: WebGL refuses textures from file:// pages
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=work)
    handler.log_message = lambda *args: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    total = int(a.seconds * FPS)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--ignore-gpu-blocklist", "--enable-gpu"])
        page = browser.new_page(viewport={"width": W, "height": H})
        page.add_init_script(f"window.SCENE_CONFIG = {json.dumps(cfg)};")
        page.on("pageerror", lambda e: print("pageerror:", e))
        page.goto(f"http://127.0.0.1:{port}/render.html")
        page.wait_for_function("window.READY === true", timeout=120000)
        canvas = page.locator("canvas")
        for i in range(total):
            page.evaluate(f"window.renderAt({i / (total - 1)})")
            canvas.screenshot(path=os.path.join(frames, f"f{i:04d}.jpg"), type="jpeg", quality=92)
            if i % 60 == 0:
                print(f"kare {i}/{total}", flush=True)
        browser.close()
    srv.shutdown()

    title = a.title or f"{props.get('adaNo')} Ada {props.get('parselNo')} Parsel"
    subtitle = f"{props.get('mahalleAd', '')} · {props.get('ilceAd', '')} / {props.get('ilAd', '')}"
    info = f"{area_text} · {props.get('nitelik', '')}" + (f" · {props.get('mevkii')} mevkii" if props.get("mevkii") else "")
    overlay = os.path.join(work, "overlay.png")
    make_overlay(overlay, title, subtitle, info)

    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", os.path.join(frames, "f%04d.jpg"), "-i", overlay,
        "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-movflags", "+faststart", a.out,
    ], check=True)
    print("VIDEO:", os.path.abspath(a.out))


if __name__ == "__main__":
    main()
