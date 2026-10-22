"""
歐洲中期天氣預報中心（ECMWF）開放資料的颱風路徑（BUFR 格式）。
  IFS 單一預報、IFS 系集（ENS）、AIFS（AI 模式）、AIFS 系集
需要套件：pip install ecmwf-opendata eccodes
授權：CC BY 4.0，需標示「Based on data and products of ECMWF」。
"""
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src_common import MS_TO_KT, item, to_iso

# (模式, stream, 嘗試的 step, 種類, 代碼, 顯示名稱, 分類)
PRODUCTS = [
    ("ifs", "oper", [240, 360, 90], "det", "ECMF", "ECMWF IFS", "nwp"),
    ("ifs", "enfo", [360, 240, 144], "ens", "E", "ECMWF", "nwp"),
    ("aifs-single", "oper", [360, 240], "det", "AIFS", "ECMWF AIFS", "ai"),
    ("aifs-ens", "enfo", [360, 240], "ens", "AI", "AIFS", "ai"),
]
# ECMWF 颱風編號最後一個字母 → ATCF 海域
BASIN = {"W": "WP", "E": "EP", "C": "CP", "L": "AL", "A": "IO", "B": "IO",
         "S": "SH", "P": "SH", "U": "SH", "R": "SH"}


def download(model, stream, steps, path):
    from ecmwf.opendata import Client
    client = Client(source="ecmwf", model=model, maximum_retries=2, retry_after=10)
    last = None
    for step in steps:
        try:
            client.retrieve(type="tf", stream=stream, step=step, target=str(path))
            return True
        except Exception as e:
            last = e
    print(f"  ECMWF {model}/{stream}：找不到檔案（{str(last)[:80]}）")
    return False


def _clean(values, n):
    vals = list(values)
    if len(vals) == 1:
        vals = vals * n
    out = []
    for v in vals:
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = None
        out.append(None if v is None or math.isnan(v) or abs(v) > 1e9 else v)
    return out


def decode(path):
    """依照 ecCodes 官方範例解讀颱風 BUFR：每則訊息一個颱風，每個 subset 一個系集成員。"""
    import eccodes as ec
    storms = []
    with open(path, "rb") as f:
        while True:
            h = ec.codes_bufr_new_from_file(f)
            if h is None:
                break
            try:
                ec.codes_set(h, "unpack", 1)
                sid = ec.codes_get(h, "stormIdentifier").strip()
                base = datetime(*(ec.codes_get(h, k) for k in ("year", "month", "day", "hour")), tzinfo=timezone.utc)
                n = ec.codes_get(h, "numberOfSubsets")
                members = [int(m) for m in _clean(ec.codes_get_array(h, "ensembleMemberNumber"), n)]
                nper = 0
                while True:
                    try:
                        ec.codes_get_array(h, f"#{nper + 1}#timePeriod")
                        nper += 1
                    except Exception:
                        break
                get = lambda key: _clean(ec.codes_get_array(h, key), n)
                tracks = [[] for _ in range(n)]
                # 第 0 小時（分析位置）
                la, lo = get("#2#latitude"), get("#2#longitude")
                p, w = get("#1#pressureReducedToMeanSeaLevel"), get("#1#windSpeedAt10M")
                for k in range(n):
                    if la[k] is not None:
                        tracks[k].append((0, la[k], lo[k], p[k], w[k]))
                for i in range(1, nper):
                    tp = get(f"#{i}#timePeriod")
                    la, lo = get(f"#{i * 2 + 2}#latitude"), get(f"#{i * 2 + 2}#longitude")
                    p, w = get(f"#{i + 1}#pressureReducedToMeanSeaLevel"), get(f"#{i + 1}#windSpeedAt10M")
                    for k in range(n):
                        if la[k] is not None and tp[k] is not None:
                            tracks[k].append((int(tp[k]), la[k], lo[k], p[k], w[k]))
                storms.append((sid, base, members, tracks))
            finally:
                ec.codes_release(h)
    return storms


def to_points(track, full=True):
    pts = []
    for tau, la, lo, p, w in sorted(track):
        pt = {"tau": tau, "lat": round(la, 1), "lon": round(lo, 1)}
        if full:
            pt["vmax"] = round(w * MS_TO_KT) if w else None
            pt["mslp"] = round(p / 100) if p else None
        pts.append(pt)
    return pts


def mean_track(tracks):
    """系集平均：每個時距，至少一半成員還在時才平均（經度先轉成 0~360 避免跨換日線出錯）。"""
    by_tau = {}
    for tr in tracks:
        for tau, la, lo, *_ in tr:
            by_tau.setdefault(tau, []).append((la, lo % 360))
    need = max(3, len(tracks) // 2)
    pts = []
    for tau in sorted(by_tau):
        v = by_tau[tau]
        if len(v) < need:
            break
        lon = sum(x[1] for x in v) / len(v)
        pts.append({"tau": tau, "lat": round(sum(x[0] for x in v) / len(v), 1),
                    "lon": round(lon - 360 if lon > 180 else lon, 1), "vmax": None, "mslp": None})
    return pts


def fetch():
    out = {}
    with tempfile.TemporaryDirectory() as d:
        for model, stream, steps, kind, code, label, cat in PRODUCTS:
            path = Path(d) / f"{model}-{stream}.bufr"
            if not download(model, stream, steps, path):
                continue
            try:
                decoded = decode(path)
            except Exception as e:
                print(f"  ECMWF {model}/{stream} 解讀失敗：", e)
                continue
            for sid, base, members, tracks in decoded:
                if len(sid) < 2 or not sid[:-1].isdigit() or int(sid[:-1]) >= 70:
                    continue   # 70 以上是模式自己預測的「可能生成」系統，不是已編號的颱風
                number = (BASIN.get(sid[-1], "XX"), int(sid[:-1]))
                init = to_iso(base)
                fcs = []
                if kind == "det":
                    tr = next((t for t in tracks if len(t) > 1), None)
                    if tr:
                        fcs.append({"model": code, "label": label, "category": cat, "init": init, "points": to_points(tr)})
                else:
                    good = [(m, t) for m, t in zip(members, tracks) if len(t) > 1 and m <= 51]
                    group = f"{label} 系集成員"
                    for m, t in good:
                        fcs.append({"model": f"{code}{'C' if m == 51 else 'P'}{m % 51:02d}", "label": f"{label} 成員 {m}",
                                    "category": "ens", "group": group, "init": init, "points": to_points(t, full=False)})
                    mean = mean_track([t for _, t in good])
                    if len(mean) > 1:
                        fcs.append({"model": f"{code}EMN", "label": f"{label} 系集平均", "category": cat, "init": init, "points": mean})
                if not fcs:
                    continue
                first = fcs[-1]["points"][0]
                key = (number, sid)
                if key not in out:
                    out[key] = item("ECMWF", [], first["lat"], first["lon"], init, number=number)
                out[key]["forecasts"].extend(fcs)
                print(f"  ECMWF {model}/{stream} {sid}：{len(fcs)} 條路徑")
    return list(out.values())
