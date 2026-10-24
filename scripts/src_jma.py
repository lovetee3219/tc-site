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


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def find_kt(obj, path=()):
    """在一個預報時點的資料裡，找「最大持續風速」的節數（略過陣風）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = path + (str(k).lower(),)
            if p[-1] == "kt" and any("sustain" in x or x == "maximumwind" for x in p) and not any("gust" in x for x in p):
                n = _num(v)
                if n is not None:
                    return n
            r = find_kt(v, p)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_kt(v, path)
            if r is not None:
                return r
    return None


def find_pressure(obj):
    """找中心氣壓（hPa）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in ("pressure", "centralpressure"):
                n = _num(v) if not isinstance(v, dict) else next((_num(x) for x in v.values() if _num(x)), None)
                if n:
                    return n
        for v in obj.values():
            r = find_pressure(v) if isinstance(v, (dict, list)) else None
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_pressure(v)
            if r:
                return r
    return None


def part_key(part):
    """預報時點的識別：優先用 advancedHours，沒有就用預報時間。"""
    if "advancedHours" in part:
        try:
            return ("h", int(part["advancedHours"]))
        except (TypeError, ValueError):
            pass
    vt = part.get("validtime") or {}
    vt = vt.get("UTC") or vt.get("JST") if isinstance(vt, dict) else None
    try:
        return ("t", to_iso(parse_time(vt))) if vt else None
    except ValueError:
        return None


def intensities(tc):
    """從 specifications.json 取各預報時點的最大風速（節）與中心氣壓。"""
    try:
        spec = json.loads(http_get(f"{BASE}{tc}/specifications.json"))
    except Exception:
        return {}
    out = {}
    for part in spec if isinstance(spec, list) else []:
        if isinstance(part, dict):
            k = part_key(part)
            if k:
                out[k] = (find_kt(part), find_pressure(part))
    return out


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
            kt, p = find_kt(part), find_pressure(part)
            pts.append({"tau": tau, "lat": round(float(lat), 1), "lon": round(float(lon), 1),
                        "vmax": round(kt) if kt else None, "mslp": round(p) if p else None,
                        "_vt": to_iso(parse_time(vt))})
        if init is None or len(pts) < 2:
            continue
        # 同一個時距只留一筆（日本附近會多出 3 小時間隔的點，保留它們）
        uniq = {p["tau"]: p for p in pts}
        pts = [uniq[k] for k in sorted(uniq)]
        # forecast.json 裡沒有強度時，從 specifications.json 補上（依預報時距或預報時間對應）
        if any(p["vmax"] is None for p in pts):
            spec = intensities(tc)
            for p in pts:
                kt, pr = spec.get(("h", p["tau"])) or spec.get(("t", p["_vt"])) or (None, None)
                if p["vmax"] is None and kt:
                    p["vmax"] = round(kt)
                if p["mslp"] is None and pr:
                    p["mslp"] = round(pr)
            if all(p["vmax"] is None for p in pts):
                sample = next((x for x in fc if isinstance(x, dict) and "center" in x), {})
                print(f"  JMA {tc}：找不到風速欄位，預報資料的欄位有：{', '.join(sample.keys())}")
        for p in pts:
            p.pop("_vt", None)
        number = t.get("typhoonNumber", "")
        label_no = f"颱風 {int(number[-2:])} 號" if len(number) == 4 and number[-2:].isdigit() else tc
        out.append(item("JMA",
                        [{"model": "RJTD", "label": "JMA 官方", "category": "official", "init": to_iso(init), "points": pts}],
                        pts[0]["lat"], pts[0]["lon"], to_iso(init), name=english_name(tc)))
        print(f"  JMA {tc}（{label_no}）：{len(pts)} 個預報點")
    return out
