"""
從 NOAA NOMADS 下載 GFS 最新一報的 10 公尺風場（1° 解析度），
轉成 leaflet-velocity 可讀的 data/wind.json。

需要套件：pip install xarray cfgrib eccodes numpy
"""
import json
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import xarray as xr

OUT = Path(__file__).resolve().parent.parent / "data" / "wind.json"
FILTER = ("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"
          "?file=gfs.t{hh}z.pgrb2.1p00.f000"
          "&lev_10_m_above_ground=on&var_UGRD=on&var_VGRD=on"
          "&dir=%2Fgfs.{ymd}%2F{hh}%2Fatmos")


def download_latest(path: Path) -> datetime:
    """由近到遠嘗試最近 4 個起報時間（GFS 一天 4 報，約延遲 4 小時上線）。"""
    t = datetime.now(timezone.utc) - timedelta(hours=4)
    base = t.replace(minute=0, second=0, microsecond=0, hour=t.hour - t.hour % 6)
    for i in range(4):
        run = base - timedelta(hours=6 * i)
        url = FILTER.format(ymd=run.strftime("%Y%m%d"), hh=run.strftime("%H"))
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
            if data[:4] == b"GRIB":
                path.write_bytes(data)
                print("下載", run.isoformat())
                return run
        except Exception as e:
            print("略過", run.isoformat(), e)
    raise SystemExit("找不到可用的 GFS 資料")


def to_velocity_json(grib: Path, run: datetime):
    ds = xr.open_dataset(grib, engine="cfgrib")
    u = ds["u10"].values.astype(float)  # 緯度由北(90)到南(-90)，經度 0~359
    v = ds["v10"].values.astype(float)
    ny, nx = u.shape
    lats, lons = ds["latitude"].values, ds["longitude"].values
    header = {"parameterCategory": 2, "parameterUnit": "m.s-1",
              "lo1": float(lons[0]), "la1": float(lats[0]),
              "lo2": float(lons[-1]), "la2": float(lats[-1]),
              "dx": float(abs(lons[1] - lons[0])), "dy": float(abs(lats[1] - lats[0])),
              "nx": nx, "ny": ny, "refTime": run.isoformat().replace("+00:00", "Z")}

    def rec(num, arr):
        return {"header": {**header, "parameterNumber": num},
                "data": np.round(np.nan_to_num(arr), 1).ravel().tolist()}

    return [rec(2, u), rec(3, v)]


def main():
    with tempfile.TemporaryDirectory() as d:
        g = Path(d) / "gfs.grb2"
        run = download_latest(g)
        out = to_velocity_json(g, run)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print("已寫入", OUT)


if __name__ == "__main__":
    main()
