"""
美國 GFS 單一預報的颱風路徑（NCEP 官方 tracker 產品，ATCF 格式）。
NCAR 的西北太平洋檔案只有 GFS 系集，沒有 GFS 單一預報，所以從這裡補。
  https://nomads.ncep.noaa.gov/pub/data/nccf/com/ens_tracker/prod/gfs.YYYYMMDD/HH/tctrack/avnx.tHHz.cyclone.trackatcfunix
（2026 年 4 月以前放在 gefs.YYYYMMDD 資料夾，程式兩個位置都會試）
美國政府資料，可自由使用。
"""
from datetime import datetime, timedelta, timezone

from src_common import http_get, item

BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/ens_tracker/prod/"
PATHS = ["gfs.{ymd}/{hh}/tctrack/avnx.t{hh}z.cyclone.trackatcfunix",
         "gefs.{ymd}/{hh}/tctrack/avnx.t{hh}z.cyclone.trackatcfunix"]


def latest_text():
    t = datetime.now(timezone.utc) - timedelta(hours=4)
    base = t.replace(minute=0, second=0, microsecond=0, hour=t.hour - t.hour % 6)
    for i in range(5):
        run = base - timedelta(hours=6 * i)
        for p in PATHS:
            url = BASE + p.format(ymd=run.strftime("%Y%m%d"), hh=run.strftime("%H"))
            try:
                text = http_get(url).decode("utf-8", errors="replace")
                if text.strip():
                    print("  GFS 使用", run.strftime("%Y-%m-%d %HZ"), "起報")
                    return text
            except Exception:
                continue
    raise RuntimeError("最近 5 次起報都找不到 GFS 路徑檔")


def fetch():
    from fetch_tracks import iso, parse_atcf
    text = latest_text()
    # 依颱風分組：同一檔案包含全球所有颱風
    by_storm = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        # 70 以上是 GFS 自己預測「可能生成」的系統或擾動，不是已編號的颱風
        if len(f) > 8 and f[1].isdigit() and int(f[1]) < 70:
            by_storm.setdefault((f[0].upper(), int(f[1])), []).append(line)
    out = []
    for (basin, num), lines in by_storm.items():
        runs = parse_atcf("\n".join(lines))
        if not runs:
            continue
        (_, init), pts = max(runs.items(), key=lambda kv: kv[0][1])
        if len(pts) < 2:
            continue
        out.append(item("GFS", [{"model": "AVNO", "label": "GFS", "category": "nwp", "init": iso(init), "points": pts}],
                        pts[0]["lat"], pts[0]["lon"], iso(init), number=(basin, num)))
    print(f"  GFS：檔案內有 {len(out)} 個颱風")
    return out
