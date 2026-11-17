# Emlak Drone Video

Drone ekibi olmadan, ilanlar için parselin etrafında dönen "drone" videosu (1080×1920, 12 sn).

## Kullanım
```
python drone.py --il Muğla --ilce Bodrum --mahalle Bitez --ada 204 --parsel 3
python drone.py --parcel-json ornek_parsel.json --house ev.glb --house-rotation 30 --out cikti/ilan.mp4
```
- Parsel sınırı ve alanı TKGM'den (`tkgm.py`), uydu görüntüsü Esri World Imagery (görüntü olmayan zoom otomatik atlanır).
- `--house`: parselin ortasına konan ev modeli (.glb), parsel büyüklüğüne göre ölçeklenir.
- Ev fotoğrafından model: `..\3D-Uretici-Lokal\hunyuan_uret.py` → `hunyuan_mesh(foto)` → .glb.
- Videoda "made with NovaDijitalTR" filigranı ve "Esri, Maxar · TKGM" kaynak yazısı var.
- Çıktılar: `cikti/` (masaüstünde `Emlak Drone Video` klasörüne de kopyalanır).
