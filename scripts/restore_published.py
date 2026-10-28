"""
把「更新資料」最近一次產生的路徑資料與更新紀錄放回 data/，讓其他流程（例如「更新測站」）發布網站時不會蓋掉它們。
路徑資料不再存進儲存庫（每 20 分鐘一份會讓儲存庫越長越大），而是放在 GitHub 的快取；
快取沒有時，改從網站上現有的版本下載。
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / ".cache" / "published"


def main():
    for name in ("tracks.json", "update_log.json"):
        dst = ROOT / "data" / name
        if (PUB / name).exists():
            shutil.copy(PUB / name, dst)
            print(f"{name}：使用快取裡的版本")
            continue
        try:
            from fetch_stations import site_url
            from src_common import http_get
            dst.write_bytes(http_get(site_url() + "data/" + name))
            print(f"{name}：快取沒有，改用網站上的版本")
        except Exception as e:
            print(f"{name}：快取與網站都沒有（{e}），保留儲存庫裡的版本")


if __name__ == "__main__":
    main()
