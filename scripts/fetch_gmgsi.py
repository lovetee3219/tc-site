"""
全球紅外線合成雲圖（NOAA GMGSI）——仿 CyclonicWx 的「Global Satellite Infrared Brightness Temperature Composite」。

來源：NOAA/NESDIS「Global Mosaic of Geostationary Satellite Imagery」長波紅外線（GMGSI_LW），
由 GOES 東／西、向日葵、Meteosat-9、Meteosat-11 拼成的全球亮溫圖，每小時一張，存放在 AWS 公開空間：
  https://noaa-gmgsi-pds.s3.amazonaws.com/GMGSI_LW/YYYY/MM/DD/HH/GLOBCOMPLIR_nc.YYYYMMDDHH
因為所有區域用同一套亮溫資料，網頁可以用同一個色階上色，全球完全一致。

處理：下載 NetCDF → 取出亮溫（K）→ 重新取樣成 0.1° 經緯網格 → 存成 8 位元灰階 PNG：
  像素值 0 = 無資料；1～255 對應亮溫 160～340 K（約 0.7 K 一階）。
網頁讀進 PNG 後自己依色階上色。保留最近 FRAMES 個小時，供動畫使用。
需要套件：pip install numpy xarray h5netcdf h5py pillow
"""
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src_common import http_get  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "gmgsi"
OUT = ROOT / "data" / "gmgsi"
URL = "https://noaa-gmgsi-pds.s3.amazonaws.com/GMGSI_LW/{t:%Y/%m/%d/%H}/GLOBCOMPLIR_nc.{t:%Y%m%d%H}"
FRAMES = 12         # 保留最近幾個小時（和網頁動畫的張數相同）
LOOKBACK = 15       # 往前找幾個小時（資料通常延遲 1～3 小時）
RES = 0.1           # 輸出網格解析度（度）
LAT_MAX = 70.0      # GMGSI 有效範圍約南北緯 71 度
BT_MIN, BT_MAX = 160.0, 340.0


def read_bt(raw):
    """讀 NetCDF，回傳 (lat 1D, lon 1D, 亮溫 2D，單位 K)。"""
    import xarray as xr
    with tempfile.NamedTemporaryFile(suffix=".nc") as f:
        f.write(raw)
        f.flush()
        ds = xr.open_dataset(f.name, engine="h5netcdf").load()
    # 找出主要的 2D 資料變數（名稱可能隨版本改變，所以不寫死）
    var = max((v for v in ds.data_vars if ds[v].ndim >= 2), key=lambda v: ds[v].size)
    data = np.squeeze(ds[var].values).astype("float32")
    lat = ds["lat"].values if "lat" in ds else ds["latitude"].values
    lon = ds["lon"].values if "lon" in ds else ds["longitude"].values
    lat = lat[:, 0] if lat.ndim == 2 else lat
    lon = lon[0, :] if lon.ndim == 2 else lon
    valid = data[np.isfinite(data)]
    med = float(np.median(valid)) if valid.size else 0
    if med < 100:          # 若以攝氏儲存，換成 K
        data = data + 273.15
    print(f"  變數 {var}：{data.shape}，中位數 {np.nanmedian(data):.1f} K")
    return lat.astype("float64"), lon.astype("float64"), data


def regrid(lat, lon, data):
    """以最近點重新取樣到規則的 0.1° 網格（由北到南、由西到東，經度 -180～180）。"""
    lon = ((lon + 180) % 360) - 180
    tlat = np.arange(LAT_MAX - RES / 2, -LAT_MAX, -RES)
    tlon = np.arange(-180 + RES / 2, 180, RES)
    oy = np.argsort(lat)
    iy = oy[np.clip(np.searchsorted(lat[oy], tlat), 0, len(lat) - 1)]
    ox = np.argsort(lon)
    ix = ox[np.clip(np.searchsorted(lon[ox], tlon), 0, len(lon) - 1)]
    out = data[np.ix_(iy, ix)]
    out[np.abs(lat[iy] - tlat) > 0.5, :] = np.nan   # 超出原始資料範圍的列設為無資料
    return tlat, tlon, out


def encode_png(bt):
    from PIL import Image
    v = np.where(np.isfinite(bt) & (bt > 100),
                 np.clip(np.round((bt - BT_MIN) / (BT_MAX - BT_MIN) * 254) + 1, 1, 255), 0).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(v, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    have = {p.stem for p in CACHE.glob("*.png")}
    for h in range(0 if "--cache-only" in sys.argv else LOOKBACK):   # --cache-only：只發布快取裡已有的，不下載
        t = now - timedelta(hours=h)
        key = f"{t:%Y%m%d%H}"
        if key in have:
            continue
        try:
            raw = http_get(URL.format(t=t), timeout=120, retries=1)
        except Exception:
            continue    # 這個小時還沒出來
        try:
            lat, lon, bt = read_bt(raw)
            _, _, grid = regrid(lat, lon, bt)
            (CACHE / f"{key}.png").write_bytes(encode_png(grid))
            print(f"GMGSI {key}：已處理")
        except Exception as e:
            print(f"GMGSI {key}：處理失敗 {e}")
    keys = sorted(p.stem for p in CACHE.glob("*.png"))
    for k in keys[:-FRAMES]:
        (CACHE / f"{k}.png").unlink()
    keys = keys[-FRAMES:]
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for k in keys:
        shutil.copy(CACHE / f"{k}.png", OUT / f"{k}.png")
    meta = {"frames": [{"time": f"{k[:4]}-{k[4:6]}-{k[6:8]}T{k[8:10]}:00:00Z", "file": f"{k}.png"} for k in keys],
            "lat0": LAT_MAX - RES / 2, "lon0": -180 + RES / 2, "res": RES, "btMin": BT_MIN, "btMax": BT_MAX}
    (OUT / "index.json").write_text(json.dumps(meta))
    print(f"GMGSI：共 {len(keys)} 張（{keys[0] if keys else '無'} ～ {keys[-1] if keys else '無'}）")


if __name__ == "__main__":
    main()
