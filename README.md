# 熱帶氣旋觀測站

一個免費架在 GitHub Pages 上的熱帶氣旋地圖：衛星雲圖、雷達、海溫、風場，加上可自由開關的各家路徑預報。

## 檔案說明

| 檔案 | 用途 |
|---|---|
| `index.html` | 網站本體（地圖、圖層面板、時間軸）。最上方 `CONFIG` 區塊可以調整大部分設定。 |
| `data/tracks.json` | 路徑資料。目前是虛構的示範颱風。 |
| `data/wind.json` | 風場資料，由自動排程產生（一開始不存在是正常的）。 |
| `scripts/fetch_tracks.py` | 下載 ATCF 路徑檔並轉成 `tracks.json`。 |
| `scripts/fetch_wind.py` | 下載 GFS 風場並轉成 `wind.json`。 |
| `.github/workflows/update-data.yml` | 讓 GitHub 每 3 小時自動執行上面兩支程式。 |

## 第一次上線（約 15 分鐘）

1. 到 https://github.com 註冊帳號。
2. 右上角「+」→「New repository」，名稱例如 `tc-site`，選 **Public**，按「Create repository」。
3. 在新儲存庫頁面點「uploading an existing file」，把這個資料夾裡**所有檔案和資料夾**拖進去，按「Commit changes」。
   - 注意：`.github` 是隱藏資料夾，Mac 按 `Cmd+Shift+.`、Windows 在檔案總管勾選「顯示隱藏的項目」才看得到。
4. 「Settings」→ 左側「Pages」→ Source 選「Deploy from a branch」，Branch 選 `main`、資料夾 `/ (root)`，按 Save。
5. 「Settings」→「Actions」→「General」→ 最下方 Workflow permissions 選「Read and write permissions」，Save。
6. 「Actions」分頁 → 左側「更新資料」→「Run workflow」，手動跑第一次。
7. 一兩分鐘後，網站會出現在 `https://你的帳號.github.io/tc-site/`。

## 在自己電腦上預覽

直接雙擊 `index.html` 時，瀏覽器會擋住讀取 `data/` 裡的檔案。請在這個資料夾開終端機，執行：

```
python -m http.server 8000
```

再打開 http://localhost:8000 。

## 接上真實颱風資料

打開 `scripts/fetch_tracks.py`，在 `SOURCES` 加入目前活躍颱風的 a-deck／b-deck 網址（格式說明在檔案註解裡）。`SOURCES` 為空時會輸出示範資料。

## 資料來源與使用規範

- 衛星雲圖、海溫：NASA GIBS（公開，請保留出處標示）
- 雷達：RainViewer 免費 API（2026 年起限制最高縮放 7、僅過去資料、限個人或教育用途）
- 風場：NOAA GFS（美國政府公開資料）
- 底圖：OpenStreetMap、CARTO

若網站要商業化，請逐一重新確認各來源的授權條款。

## 常見問題

- **衛星雲圖一片空白**：GIBS 即時資料偶爾會延遲超過一小時。把 `CONFIG.latencyMin` 調大（例如 90）。
- **風場勾不起來**：Actions 還沒成功執行過，到「Actions」分頁看錯誤訊息。
- **圖層名稱失效**：到 NASA Worldview 搜尋圖層，複製新名稱貼到 `CONFIG.sat`。
