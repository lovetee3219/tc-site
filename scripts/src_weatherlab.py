"""
Google DeepMind Weather Lab 的 AI 颱風系集預報（ATCF 格式）。
  FNV3P2 = WeatherNext 2 Cyclones（WN2C，俗稱 FNV3），50 個成員
  WNV3   = WeatherNext 3 Cyclones（WN3C），64 個成員
網址：https://deepmind.google.com/science/weatherlab/download/cyclones/{模型}/{ensemble 或 ensemble_mean}/paired/atcf/...
這是實驗性資料，使用前請閱讀 Weather Lab 的使用條款，並標示出處。
"""
from datetime import datetime, timedelta, timezone

from src_common import http_get, item

BASE = "https://deepmind.google.com/science/weatherlab/download/cyclones"
# (Weather Lab 的模型名稱, 網站上的代碼, 顯示名稱)
PRODUCTS = [("FNV3P2", "WN2C", "FNV3（WN2C）"), ("WNV3", "WN3C", "WN3C")]


def url(model, product, t):
    return f"{BASE}/{model}/{product}/paired/atcf/{model}_{t:%Y_%m_%dT%H}_00_atcf_a_deck.txt"


def latest(model):
    """找最近一次有資料的起報時間（從 6 小時前開始往回找 2 天）。"""
    t = datetime.now(timezone.utc) - timedelta(hours=6)
    t = t.replace(minute=0, second=0, microsecond=0, hour=t.hour - t.hour % 6)
    for i in range(8):
        run = t - timedelta(hours=6 * i)
        try:
            text = http_get(url(model, "ensemble_mean", run), timeout=60, retries=1).decode("utf-8", "replace")
            if text.strip():
                return run, text
        except Exception:
            continue
    raise RuntimeError(f"{model}：最近 2 天都找不到資料")


def parse(text):
    """回傳 {(海域, 編號): {成員代碼: [點...]}}。成員以「TECHNUM + TECH」區分。"""
    from fetch_tracks import parse_latlon
    out = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 9 or not f[1].isdigit() or not f[6] or not f[7]:
            continue
        try:
            num, tau = int(f[1]), int(f[5])
            lat, lon = parse_latlon(f[6], f[7])
        except (ValueError, IndexError):
            continue
        if num >= 70:   # 70 以上是預測可能生成的擾動，不是已編號的颱風
            continue
        member = f"{f[3]}{f[4]}"
        pts = out.setdefault((f[0].upper(), num), {}).setdefault(member, {})
        if tau not in pts:
            vmax = int(f[8]) if f[8].isdigit() and int(f[8]) > 0 else None
            mslp = int(f[9]) if len(f) > 9 and f[9].isdigit() and int(f[9]) > 0 else None
            pts[tau] = {"tau": tau, "lat": lat, "lon": lon, "vmax": vmax, "mslp": mslp}
    return {k: {m: [p[t] for t in sorted(p)] for m, p in v.items()} for k, v in out.items()}


def fetch():
    from fetch_tracks import iso
    storms = {}
    for model, code, label in PRODUCTS:
        try:
            run, mean_text = latest(model)
        except Exception as e:
            print(f"  Weather Lab {code}：{e}")
            continue
        # 起報時間以檔案內容為準
        inits = [l.split(",")[2].strip() for l in mean_text.splitlines() if l.count(",") > 8]
        init = iso(max(inits) if inits else run.strftime("%Y%m%d%H"))
        name = lambda suffix: f"{label} {suffix}".replace("） ", "）")
        means = parse(mean_text)
        try:
            members = parse(http_get(url(model, "ensemble", run), timeout=120).decode("utf-8", "replace"))
        except Exception as e:
            print(f"  Weather Lab {code} 成員下載失敗：", e)
            members = {}
        for key in set(means) | set(members):
            fcs = []
            m = means.get(key, {})
            if m:
                pts = max(m.values(), key=len)
                if len(pts) > 1:
                    fcs.append({"model": code, "label": name("系集平均"), "category": "ai", "init": init, "points": pts})
            for i, (_, pts) in enumerate(sorted(members.get(key, {}).items())):
                if len(pts) > 1:
                    fcs.append({"model": f"{code}{i:02d}", "label": f"{label} 成員 {i}", "category": "ens",
                                "group": name("系集成員"), "init": init,
                                "points": [{k: p[k] for k in ("tau", "lat", "lon")} for p in pts]})
            if not fcs:
                continue
            first = fcs[0]["points"][0]
            if key not in storms:
                storms[key] = item("WeatherLab", [], first["lat"], first["lon"], init, number=key)
            storms[key]["forecasts"].extend(fcs)
        print(f"  Weather Lab {code}：{run:%Y-%m-%d %HZ} 起報，{len(set(means) | set(members))} 個颱風")
    return list(storms.values())
