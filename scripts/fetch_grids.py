"""
下載海溫與海洋熱含量的格點資料，存成網頁可直接讀取的小型二進位檔：
  data/grids/sst.bin + sst.json   海溫（NOAA OISST v2.1 近即時版，0.25°，每日）
  data/grids/ohc.bin + ohc.json   海洋熱含量（NOAA AOML TCHP 或 NESDIS OHC，0.25°，每日）

.bin 是 Int16（little-endian），由北往南、由西往東排列；數值 = 原值 × scale，缺值 = -32768。
網頁讀進來後自己上色，所以色階可以任意設計，滑鼠移上去也能讀到精確數值。

這些資料一天只更新一次，所以 6 小時內抓過就直接沿用快取。
需要套件：pip install numpy scipy
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src_common import http_get  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "grids"
CACHE = ROOT / ".cache" / "grids"
REFRESH_HOURS = 6
LAT_RANGE = (-60, 60)   # 只需要熱帶與副熱帶

SST_SOURCES = [
    ("NOAA OISST v2.1（近即時）",
     "https://coastwatch.pfeg.noaa.gov/erddap/griddap/ncdcOisst21NrtAgg_LonPM180.nc"
     "?sst[(last)][(0.0)][({s}):({n})][(-179.875):(179.875)]", "sst"),
]
OHC_SOURCES = [
    ("NOAA AOML 熱帶氣旋熱潛勢（TCHP）",
     "https://erddap.aoml.noaa.gov/hdb/erddap/griddap/TCHP.nc"
     "?Tropical_Cyclone_Heat_Potential[(last)][({s}):({n})][(-180.0):(180.0)]", "Tropical_Cyclone_Heat_Potential"),
    ("NOAA NESDIS 衛星海洋熱含量（OHC）",
     "https://erddap.aoml.noaa.gov/hdb/erddap/griddap/UOHC_{year}.nc"
     "?Ocean_Heat_Content[(last)][({s}):({n})][(-180.0):(180.0)]", "Ocean_Heat_Content"),
]
PRODUCTS = {"sst": (SST_SOURCES, 100, "°C"), "ohc": (OHC_SOURCES, 10, "kJ/cm²")}


def read_nc(raw, var):
    """讀 ERDDAP 給的 NetCDF-3 檔，回傳 (時間, 緯度陣列, 經度陣列, 2D 資料)。"""
    from scipy.io import netcdf_file
    with tempfile.NamedTemporaryFile(suffix=".nc") as f:
        f.write(raw)
        f.flush()
        with netcdf_file(f.name, "r", mmap=False) as nc:
            lat = np.array(nc.variables["latitude"][:], dtype=float)
            lon = np.array(nc.variables["longitude"][:], dtype=float)
            v = nc.variables[var]
            data = np.array(v[:], dtype=float)
            fill = [getattr(v, k) for k in ("_FillValue", "missing_value") if hasattr(v, k)]
            t = float(nc.variables["time"][:][-1])
    while data.ndim > 2:   # 拿掉 time、zlev 這些長度為 1 的維度
        data = data[-1]
    for fv in fill:
        data[data == float(np.asarray(fv).ravel()[0])] = np.nan
    data[np.abs(data) > 1e6] = np.nan
    return datetime.fromtimestamp(t, timezone.utc), lat, lon, data


def encode(name, src_name, when, lat, lon, data, scale, unit):
    if lat[0] < lat[-1]:   # 統一成由北往南
        lat, data = lat[::-1], data[::-1]
    q = np.where(np.isnan(data), -32768, np.clip(np.round(data * scale), -32767, 32767)).astype("<i2")
    meta = {"name": name, "source": src_name, "date": when.strftime("%Y-%m-%d"),
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nx": int(len(lon)), "ny": int(len(lat)),
            "lat0": float(lat[0]), "dlat": float(lat[1] - lat[0]),
            "lon0": float(lon[0]), "dlon": float(lon[1] - lon[0]),
            "scale": scale, "unit": unit, "missing": -32768}
    return meta, q.tobytes()


def fetch_product(name):
    sources, scale, unit = PRODUCTS[name]
    best = None
    year = datetime.now(timezone.utc).year
    for src_name, tmpl, var in sources:
        url = tmpl.format(s=LAT_RANGE[0], n=LAT_RANGE[1], year=year)
        try:
            when, lat, lon, data = read_nc(http_get(url, timeout=180), var)
        except Exception as e:
            print(f"  {src_name} 失敗：{str(e)[:120]}")
            continue
        age = (datetime.now(timezone.utc) - when).days
        print(f"  {src_name}：資料日期 {when:%Y-%m-%d}（{age} 天前）")
        if best is None or when > best[1]:
            best = (src_name, when, lat, lon, data)
        if age <= 3:   # 夠新了，不必再試下一個來源
            break
    if best is None:
        raise RuntimeError("所有來源都失敗")
    return encode(name, best[0], best[1], best[2], best[3], best[4], scale, unit)


def site_fallback(name):
    from fetch_stations import site_url
    url = site_url()
    if not url:
        raise RuntimeError("無法推算網站網址")
    meta = json.loads(http_get(f"{url}data/grids/{name}.json"))
    return meta, http_get(f"{url}data/grids/{name}.bin", timeout=120)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    for name in PRODUCTS:
        print("處理", name)
        cj, cb = CACHE / f"{name}.json", CACHE / f"{name}.bin"
        fresh = False
        if cj.exists() and cb.exists():
            fetched = datetime.fromisoformat(json.loads(cj.read_text())["fetched"].replace("Z", "+00:00"))
            fresh = (datetime.now(timezone.utc) - fetched).total_seconds() < REFRESH_HOURS * 3600
        if not fresh:
            try:
                meta, blob = fetch_product(name)
                cj.write_text(json.dumps(meta, ensure_ascii=False))
                cb.write_bytes(blob)
            except Exception as e:
                print(f"  下載失敗：{e}")
                if not cj.exists():
                    try:   # 快取也沒有：從網站上拿現有的版本
                        meta, blob = site_fallback(name)
                        cj.write_text(json.dumps(meta, ensure_ascii=False))
                        cb.write_bytes(blob)
                        print("  改用網站上現有的版本")
                    except Exception as e2:
                        print(f"  網站上也沒有：{e2}")
                        continue
        else:
            print("  6 小時內已更新過，沿用快取")
        shutil.copy(cj, OUT / f"{name}.json")
        shutil.copy(cb, OUT / f"{name}.bin")
        m = json.loads(cj.read_text())
        print(f"  輸出 {name}：{m['source']}，{m['date']}，{m['nx']}×{m['ny']}")


if __name__ == "__main__":
    main()
