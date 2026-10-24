"""
中央氣象署（CWA）颱風路徑預報。
資料集：W-C0034-005 颱風消息與警報－熱帶氣旋路徑
需要免費的 API 授權碼，請放在 GitHub 的 Secret「CWA_API_KEY」，不要寫在程式碼裡。
使用規範：政府資料開放授權條款，需標示出處「中央氣象署」。
"""
import json
import os

from src_common import MS_TO_KT, http_get, item, parse_time, to_iso

URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005?format=JSON"


def gk(obj, name):
    """不分大小寫取欄位（氣象署欄位名稱的大小寫曾經變動過）。"""
    if not isinstance(obj, dict):
        return None
    if name in obj:
        return obj[name]
    for k, v in obj.items():
        if k.lower() == name.lower():
            return v
    return None


def as_list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fix_point(f, tau):
    lat, lon = num(gk(f, "CoordinateLatitude")), num(gk(f, "CoordinateLongitude"))
    if lat is None or lon is None:
        return None
    w, p = num(gk(f, "MaxWindSpeed")), num(gk(f, "Pressure"))
    return {"tau": tau, "lat": round(lat, 1), "lon": round(lon, 1),
            "vmax": round(w * MS_TO_KT) if w else None, "mslp": int(p) if p else None}


def fetch():
    key = os.environ.get("CWA_API_KEY")
    if not key:
        print("  CWA：沒有設定 CWA_API_KEY，略過")
        return []
    data = json.loads(http_get(URL, headers={"Authorization": key, "Accept": "application/json"}))
    records = gk(data, "records") or {}
    tcs = as_list(gk(gk(records, "TropicalCyclones") or {}, "TropicalCyclone"))
    out = []
    for tc in tcs:
        en = gk(tc, "TyphoonName") or ""
        zh = gk(tc, "CwaTyphoonName") or ""
        # 分析（過去定位）→ 當作實測路徑
        best = []
        for f in as_list(gk(gk(tc, "AnalysisData") or {}, "Fix")):
            t = gk(f, "DateTime") or gk(f, "FixTime")
            p = fix_point(f, 0)
            if t and p:
                p.pop("tau")
                best.append({**p, "time": to_iso(parse_time(t))})
        # 預報：可能同時有好幾次起報，只取最新一次
        runs = {}
        for f in as_list(gk(gk(tc, "ForecastData") or {}, "Fix")):
            init, tau = gk(f, "InitialTime"), num(gk(f, "ForecastHour"))
            p = fix_point(f, int(tau) if tau is not None else None)
            if init and p and p["tau"] is not None:
                runs.setdefault(to_iso(parse_time(init)), []).append(p)
        if not runs:
            continue
        init = max(runs)
        pts = sorted(runs[init], key=lambda p: p["tau"])
        # 把起報時刻的分析位置當作第 0 小時，路徑才會從颱風目前位置畫出去
        start = next((b for b in best if b["time"] == init), None)
        if start and pts[0]["tau"] != 0:
            pts.insert(0, {"tau": 0, **{k: start[k] for k in ("lat", "lon", "vmax", "mslp")}})
        now = best[-1] if best else pts[0]
        name = en.strip().capitalize() or None      # 國際英文名，例如 IN-FA → In-fa
        name_zh = zh.strip() or None                # 中央氣象署的中文譯名，例如 烟花
        out.append(item("CWA",
                        [{"model": "RCTP", "label": "CWA 官方", "category": "official", "init": init, "points": pts}],
                        now["lat"], now["lon"], now.get("time", init), name=name, best=best, name_zh=name_zh))
        print(f"  CWA {name} {name_zh or ''}：{len(pts)} 個預報點，{len(best)} 個定位點")
    return out
