"""TKGM parcel lookup: il / ilçe / mahalle names + ada / parsel -> GeoJSON polygon and attributes.

usage: python tkgm.py "Muğla" "Bodrum" "Bitez" 204 3 [out.json]
"""
import json
import sys
import unicodedata
import urllib.request

API = "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3/api"


def get(path):
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def norm(s: str) -> str:
    """Case/diacritic-insensitive Turkish name match (İ/ı, ş, ğ ...)."""
    s = s.replace("İ", "i").replace("I", "ı").lower()
    s = s.translate(str.maketrans("ıçğöşü", "icgosu"))
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if c.isalnum())


def pick(features, name, level):
    want = norm(name)
    for f in features:
        if norm(f["properties"]["text"]) == want:
            return f["properties"]["id"]
    for f in features:
        if want in norm(f["properties"]["text"]):
            return f["properties"]["id"]
    names = ", ".join(f["properties"]["text"] for f in features)
    raise SystemExit(f"{level} '{name}' bulunamadı. Seçenekler: {names}")


def lookup(il, ilce, mahalle, ada, parsel):
    il_id = pick(get("idariYapi/ilListe")["features"], il, "İl")
    ilce_id = pick(get(f"idariYapi/ilceListe/{il_id}")["features"], ilce, "İlçe")
    mah_id = pick(get(f"idariYapi/mahalleListe/{ilce_id}")["features"], mahalle, "Mahalle")
    return get(f"parsel/{mah_id}/{ada}/{parsel}")


if __name__ == "__main__":
    il, ilce, mahalle, ada, parsel = sys.argv[1:6]
    out = sys.argv[6] if len(sys.argv) > 6 else "parsel.json"
    feature = lookup(il, ilce, mahalle, ada, parsel)
    json.dump(feature, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    p = feature["properties"]
    print(json.dumps({k: p.get(k) for k in ("ilAd", "ilceAd", "mahalleAd", "adaNo", "parselNo", "alan", "nitelik", "mevkii", "pafta")}, ensure_ascii=False))
