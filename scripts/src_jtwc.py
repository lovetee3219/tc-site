"""
美國聯合颱風警報中心（JTWC）警報。
資料：JTWC 網站上的警報文字檔，例如
  https://www.metoc.navy.mil/jtwc/products/wp2926web.txt   （西北太平洋 29W，2026 年）
JTWC 負責西北太平洋（wp）、北印度洋（io）、南半球（sh）。
JTWC 網站註明其產品屬公共資訊，可自由使用。
"""
import re
from datetime import datetime, timedelta, timezone

from src_common import http_get, item, to_iso

URL = "https://www.metoc.navy.mil/jtwc/products/{basin}{num:02d}{yy}web.txt"
LATLON = r"(\d+(?:\.\d+)?)([NS])\s+(\d+(?:\.\d+)?)([EW])"


def ddhhmm_to_dt(s, ref):
    """
    警報裡的時間只有「日時分」，例如 220000Z，要自己補上年月。
    警報時間不可能在未來，所以取「不晚於現在」的最近一個符合的日期（本月、上個月、前兩個月依序試）。
    舊版會把上個月 28 日的舊警報誤判成本月 28 日（未來），導致已消散的颱風被當成還在活躍。
    """
    day, hour, minute = int(s[:2]), int(s[2:4]), int(s[4:6])
    for months_back in (0, 1, 2):
        y, m = ref.year, ref.month - months_back
        while m < 1:
            y, m = y - 1, m + 12
        try:
            dt = datetime(y, m, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt <= ref + timedelta(hours=3):
            return dt
    raise ValueError("無法判斷警報時間：" + s)


def latlon(lat, ns, lon, ew):
    return round(float(lat) * (1 if ns == "N" else -1), 1), round(float(lon) * (1 if ew == "E" else -1), 1)


def parse(text, ref=None):
    ref = ref or datetime.now(timezone.utc)
    t = " ".join(text.split())  # 把換行和多餘空白壓成一個空白，比較好比對
    m = re.search(r"WARNING POSITION:\s*(\d{6})Z\s*---\s*NEAR\s+" + LATLON, t)
    if not m:
        return None
    init = ddhhmm_to_dt(m.group(1), ref)
    lat, lon = latlon(*m.group(2, 3, 4, 5))
    w0 = re.search(r"MAX SUSTAINED WINDS - (\d+) KT", t[m.end():])
    pts = [{"tau": 0, "lat": lat, "lon": lon, "vmax": int(w0.group(1)) if w0 else None, "mslp": None}]
    for f in re.finditer(r"(\d+) HRS, VALID AT:\s*(\d{6})Z\s*---\s*" + LATLON + r"(.{0,80}?MAX SUSTAINED WINDS - (\d+) KT)?", t):
        la, lo = latlon(*f.group(3, 4, 5, 6))
        pts.append({"tau": int(f.group(1)), "lat": la, "lon": lo,
                    "vmax": int(f.group(8)) if f.group(8) else None, "mslp": None})
    # JTWC 只在備註寫出「目前」的最低中心氣壓，例如 MINIMUM CENTRAL PRESSURE AT 240000Z IS 965 MB；
    # 預報時點沒有氣壓。依時間對應到同一時刻的預報點（通常是第 0 小時）
    for pm in re.finditer(r"MINIMUM CENTRAL PRESSURE AT (\d{6})Z IS (\d{3,4}) MB", t):
        try:
            tau = round((ddhhmm_to_dt(pm.group(1), ref) - init).total_seconds() / 3600)
        except ValueError:
            continue
        for p in pts:
            if p["tau"] == tau:
                p["mslp"] = int(pm.group(2))
    name = re.search(r"SUBJ/[A-Z ]*?\d+[A-Z] \(([A-Z\- ]+)\)", t) or re.search(r"\d+[WSPAB] \(([A-Z\- ]+)\) WARNING", t)
    return init, pts, (name.group(1).strip().capitalize() if name else None)


def fetch(storm_ids):
    """storm_ids：例如 ["WP292026"]；只處理 JTWC 負責的海域。"""
    out = []
    for sid in storm_ids:
        basin, num, year = sid[:2].lower(), int(sid[2:4]), sid[4:8]
        if basin not in ("wp", "io", "sh") or not year.isdigit():
            continue
        url = URL.format(basin=basin, num=num, yy=year[2:])
        try:
            text = http_get(url).decode("utf-8", errors="replace")
            res = parse(text)
        except Exception as e:
            print(f"  JTWC {sid} 失敗：", e)
            continue
        if not res:
            print(f"  JTWC {sid}：檔案裡找不到警報位置（可能已停止發布警報）")
            continue
        init, pts, name = res
        if datetime.now(timezone.utc) - init > timedelta(hours=24):
            # JTWC 停止發布警報後，最後一份警報仍會留在網站上；超過 24 小時沒更新就不採用
            print(f"  JTWC {sid}：最後一份警報是 {init:%m/%d %HZ}，已超過 24 小時，視為已停止發布")
            continue
        out.append(item("JTWC",
                        [{"model": "JTWC", "label": "JTWC 官方", "category": "official", "init": to_iso(init), "points": pts}],
                        pts[0]["lat"], pts[0]["lon"], to_iso(init), number=(basin.upper(), num), name=name))
        print(f"  JTWC {sid}（{name}）：{len(pts)} 個預報點")
    return out
