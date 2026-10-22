"""
下載 ATCF 格式的路徑檔（a-deck = 各機構／模式預報、b-deck = 最佳路徑），
轉成網站使用的 data/tracks.json。

ATCF 是美國 NHC／JTWC 使用、也被許多研究單位採用的標準文字格式，
一行代表「某個模式、某次起報、某個預報時距」的一筆資料：
  BASIN, CY, YYYYMMDDHH, TECHNUM, TECH, TAU, LAT, LON, VMAX, MSLP, ...
例：WP, 25, 2026102000, 03, AVNO, 24, 215N, 1302E, 95, 955, TY, ...

另外會合併以下官方與模式來源（各自寫在 src_*.py）：
  JTWC（src_jtwc.py）、JMA（src_jma.py）、CWA（src_cwa.py，需授權碼）、ECMWF（src_ecmwf.py）
任何一個來源失敗都不影響其他來源。

使用方式：
  python scripts/fetch_tracks.py            # 依 SOURCES 下載，並合併各官方來源
  python scripts/fetch_tracks.py --demo     # 產生示範資料（不需網路）
"""
import gzip
import json
import math
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
    # 名稱會自動從 ATCF 檔讀取，不需要再填 "name"
    {"storm": "WP292026",
     "adeck": "https://hurricanes.ral.ucar.edu/repository/data/adecks_open/awp292026.dat",
     # b-deck 可以填多個網址，程式會依序嘗試，抓到為止
     "bdeck": ["https://hurricanes.ral.ucar.edu/repository/data/bdecks_open/bwp292026.dat",
               "https://hurricanes.ral.ucar.edu/repository/data/bdecks_open/2026/bwp292026.dat"]},
    # {"storm": "AL092026",
    #  "adeck": "https://ftp.nhc.noaa.gov/atcf/aid_public/aal092026.dat.gz",
    #  "bdeck": "https://ftp.nhc.noaa.gov/atcf/btk/bal092026.dat"},
]

# ── 模式代碼 → 顯示名稱與分類 ──────────────────────────────────
# category: official（官方）、nwp（數值模式）、ai（AI 模式）
# 代碼會隨年度增減，請以資料來源當季的說明為準；不在表內的代碼會被略過。
MODELS = {
    # 官方預報
    "OFCL": ("NHC 官方", "official"),
    "JTWC": ("JTWC 官方", "official"),
    "RJTD": ("JMA 官方", "official"),
    "RCTP": ("CWA 官方", "official"),
    # GFS（單一預報另由 src_gfs.py 補上）
    "AVNO": ("GFS", "nwp"),
    "AEMN": ("GEFS 系集平均", "nwp"),
    # ECMWF
    "EMX":  ("ECMWF IFS", "nwp"),
    "ECMF": ("ECMWF IFS", "nwp"),
    "EEMN": ("ECMWF 系集平均", "nwp"),
}


# 系集成員：代碼開頭 → 群組名稱。同一群組在網站上用一個勾選框一起開關。
# 例：AP01~AP30 是 GEFS 的 30 個成員，AC00 是控制組。
ENSEMBLES = {
    "AP": "GEFS 系集成員", "AC": "GEFS 系集成員",
    "EP": "ECMWF 系集成員", "EC": "ECMWF 系集成員",
}


def classify(tech):
    """回傳 (顯示名稱, 分類, 系集群組)；不認得的代碼回傳 None。"""
    if tech in MODELS:
        label, cat = MODELS[tech]
        return label, cat, None
    if len(tech) == 4 and tech[2:].isdigit() and tech[:2] in ENSEMBLES:
        return tech, "ens", ENSEMBLES[tech[:2]]
    return None


def fetch_text(url) -> str:
    """url 可以是單一網址或清單；清單會依序嘗試，回傳第一個成功的。"""
    if isinstance(url, (list, tuple)):
        last = None
        for u in url:
            try:
                return fetch_text(u)
            except Exception as e:
                last = e
        raise last
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


NOT_NAMES = {"", "INVEST", "NONAME", "UNNAMED", "NAMELESS"}


def find_name(text: str):
    """從 ATCF 第 28 欄（STORMNAME）找最新一筆颱風名稱，例如 CHAMPI → Champi。"""
    best = ("", None)
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) > 27 and f[27].upper() not in NOT_NAMES and f[27].replace("-", "").isalpha():
            if f[2] >= best[0]:
                best = (f[2], f[27])
    return best[1].title() if best[1] else None


def latest_runs(runs):
    """每個模式只保留最新一次起報。"""
    best = {}
    for (tech, init), pts in runs.items():
        if classify(tech) and (tech not in best or init > best[tech][0]):
            best[tech] = (init, pts)
    return best


def iso(yyyymmddhh: str) -> str:
    t = datetime.strptime(yyyymmddhh, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return t.isoformat().replace("+00:00", "Z")


def build_storm(src):
    """a-deck 與 b-deck 分開處理：其中一個抓不到，另一個仍會顯示。"""
    storm = {"id": src["storm"], "basin": src["storm"][:2].upper(), "name": src.get("name") or src["storm"], "best": [], "forecasts": []}
    auto_name = None
    if src.get("bdeck"):
        try:
            text = fetch_text(src["bdeck"])
            auto_name = find_name(text)
            b = parse_atcf(text)
            pts = sorted(((init, p[0]) for (_, init), p in b.items() if p), key=lambda x: x[0])
            storm["best"] = [{**p, "time": iso(init)} for init, p in pts]
            print(f"  b-deck：{len(storm['best'])} 個實測點")
        except Exception as e:
            print("  b-deck 失敗：", e)
    if src.get("adeck"):
        try:
            text = fetch_text(src["adeck"])
            auto_name = auto_name or find_name(text)
            runs = parse_atcf(text)
            # CARQ = 作業單位每次的即時定位；b-deck 抓不到時，用它當實測路徑
            carq = sorted((init, pts[0]) for (tech, init), pts in runs.items() if tech == "CARQ" and pts and pts[0]["tau"] == 0)
            if carq and not storm["best"]:
                storm["best"] = [{k: v for k, v in p.items() if k != "tau"} | {"time": iso(init)} for init, p in carq]
                print(f"  b-deck 沒有資料，改用 CARQ 即時定位：{len(carq)} 個點")
            found = sorted({tech for tech, _ in runs})
            print("  a-deck 內的模式代碼：", " ".join(found))
            for tech, (init, pts) in latest_runs(runs).items():
                if len(pts) < 2:
                    continue
                label, cat, group = classify(tech)
                fc = {"model": tech, "label": label, "category": cat, "init": iso(init), "points": pts}
                if group:
                    fc["group"] = group
                storm["forecasts"].append(fc)
            shown = [f["model"] for f in storm["forecasts"] if f["category"] != "ens"]
            groups = sorted({f["group"] for f in storm["forecasts"] if f["category"] == "ens"})
            print("  會顯示的預報：", " ".join(shown) or "（無）", "｜系集：", "、".join(groups) or "（無）")
        except Exception as e:
            print("  a-deck 失敗：", e)
    if auto_name:  # 自動抓到的名稱優先，例如「Champi（WP29）」
        storm["name"] = f"{auto_name}（{src['storm'][:4]}）"
    print("  名稱：", storm["name"])
    set_current(storm)
    return storm


def set_current(storm):
    """記下颱風目前位置，用來把其他來源（JMA、CWA 等）對應到同一個颱風。"""
    if storm["best"]:
        p = storm["best"][-1]
        storm["current"] = {"lat": p["lat"], "lon": p["lon"], "time": p["time"]}
    elif storm["forecasts"]:
        f = max(storm["forecasts"], key=lambda f: f["init"])
        storm["current"] = {"lat": f["points"][0]["lat"], "lon": f["points"][0]["lon"], "time": f["init"]}


def distance_deg(a, b):
    dlon = (a["lon"] - b["lon"] + 540) % 360 - 180
    return math.hypot(a["lat"] - b["lat"], dlon * math.cos(math.radians(a["lat"])))


def attach(storms, it, create=True):
    """把一筆外部來源的預報併入對應的颱風；找不到時，create=True 才新增，否則略過。"""
    target = None
    if it["number"]:
        basin, num = it["number"]
        target = next((s for s in storms if s["id"][:2] == basin and s["id"][2:4].isdigit() and int(s["id"][2:4]) == num), None)
    if target is None:
        near = [(distance_deg(s["current"], it), s) for s in storms if s.get("current")]
        near = [x for x in near if x[0] < 5]   # 5 度以內視為同一個颱風
        if near:
            target = min(near, key=lambda x: x[0])[1]
    if target is None and not create:
        return None
    if target is None:
        sid = f"{it['number'][0]}{it['number'][1]:02d}" if it["number"] else f"{it['source']}-{len(storms) + 1}"
        # 沒有 JTWC 編號的只會來自 JMA、CWA，都是西北太平洋
        basin = it["number"][0] if it["number"] else "WP"
        target = {"id": sid, "basin": basin, "name": it["name"] or sid, "best": [], "forecasts": [],
                  "current": {"lat": it["lat"], "lon": it["lon"], "time": it["time"]}}
        storms.append(target)
    codes = {f["model"] for f in it["forecasts"]}
    target["forecasts"] = [f for f in target["forecasts"] if f["model"] not in codes] + it["forecasts"]
    if not target["best"] and it["best"]:
        target["best"] = it["best"]
        set_current(target)
    if it["name"] and target["name"] == target["id"]:
        target["name"] = it["name"]
    return target


def merge_official(storms):
    """依序抓取各來源。單一來源失敗只會印出訊息，不影響其他來源。"""
    import src_cwa
    import src_ecmwf
    import src_gfs
    import src_jma
    import src_jtwc
    # 第三個值：找不到對應颱風時要不要新增一個。全部設為 True，所以其他海域的颱風也會讀進來；
    # 網站一開始只顯示西北太平洋，其他海域可在左欄勾選。
    tasks = [("JTWC", lambda: src_jtwc.fetch([s["id"] for s in storms]), True),
             ("JMA", src_jma.fetch, True), ("CWA", src_cwa.fetch, True),
             ("GFS", src_gfs.fetch, True), ("ECMWF", src_ecmwf.fetch, True)]
    for name, fn, create in tasks:
        print("處理", name)
        try:
            for it in fn():
                t = attach(storms, it, create)
                if t:
                    print(f"  → 併入 {t['id']}")
        except Exception as e:
            print(f"  {name} 失敗：", e)


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
    spread = {"JTWC": (1.0, 1.5), "AVNO": (1.3, 1.2), "ECMF": (0.8, 1.7), "AEMN": (1.15, 1.4), "EEMN": (0.9, 1.6)}
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
    return [{"id": "WP992026", "basin": "WP", "name": "示範颱風（虛構資料）", "demo": True, "best": best, "forecasts": fc}]


def main():
    storms = []
    if "--demo" not in sys.argv:
        for src in SOURCES:
            print("處理", src["storm"])
            storm = build_storm(src)
            if storm["best"] or storm["forecasts"]:
                storms.append(storm)
        merge_official(storms)
    if not storms:
        storms = demo()
        print("沒有任何颱風資料，輸出示範資料。")
    for s in storms:
        s.pop("current", None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "storms": storms}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已寫入", OUT)


if __name__ == "__main__":
    main()
