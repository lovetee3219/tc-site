"""
下載地面測站的即時觀測，寫成 data/stations.json（不存進儲存庫，只隨網站一起發布）。

1. 中央氣象署 署屬氣象站（O-A0003-001，10 分鐘更新）：平均風、瞬間風、測站氣壓
   需要 GitHub Secret「CWA_API_KEY」
2. 日本氣象廳 AMeDAS（有風速的測站，10 分鐘更新）：平均風、海平面氣壓
   瞬間風速只在個別測站的檔案裡，逐站下載太多，所以只替颱風附近的測站下載。
   這是氣象廳網站內部使用的資料，不是正式 API，網址可能變動。
"""
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src_common import http_get, to_iso

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "stations.json"
JMA = "https://www.jma.go.jp/bosai/amedas/"
GUST_RADIUS_KM = 700     # 颱風中心多遠以內的 AMeDAS 測站要另外抓瞬間風速
GUST_MAX_STATIONS = 300  # 最多抓幾站，避免對氣象廳發出太多請求
JST = timezone(timedelta(hours=9))


def num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v <= -90 else v   # 氣象署用 -99、-999 表示缺值


# ── 中央氣象署 ─────────────────────────────────────────────
def fetch_cwa():
    key = os.environ.get("CWA_API_KEY")
    if not key:
        print("CWA：沒有設定 CWA_API_KEY，略過")
        return []
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001?format=JSON"
    data = json.loads(http_get(url, headers={"Authorization": key, "Accept": "application/json"}))
    out = []
    for st in data.get("records", {}).get("Station", []):
        coords = st.get("GeoInfo", {}).get("Coordinates", [])
        c = next((x for x in coords if x.get("CoordinateName") == "WGS84"), coords[0] if coords else None)
        we = st.get("WeatherElement", {})
        if not c:
            continue
        ws, wd = num(we.get("WindSpeed")), num(we.get("WindDirection"))
        out.append({
            "src": "CWA", "id": st.get("StationId"), "name": st.get("StationName"),
            "lat": round(float(c["StationLatitude"]), 4), "lon": round(float(c["StationLongitude"]), 4),
            "time": to_iso(datetime.fromisoformat(st["ObsTime"]["DateTime"])),
            "ws": ws, "wd": wd,
            "gust": num((we.get("GustInfo") or {}).get("PeakGustSpeed")),
            "p": num(we.get("AirPressure")), "pType": "測站",
        })
    print(f"CWA：{len(out)} 站")
    return out


# ── 日本氣象廳 AMeDAS ──────────────────────────────────────
def val(obs, k):
    v = obs.get(k)
    return v[0] if isinstance(v, list) and v and v[0] is not None else None


def storm_centers():
    """從 tracks.json 取出西北太平洋颱風目前的位置。"""
    try:
        tracks = json.loads((ROOT / "data" / "tracks.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for s in tracks.get("storms", []):
        if s.get("demo") or (s.get("basin") or s["id"][:2]).upper() != "WP":
            continue
        if s.get("best"):
            p = s["best"][-1]
        elif s.get("forecasts"):
            p = max(s["forecasts"], key=lambda f: f["init"])["points"][0]
        else:
            continue
        out.append((p["lat"], p["lon"]))
    return out


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def jma_gust(code, t):
    """讀個別測站的 3 小時檔，取得最新一筆的瞬間風速。"""
    block = t.replace(hour=t.hour - t.hour % 3, minute=0)
    url = f"{JMA}data/point/{code}/{block:%Y%m%d}_{block:%H}.json"
    try:
        d = json.loads(http_get(url, timeout=20))
    except Exception:
        return None
    obs = d.get(f"{t:%Y%m%d%H%M}00") or (d[max(d)] if d else {})
    for k, v in obs.items():   # 欄位名稱以 gust 開頭、不是風向或時間的那一個
        if k.lower().startswith("gust") and "direction" not in k.lower() and "time" not in k.lower():
            return v[0] if isinstance(v, list) else None
    return None


def fetch_jma():
    latest = http_get(JMA + "data/latest_time.txt").decode().strip()
    t = datetime.fromisoformat(latest).astimezone(JST)
    table = json.loads(http_get(JMA + "const/amedastable.json"))
    obs_all = json.loads(http_get(f"{JMA}data/map/{t:%Y%m%d%H%M}00.json"))
    out = []
    for code, obs in obs_all.items():
        ws = val(obs, "wind")
        if ws is None or code not in table:
            continue
        info = table[code]
        lat = info["lat"][0] + info["lat"][1] / 60
        lon = info["lon"][0] + info["lon"][1] / 60
        wdc = val(obs, "windDirection")   # 1～16 = 北北東～北，0 = 靜穩
        sea, local = val(obs, "normalPressure"), val(obs, "pressure")
        out.append({
            "src": "JMA", "id": code, "name": info.get("kjName"), "manned": info.get("type") == "A",
            "lat": round(lat, 4), "lon": round(lon, 4), "time": to_iso(t),
            "ws": ws, "wd": None if not wdc else (wdc % 16) * 22.5,
            "gust": None,
            "p": sea if sea is not None else local, "pType": "海平面" if sea is not None else "測站",
        })
    # 颱風附近的測站另外抓瞬間風速
    centers = storm_centers()
    near = sorted((min(km((s["lat"], s["lon"]), c) for c in centers), i) for i, s in enumerate(out)) if centers else []
    near = [i for d, i in near if d <= GUST_RADIUS_KM][:GUST_MAX_STATIONS]
    if near:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for i, g in zip(near, ex.map(lambda i: jma_gust(out[i]["id"], t), near)):
                out[i]["gust"] = g
    print(f"JMA：{len(out)} 站有風速，其中 {len(near)} 站另外抓了瞬間風速")
    return out


def main():
    stations = []
    for name, fn in (("CWA", fetch_cwa), ("JMA", fetch_jma)):
        try:
            stations += fn()
        except Exception as e:
            print(f"{name} 失敗：", e)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": to_iso(datetime.now(timezone.utc)), "stations": stations},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已寫入", OUT, f"（共 {len(stations)} 站）")


if __name__ == "__main__":
    main()
