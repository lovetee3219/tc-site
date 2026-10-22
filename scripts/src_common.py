"""各資料來源共用的小工具。"""
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (tc-site personal project; GitHub Actions)"
MS_TO_KT = 1.943844


def http_get(url, headers=None, timeout=60, retries=2) -> bytes:
    """下載網址內容。網路暫時不穩時自動重試（找不到檔案的 404 不重試）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404) or attempt == retries:
                raise
        except Exception:
            if attempt == retries:
                raise
        time.sleep(3 * (attempt + 1))


def to_iso(dt: datetime) -> str:
    """任何時區的時間 → UTC 的 ISO 字串，例如 2026-10-22T00:00:00Z"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s: str) -> datetime:
    """解析帶時區的 ISO 時間，例如 2026-10-22T08:00:00+08:00"""
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def item(source, forecasts, lat, lon, time, number=None, name=None, best=None):
    """
    各來源統一回傳這種格式，由 fetch_tracks.py 負責對應到正確的颱風：
      number：("WP", 29) 這種 JTWC 編號；沒有時改用位置比對
      lat/lon/time：目前（分析時刻）的中心位置
    """
    return {"source": source, "number": number, "name": name, "lat": lat, "lon": lon,
            "time": time, "forecasts": forecasts, "best": best or []}
