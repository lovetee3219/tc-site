"""
下載 ATCF 格式的路徑檔（a-deck = 各機構／模式預報、b-deck = 最佳路徑），
轉成網站使用的 data/tracks.json。

ATCF 是美國 NHC／JTWC 使用、也被許多研究單位採用的標準文字格式，
一行代表「某個模式、某次起報、某個預報時距」的一筆資料：
  BASIN, CY, YYYYMMDDHH, TECHNUM, TECH, TAU, LAT, LON, VMAX, MSLP, ...
例：WP, 25, 2026102000, 03, AVNO, 24, 215N, 1302E, 95, 955, TY, ...

使用方式：
  python scripts/fetch_tracks.py            # 依 SOURCES 下載
  python scripts/fetch_tracks.py --demo     # 產生示範資料（不需網路）
"""
import gzip
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "tracks.json"

# ── 要抓的檔案清單 ──────────────────────────────────────────────
# 每個熱帶氣旋各有一組 a-deck、b-deck，檔名規則：
#   a{海域}{編號}{年}.dat(.gz)   例：aal092026.dat.gz、awp252026.dat
# 公開目錄（使用前請確認網址仍有效、並遵守各站使用規範）：
#   NHC（大西洋 al、東太平洋 ep、中太平洋 cp）
#     a-deck: https://ftp.nhc.noaa.gov/atcf/aid_public/
#     b-deck: https://ftp.nhc.noaa.gov/atcf/btk/
#   其他海域（西北太平洋 wp、北印度洋 io、南半球 sh）可找研究單位的公開鏡像，
#   例如 UCAR/NCAR RAL 的熱帶氣旋導引資料，網址請到其網站確認。
SOURCES = [
    # {"storm": "AL092026", "name": "颶風範例",
    #  "adeck": "https://ftp.nhc.noaa.gov/atcf/aid_public/aal092026.dat.gz",
    #  "bdeck": "https://ftp.nhc.noaa.gov/atcf/btk/bal092026.dat"},
]

# ── 模式代碼 → 顯示名稱與分類 ──────────────────────────────────
# category: official（官方）、nwp（數值模式）、ai（AI 模式）
# 代碼會隨年度增減，請以資料來源當季的說明為準；不在表內的代碼會被略過。
MODELS = {
    "OFCL": ("NHC 官方", "official"),
    "JTWC": ("JTWC 官方", "official"),
    "AVNO": ("GFS", "nwp"),
    "AVNI": ("GFS（插值）", "nwp"),
    "EMX":  ("ECMWF IFS", "nwp"),
    "EMXI": ("ECMWF IFS（插值）", "nwp"),
    "ECMF": ("ECMWF IFS", "nwp"),
    "EGRR": ("UKMET", "nwp"),
    "UKM":  ("UKMET", "nwp"),
    "CMC":  ("加拿大 GDPS", "nwp"),
    "NVGM": ("美國海軍 NAVGEM", "nwp"),
    "HAFA": ("HAFS-A", "nwp"),
    "HAFB": ("HAFS-B", "nwp"),
    "AEMN": ("GEFS 系集平均", "nwp"),
    "EEMN": ("ECMWF 系集平均", "nwp"),
    # AI 模式的 ATCF 代碼尚未統一，以下為佔位，請依資料來源實際使用的代碼修改
    "AIFS": ("ECMWF AIFS", "ai"),
    "GDMI": ("Google DeepMind", "ai"),
}


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "tc-site/0.1 (personal project)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def parse_latlon(lat: str, lon: str):
    """'215N' → 21.5，'1302E' → 130.2（南緯、西經為負值）"""
    la = int(lat[:-1]) / 10.0 * (1 if lat[-1] == "N" else -1)
    lo = int(lon[:-1]) / 10.0 * (1 if lon[-1] == "E" else -1)
    return round(la, 1), round(lo, 1)


def parse_atcf(text: str):
    """回傳 {(tech, init): [點...]}；同一時距只留一筆（風半徑會讓同一點重複多行）。"""
    runs = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 9 or not f[6] or not f[7]:
            continue
        try:
            init, tech, tau = f[2], f[4], int(f[5])
            lat, lon = parse_latlon(f[6], f[7])
        except (ValueError, IndexError):
            continue
        vmax = int(f[8]) if f[8].isdigit() and int(f[8]) > 0 else None
        mslp = int(f[9]) if len(f) > 9 and f[9].isdigit() and int(f[9]) > 0 else None
        pts = runs.setdefault((tech, init), {})
        if tau not in pts:
            pts[tau] = {"tau": tau, "lat": lat, "lon": lon, "vmax": vmax, "mslp": mslp}
    return {k: sorted(v.values(), key=lambda p: p["tau"]) for k, v in runs.items()}


def latest_runs(runs):
    """每個模式只保留最新一次起報。"""
    best = {}
    for (tech, init), pts in runs.items():
        if tech in MODELS and (tech not in best or init > best[tech][0]):
            best[tech] = (init, pts)
    return best


def iso(yyyymmddhh: str) -> str:
    t = datetime.strptime(yyyymmddhh, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return t.isoformat().replace("+00:00", "Z")


def build_storm(src):
    storm = {"id": src["storm"], "name": src.get("name", src["storm"]), "best": [], "forecasts": []}
    if src.get("bdeck"):
        b = parse_atcf(fetch_text(src["bdeck"]))
        pts = sorted(((init, p[0]) for (_, init), p in b.items() if p), key=lambda x: x[0])
        storm["best"] = [{**p, "time": iso(init)} for init, p in pts]
    if src.get("adeck"):
        for tech, (init, pts) in latest_runs(parse_atcf(fetch_text(src["adeck"]))).items():
            if len(pts) < 2:
                continue
            label, cat = MODELS[tech]
            storm["forecasts"].append({"model": tech, "label": label, "category": cat,
                                       "init": iso(init), "points": pts})
    return storm


def demo():
    """產生一個「虛構」颱風，讓網站在還沒接上真實資料時也看得到效果。"""
    t0 = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t0 -= timedelta(hours=t0.hour % 6)
    best = []
    for i in range(6, -1, -1):
        k = 6 - i
        best.append({"time": (t0 - timedelta(hours=6 * i)).isoformat().replace("+00:00", "Z"),
                     "lat": round(17.0 + 0.5 * k, 1), "lon": round(134.0 - 0.9 * k, 1),
                     "vmax": 45 + 10 * k, "mslp": 995 - 8 * k})
    lat0, lon0 = best[-1]["lat"], best[-1]["lon"]
    spread = {"JTWC": (1.0, 1.5), "AVNO": (1.3, 1.2), "EMX": (0.8, 1.7), "EGRR": (0.9, 1.6),
              "HAFA": (1.15, 1.4), "AIFS": (0.95, 1.6), "GDMI": (1.05, 1.55)}
    fc = []
    for tech, (dn, dw) in spread.items():
        label, cat = MODELS[tech]
        pts = []
        for h in range(0, 121, 12):
            s = h / 12
            pts.append({"tau": h,
                        "lat": round(lat0 + dn * s + (0.04 * s * s if tech == "AVNO" else 0), 1),
                        "lon": round(lon0 - dw * s + 0.03 * s * s, 1),
                        "vmax": max(30, int(105 + 5 * s - 3 * s * s)), "mslp": None})
        fc.append({"model": tech, "label": label, "category": cat, "init": best[-1]["time"], "points": pts})
    return [{"id": "WP992026", "name": "示範颱風（虛構資料）", "demo": True, "best": best, "forecasts": fc}]


def main():
    if "--demo" in sys.argv or not SOURCES:
        storms = demo()
        print("SOURCES 為空或指定 --demo，輸出示範資料。")
    else:
        storms = []
        for src in SOURCES:
            try:
                storms.append(build_storm(src))
                print("OK", src["storm"])
            except Exception as e:  # 單一來源失敗不影響其他
                print("失敗", src["storm"], e)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "storms": storms}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已寫入", OUT)


if __name__ == "__main__":
    main()
