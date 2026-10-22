"""
產生網站用的海岸線檔案（只需執行一次，之後檔案存在就會自動略過）：
  data/coast_50m.json        縮小地圖時用的整份海岸線（約 0.9 MB）
  data/coast10/{緯}_{經}.json  放大地圖時用的精細海岸線，切成 10°×10° 小塊，
                              網頁只下載畫面範圍內的幾塊
資料來源：Natural Earth（公有領域）
"""
import json
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SRC = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_{}_coastline.geojson"
BOX = 10      # 每塊幾度
PIECE = 200   # 長海岸線切成每段最多幾個點


def load_lines(res, decimals):
    with urllib.request.urlopen(SRC.format(res), timeout=120) as r:
        gj = json.load(r)
    lines = []
    for f in gj["features"]:
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for part in parts:
            out = []
            for x, y in part:
                q = [round(x, decimals), round(y, decimals)]
                if not out or out[-1] != q:
                    out.append(q)
            if len(out) > 1:
                lines.append(out)
    return lines


def main():
    if (DATA / "coast_50m.json").exists() and (DATA / "coast10" / "index.json").exists():
        print("海岸線檔案已存在，略過。")
        return
    DATA.mkdir(exist_ok=True)

    lines50 = load_lines("50m", 2)
    (DATA / "coast_50m.json").write_text(json.dumps(lines50, separators=(",", ":")))
    print("coast_50m.json：", len(lines50), "條線")

    boxes = {}
    for line in load_lines("10m", 2):
        for i in range(0, len(line) - 1, PIECE - 1):
            piece = line[i:i + PIECE]
            lon, lat = piece[0]
            key = f"{int(lat // BOX * BOX)}_{int(lon // BOX * BOX)}"
            boxes.setdefault(key, []).append(piece)
    out = DATA / "coast10"
    out.mkdir(exist_ok=True)
    for key, pieces in boxes.items():
        (out / f"{key}.json").write_text(json.dumps(pieces, separators=(",", ":")))
    (out / "index.json").write_text(json.dumps(sorted(boxes)))
    print("coast10：", len(boxes), "塊")


if __name__ == "__main__":
    main()
