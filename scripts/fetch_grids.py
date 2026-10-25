"""
下載海溫與海洋熱含量的格點資料，存成網頁可直接讀取的小型二進位檔：
  data/grids/sst.bin + sst.json   海溫（NOAA OISST v2.1 近即時版，0.25°，每日）
  data/grids/ohc.bin + ohc.json   海洋熱含量（由 Copernicus Marine 全球海洋模式的水溫計算，0.25°，每日；
                                  失敗時改用 NOAA AOML TCHP 或 NESDIS OHC）

.bin 是 Int16（little-endian），由北往南、由西往東排列；數值 = 原值 × scale，缺值 = -32768。
網頁讀進來後自己上色，所以色階可以任意設計，滑鼠移上去也能讀到精確數值。

這些資料一天只更新一次，所以 6 小時內抓過就直接沿用快取。
需要套件：pip install numpy scipy copernicusmarine
Copernicus Marine 需要免費帳號，請在 GitHub Secrets 設定
  COPERNICUSMARINE_SERVICE_USERNAME、COPERNICUSMARINE_SERVICE_PASSWORD

python scripts/fetch_grids.py --cache-only   只用快取或網站上的現有版本，不下載（給每 20 分鐘的「更新測站」用）
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src_common import http_get  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "grids"
CACHE = ROOT / ".cache" / "grids"
REFRESH_HOURS = 6
MAX_DATA_AGE_DAYS = 3
LAT_RANGE = (-60, 60)   # 只需要熱帶與副熱帶
OHC_LAT_RANGE = (-45, 50)   # 海洋熱含量只在熱帶有意義，範圍小一點可少下載很多資料

SST_SOURCES = [
    ("NOAA OISST v2.1（近即時）",
     "https://coastwatch.pfeg.noaa.gov/erddap/griddap/ncdcOisst21NrtAgg_LonPM180.nc"
     "?sst[(last)][(0.0)][({s}):({n})][(-179.875):(179.875)]", "sst"),
]
OHC_SOURCES = [
    ("Copernicus Marine 全球海洋模式（CMEMS）", None, "cmems"),
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


def cmems_ohc():
    """
    用 Copernicus Marine 全球海洋分析（1/12°、每日平均）的水溫剖面計算海洋熱含量：
      OHC = ρ × cp × ∫ max(T − 26°C, 0) dz（從海面積分到 26°C 等溫線）
    為了省記憶體，先把水平解析度平均成 0.25° 再積分。
    """
    if not (os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME") and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")):
        raise RuntimeError("沒有設定 Copernicus Marine 帳號（COPERNICUSMARINE_SERVICE_USERNAME／PASSWORD）")
    import copernicusmarine
    RHO, CP = 1025.0, 3993.0          # 海水密度 kg/m³、比熱 J/(kg·K)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    last = None
    for back in (1, 2, 3):            # 昨天的分析場最可靠；還沒出來就往前找
        day = today - timedelta(days=back)
        try:
            ds = copernicusmarine.open_dataset(
                dataset_id="cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m", variables=["thetao"],
                minimum_longitude=-180, maximum_longitude=180, minimum_latitude=OHC_LAT_RANGE[0], maximum_latitude=OHC_LAT_RANGE[1],
                minimum_depth=0, maximum_depth=400, start_datetime=day, end_datetime=day,
                coordinates_selection_method="nearest")
            T = ds["thetao"].isel(time=0)
            T = T.coarsen(latitude=3, longitude=3, boundary="trim").mean().load()
            break
        except Exception as e:
            last = e
            print(f"  CMEMS {day:%Y-%m-%d} 讀取失敗：{str(e)[:120]}")
    else:
        raise RuntimeError(f"CMEMS 最近 3 天都讀不到：{last}")
    z = T["depth"].values.astype(float)
    edges = np.concatenate([[0.0], (z[1:] + z[:-1]) / 2, [z[-1] + (z[-1] - z[-2]) / 2]])
    dz = np.diff(edges)[:, None, None]
    t = T.values.astype(float)                       # (depth, lat, lon)
    surface_nan = np.isnan(t[0])
    excess = np.nan_to_num(np.clip(t - 26.0, 0, None))
    ohc = (excess * dz).sum(axis=0) * RHO * CP / 1e7   # J/m² → kJ/cm²
    ohc[surface_nan] = np.nan                          # 陸地
    return day, T["latitude"].values.astype(float), T["longitude"].values.astype(float), ohc


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
        try:
            if var == "cmems":
                when, lat, lon, data = cmems_ohc()
            else:
                url = tmpl.format(s=LAT_RANGE[0], n=LAT_RANGE[1], year=year)
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
            meta = json.loads(cj.read_text())
            data_age = (datetime.now(timezone.utc) - datetime.fromisoformat(meta["date"]).replace(tzinfo=timezone.utc)).days
            # 6 小時內抓過、而且資料本身夠新（3 天內）才沿用快取；
            # 資料日期很舊（例如來源早已停止更新）就重新下載，讓程式有機會改用其他來源
            fresh = (datetime.now(timezone.utc) - fetched).total_seconds() < REFRESH_HOURS * 3600 and data_age <= MAX_DATA_AGE_DAYS
        if "--cache-only" in sys.argv:
            fresh = cj.exists() and cb.exists()   # 只用現有版本，不下載
        if not fresh:
            try:
                if "--cache-only" in sys.argv:
                    raise RuntimeError("--cache-only：不下載")
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
            print("  6 小時內已更新過且資料夠新，沿用快取")
        shutil.copy(cj, OUT / f"{name}.json")
        shutil.copy(cb, OUT / f"{name}.bin")
        m = json.loads(cj.read_text())
        print(f"  輸出 {name}：{m['source']}，{m['date']}，{m['nx']}×{m['ny']}")


if __name__ == "__main__":
    main()
