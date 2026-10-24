"""
日本氣象廳（JMA）颱風預報。
資料：氣象廳網站內部使用的公開 JSON（不需金鑰）
  活動中的颱風清單：https://www.jma.go.jp/bosai/typhoon/data/targetTc.json
  個別颱風預報：    https://www.jma.go.jp/bosai/typhoon/data/{TC番號}/forecast.json
使用規範：日本政府標準利用規約，需標示出處「気象庁」。
"""
import json

from src_common import http_get, item, parse_time, to_iso

BASE = "https://www.jma.go.jp/bosai/typhoon/data/"


def english_name(tc):
    """從 specifications.json 找颱風的英文名（找不到就回傳 None，不影響其他資料）。"""
    try:
        spec = json.loads(http_get(f"{BASE}{tc}/specifications.json"))
    except Exception:
        return None
    stack = [spec]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            nm = x.get("name")
            if isinstance(nm, dict) and isinstance(nm.get("en"), str) and nm["en"].strip():
                return nm["en"].strip().capitalize()
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return None


def fetch():
    targets = json.loads(http_get(BASE + "targetTc.json"))
    out = []
    for t in targets:
        tc = t.get("tropicalCyclone")
        if not tc:
            continue
        try:
            fc = json.loads(http_get(f"{BASE}{tc}/forecast.json"))
        except Exception as e:
            print(f"  JMA {tc} 失敗：", e)
            continue
        pts, init = [], None
        for part in fc:
            if not isinstance(part, dict) or "center" not in part or "advancedHours" not in part:
                continue
            vt = part.get("validtime", {})
            vt = vt.get("UTC") or vt.get("JST")
            if vt is None:
                continue
            tau = int(part["advancedHours"])
            if tau == 0:
                init = parse_time(vt)
            lat, lon = part["center"]
            pts.append({"tau": tau, "lat": round(float(lat), 1), "lon": round(float(lon), 1), "vmax": None, "mslp": None})
        if init is None or len(pts) < 2:
            continue
        # 同一個時距只留一筆（日本附近會多出 3 小時間隔的點，保留它們）
        uniq = {p["tau"]: p for p in pts}
        pts = [uniq[k] for k in sorted(uniq)]
        number = t.get("typhoonNumber", "")
        label_no = f"颱風 {int(number[-2:])} 號" if len(number) == 4 and number[-2:].isdigit() else tc
        out.append(item("JMA",
                        [{"model": "RJTD", "label": "JMA 官方", "category": "official", "init": to_iso(init), "points": pts}],
                        pts[0]["lat"], pts[0]["lon"], to_iso(init), name=english_name(tc)))
        print(f"  JMA {tc}（{label_no}）：{len(pts)} 個預報點")
    return out
