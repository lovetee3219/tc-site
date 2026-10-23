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
# 過去觀測的累積檔：靠 GitHub Actions 的快取在每次執行之間保存（不存進儲存庫）
STORE = ROOT / ".cache" / "stations_history.json"
HIST_DIR = ROOT / "data" / "history"   # 每站一個小檔，網頁點測站時才下載
# 累積檔也隨網站發布一份。萬一 GitHub 的快取沒讀到，就從網站上把它拿回來接著累積
PUBLIC_STORE = ROOT / "data" / "history_store.json"
KEEP_HOURS = 24
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
def cwa_gust_time(we):
    """瞬間風發生的時間（氣象署有提供時才填）。"""
    occ = (we.get("GustInfo") or {}).get("Occurred_at") or {}
    t = occ.get("DateTime")
    try:
        return to_iso(datetime.fromisoformat(t)) if t else None
    except ValueError:
        return None


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
            "gustTime": cwa_gust_time(we),
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
    """
    讀個別測站的 3 小時檔，取得「今天到目前為止的最大瞬間風速」與發生時刻。
    注意：氣象廳的 gust 欄位是每日累計的最大值（每天 0 時重新計算），不是每 10 分鐘的瞬間風速，
    所以它只會維持或變大。氣象廳的公開資料沒有提供每 10 分鐘的瞬間風速。
    """
    block = t.replace(hour=t.hour - t.hour % 3, minute=0)
    url = f"{JMA}data/point/{code}/{block:%Y%m%d}_{block:%H}.json"
    try:
        d = json.loads(http_get(url, timeout=20))
    except Exception:
        return None
    obs = d.get(f"{t:%Y%m%d%H%M}00") or (d[max(d)] if d else {})
    g = obs.get("gust")
    if not (isinstance(g, list) and g and g[0] is not None):
        return None
    when = None
    gt = obs.get("gustTime")
    if isinstance(gt, dict) and "hour" in gt:
        # 氣象廳的 gustTime 是「世界時（UTC）」的時與分，不是日本時間。
        # 取「不晚於觀測時間」的最近一次該時分，就是實際發生的時刻（必要時往前一天）。
        try:
            tu = t.astimezone(timezone.utc)
            gdt = tu.replace(hour=int(gt["hour"]) % 24, minute=int(gt.get("minute", 0)), second=0, microsecond=0)
            if gdt > tu:
                gdt -= timedelta(days=1)
            when = to_iso(gdt)
        except ValueError:
            pass
    return {"v": g[0], "time": when}


def fetch_jma():
    latest = http_get(JMA + "data/latest_time.txt").decode().strip()
    t = datetime.fromisoformat(latest).astimezone(JST)
    table = json.loads(http_get(JMA + "const/amedastable.json"))
    # 最新時間的檔案偶爾還沒放上去，往前找最多 30 分鐘
    for back in range(4):
        try:
            obs_all = json.loads(http_get(f"{JMA}data/map/{t:%Y%m%d%H%M}00.json"))
            break
        except Exception:
            if back == 3:
                raise
            t -= timedelta(minutes=10)
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
            "gust": None,          # 氣象廳沒有每 10 分鐘的瞬間風速
            "gustMax": None,       # 今日最大瞬間風速（颱風附近的測站才有）
            "p": sea if sea is not None else local, "pType": "海平面" if sea is not None else "測站",
        })
    # 颱風附近的測站另外抓瞬間風速
    centers = storm_centers()
    near = sorted((min(km((s["lat"], s["lon"]), c) for c in centers), i) for i, s in enumerate(out)) if centers else []
    near = [i for d, i in near if d <= GUST_RADIUS_KM][:GUST_MAX_STATIONS]
    if near:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for i, g in zip(near, ex.map(lambda i: jma_gust(out[i]["id"], t), near)):
                if g:
                    out[i]["gustMax"], out[i]["gustMaxTime"] = g["v"], g["time"]
    print(f"JMA：{len(out)} 站有風速，其中 {len(near)} 站另外抓了瞬間風速")
    return out


def site_url():
    """網站網址：可用環境變數 SITE_URL 指定；沒指定就依 GitHub 儲存庫名稱推算。"""
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")   # 例如 yourname/tc-site
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def load_store():
    try:
        store = json.loads(STORE.read_text())
        print(f"歷史資料：從 GitHub 快取讀到 {sum('#' not in k for k in store)} 站")
        return store
    except Exception:
        pass
    url = site_url()
    if url:
        try:
            store = json.loads(http_get(url + "data/history_store.json", timeout=60))
            print(f"歷史資料：快取沒有資料，改從網站取回 {sum('#' not in k for k in store)} 站")
            return store
        except Exception as e:
            print("歷史資料：快取與網站都沒有舊資料，從頭開始累積（", e, "）")
    return {}


def save_history(stations):
    """把這次的觀測加進累積檔，刪掉超過 24 小時的，再為每站寫出一個小檔。"""
    store = load_store()
    now_min = int(datetime.now(timezone.utc).timestamp() // 60)
    cutoff = now_min - KEEP_HOURS * 60
    for st in stations:
        try:   # 單一測站的資料有問題時略過它，不影響其他測站
            key = f"{st['src']}_{st['id']}"
            t = int(datetime.fromisoformat(st["time"].replace("Z", "+00:00")).timestamp() // 60)
            rows = [r for r in store.get(key, []) if r[0] >= cutoff and r[0] != t]
            rows.append([t, st.get("ws"), st.get("gust"), st.get("p")])
            store[key] = sorted(rows)
            # 日本氣象廳：沒有即時陣風，改記錄「今日最大瞬間風速」的發生時間與數值（同一時間只記一次）
            if st.get("gustMax") is not None and st.get("gustMaxTime"):
                gt = int(datetime.fromisoformat(st["gustMaxTime"].replace("Z", "+00:00")).timestamp() // 60)
                ek = key + "#gust"
                ev = [e for e in store.get(ek, []) if e[0] >= cutoff and e[0] != gt]
                ev.append([gt, st["gustMax"]])
                store[ek] = sorted(ev)
        except Exception as e:
            print("  略過一筆有問題的測站資料：", e)
    store = {k: v for k, v in store.items() if v and v[-1][0] >= cutoff}
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, separators=(",", ":")))
    PUBLIC_STORE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_STORE.write_text(STORE.read_text())
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    stations_only = {k: v for k, v in store.items() if "#" not in k}
    for key, rows in stations_only.items():
        cols = list(zip(*rows))
        out = {"t": cols[0], "ws": cols[1], "gust": cols[2], "p": cols[3]}
        ev = store.get(key + "#gust")
        if ev:   # 陣風發生時刻（分鐘）與數值
            out["gx"], out["gv"] = [e[0] for e in ev], [e[1] for e in ev]
        (HIST_DIR / f"{key}.json").write_text(json.dumps(out, separators=(",", ":")))
    # 清單檔：網頁讀不到某站的歷史資料時，用它判斷是哪裡出問題
    (HIST_DIR / "index.json").write_text(json.dumps({
        "updated": to_iso(datetime.now(timezone.utc)), "count": len(stations_only),
        "stations": {k: [len(v), v[-1][0]] for k, v in stations_only.items()}}, separators=(",", ":")))
    longest = max((len(v) for v in stations_only.values()), default=0)
    print(f"歷史資料：{len(stations_only)} 站，最多累積 {longest} 筆")


LAST = ROOT / ".cache" / "stations_last.json"   # 各來源最後一次成功的資料
MAX_STALE_HOURS = 3


def load_last():
    """讀取上次成功的資料：先找 GitHub 快取，沒有的話從網站上目前的 stations.json 拿。"""
    try:
        return json.loads(LAST.read_text())
    except Exception:
        pass
    url = site_url()
    if url:
        try:
            d = json.loads(http_get(url + "data/stations.json", timeout=60))
            out = {}
            for st in d.get("stations", []):
                out.setdefault(st["src"], []).append(st)
            return out
        except Exception:
            pass
    return {}


def main():
    stations, last, good = [], load_last(), {}
    main.stations = stations
    limit = datetime.now(timezone.utc) - timedelta(hours=MAX_STALE_HOURS)
    for name, fn in (("CWA", fetch_cwa), ("JMA", fetch_jma)):
        try:
            got = fn()
            if not got:
                raise ValueError("沒有任何測站資料")
            stations += got
            good[name] = got
        except Exception as e:
            # 這次抓不到：沿用上次成功的資料（3 小時內），網頁上的測站才不會整批消失
            old = [st for st in last.get(name, [])
                   if st.get("time") and datetime.fromisoformat(st["time"].replace("Z", "+00:00")) >= limit]
            stations += old
            good[name] = last.get(name, [])
            print(f"{name} 失敗：{e}；沿用上次的 {len(old)} 站資料")
    LAST.parent.mkdir(parents=True, exist_ok=True)
    LAST.write_text(json.dumps(good, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": to_iso(datetime.now(timezone.utc)), "stations": stations},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已寫入", OUT, f"（共 {len(stations)} 站）")
    return stations


def main_safe():
    """不論抓資料時發生什麼錯誤，最後都一定要產生歷史資料檔，網頁的折線圖才不會整批讀不到。"""
    main.stations = []
    try:
        main()
    finally:
        try:
            save_history(main.stations)   # main 中途出錯時，也會用到目前為止抓到的資料
        except Exception as e:
            print("歷史資料寫入失敗：", e)


if __name__ == "__main__":
    main_safe()
