"""
下載 ATCF 格式的路徑檔（a-deck = 各機構／模式預報、b-deck = 最佳路徑），
轉成網站使用的 data/tracks.json。

ATCF 是美國 NHC／JTWC 使用、也被許多研究單位採用的標準文字格式，
一行代表「某個模式、某次起報、某個預報時距」的一筆資料：
  BASIN, CY, YYYYMMDDHH, TECHNUM, TECH, TAU, LAT, LON, VMAX, MSLP, ...
例：WP, 25, 2026102000, 03, AVNO, 24, 215N, 1302E, 95, 955, TY, ...

另外會合併以下官方與模式來源（各自寫在 src_*.py）：
  JTWC（src_jtwc.py）、JMA（src_jma.py）、CWA（src_cwa.py，需授權碼）、ECMWF（src_ecmwf.py）
任何一個來源失敗都不影響其他來源。

使用方式：
  python scripts/fetch_tracks.py            # 依 SOURCES 下載，並合併各官方來源
  python scripts/fetch_tracks.py --demo     # 產生示範資料（不需網路）
"""
import gzip
import json
import math
import os
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "tracks.json"

# ── 要抓的檔案清單 ──────────────────────────────────────────────
# 每個熱帶氣旋各有一組 a-deck、b-deck，檔名規則：
#   a{海域}{編號}{年}.dat(.gz)   例：aal092026.dat.gz、awp252026.dat
# 公開目錄（使用前請確認網址仍有效、並遵守各站使用規範）：
#   NHC（大西洋 al、東太平洋 ep、中太平洋 cp）
#     a-deck: https://ftp.nhc.noaa.gov/atcf/aid_public/
#     b-deck: https://ftp.nhc.noaa.gov/atcf/btk/
#   其他海域（西北太平洋 wp、北印度洋 io、南半球 sh）可找研究單位的公開鏡像，
#   例如 UCAR/NCAR RAL 的熱帶氣旋導引資料，網址請到其網站確認。
SOURCES = [
    # 一般情況不必填：活躍的颱風會自動找出來（見下方 AUTO_HOURS）。
    # 只有自動尋找漏掉、或想固定追蹤某個颱風時，才在這裡手動加入，例如：
    # {"storm": "WP292026",
    #  "adeck": "https://hurricanes.ral.ucar.edu/repository/data/adecks_open/awp292026.dat",
    #  "bdeck": ["https://hurricanes.ral.ucar.edu/repository/data/bdecks_open/bwp292026.dat"]},
]

# ── 自動找出目前活躍的颱風 ─────────────────────────────────────
# 讀取 NCAR 公開 a-deck 目錄，最近 AUTO_HOURS 小時內有更新、編號小於 70 的檔案都視為活躍颱風，
# 自動加進 SOURCES（不必再手動新增）。設成 0 就關閉自動尋找。
AUTO_HOURS = 36
NCAR = "https://hurricanes.ral.ucar.edu/repository/data/"

# ── 中文譯名對照表 ─────────────────────────────────────────────
# 來源：中央氣象署《颱風百問》表 2-1「西北太平洋及南海颱風中文譯名及國際命名對照表」（2026 年 6 月更新）。
# 中央氣象署有發布某颱風時，以氣象署當下的譯名為準；沒有發布時（例如還在遠洋）才查這張表。
# 颱風委員會每年會更換部分名字，氣象署更新對照表後，照格式修改這裡即可。
_NAME_TABLE = """
丹瑞 高基 娜克莉 科羅旺 翠絲 | Damrey Koki Nakri Krovanh Trases
天馬 銀杏 風神 杜鵑 木蘭 | Tianma Yinxing Fengshen Dujuan Mulan
鴻雁 青蛙 海鷗 舒力基 米雷 | Kirogi Gaeguri Kalmaegi Surigae Meari
鴛鴦 點心 鳳凰 彩雲 青馬 | Yun-yeung Dim-Sum Fung-wong Choi-wan Tsing-ma
小犬 希比 天琴 小熊 蝎虎 | Koinu Hebi Koto Koguma Tokage
布拉萬 帕布 洛鞍 薔琵 坡鹿 | Bolaven Pabuk Nokaen Champi Ong-mang
三巴 蝴蝶 西望洋 煙花 梅花 | Sanba Wutip Penha In-Fa Muifa
鯉魚 聖帕 鸚鵡 查帕卡 莫柏 | Jelawat Sepat Nuri Cempaka Merbok
蒂魯 木恩 辛樂克 尼伯特 南瑪都 | Tirou Mun Sinlaku Nepartak Nanmadol
馬力斯 丹娜絲 哈格比 盧碧 塔拉斯 | Maliksi Danas Hagupit Lupit Talas
凱米 百合 薔蜜 銀河 核桃 | Gaemi Nari Jangmi Mirinae Hodu
巴比侖 薇帕 米克拉 妮妲 庫拉 | Prapiroon Wipha Mekkhala Nida Kulap
瑪莉亞 范斯高 無花果 奧麥斯 洛克 | Maria Francisco Higos Omais Roke
山神 竹節草 巴威 布袋蓮 桑卡 | Son-Tinh Co-may Bavi Luc-Binh Sonca
安比 柯羅莎 梅莎 璨樹 尼莎 | Ampil Krosa Maysak Chanthu Nesat
悟空 白鹿 海神 電母 海棠 | Wukong Bailu Haishen Dianmu Haitang
雲雀 楊柳 紅霞 蒲公英 蜻蜓 | Jongdari Podul Noul Mindulle Jamjari
珊珊 玲玲 白海豚 獅子山 榕樹 | Shanshan Lingling Dolphin Lionrock Banyan
船尾 劍魚 鯨魚 時鐘 山貓 | Tomo Kajiki Kujira Tokei Yamaneko
麗琵 藍湖 昌鴻 南修 帕卡 | Leepi Nongfa Chan-hom Namtheun Pakhar
貝碧佳 琵琶 琵鷺 瑪瑙 珊瑚 | Bebinca Peipah Peilou Malou Sanvu
葡萄桑 塔巴 南卡 妮亞圖 瑪娃 | Pulasan Tapah Nangka Nyatoh Mawar
蘇力 米塔 沙德爾 沙布爾 谷超 | Soulik Mitag Saudel Sarbul Guchol
西馬隆 樺加沙 紫檀 阿穆丘 泰利 | Cimaron Ragasa Narra Amuyao Talim
娜尼 浣熊 簡拉維 古莎莉 大麥 | Narae Neoguri Gaenari Gosari Bori
布拉帕 博羅依 閃電 芙蓉 卡努 | Burapha Bualoi Atsani Chaba Khanun
百里嘉 麥德姆 艾陶 艾利 蘭恩 | Barijat Matmo Etau Aere Lan
花斑 哈隆 班朗 桑達 海星 | Hoaban Halong Bang-lang Songda Saobien
"""
_key = lambda en: re.sub(r"[^a-z]", "", en.lower())   # In-Fa、In-fa、IN-FA 都視為同一個
NAMES_ZH = {}
for _line in _NAME_TABLE.strip().splitlines():
    _zh, _en = _line.split("|")
    NAMES_ZH.update({_key(e): z for z, e in zip(_zh.split(), _en.split())})


def discover_sources():
    from datetime import datetime, timedelta, timezone
    if AUTO_HOURS <= 0:
        return []
    try:
        html = fetch_text(NCAR + "adecks_open/")
    except Exception as e:
        print("自動尋找颱風失敗：", e)
        return []
    now = datetime.now(timezone.utc)
    found = []
    # 目錄頁的日期可能是「2026-10-23 05:41」或「23-Oct-2026 05:41」兩種寫法
    pat = r'href="a(wp|io|sh|ep|cp|al)(\d\d)(\d{4})\.dat".*?(\d{4}-\d\d-\d\d \d\d:\d\d|\d\d-[A-Za-z]{3}-\d{4} \d\d:\d\d)'
    for m in re.finditer(pat, html):
        basin, num, year, mod = m.groups()
        fmt = "%Y-%m-%d %H:%M" if mod[4] == "-" else "%d-%b-%Y %H:%M"
        when = datetime.strptime(mod, fmt).replace(tzinfo=timezone.utc)
        if int(num) >= 70 or now - when > timedelta(hours=AUTO_HOURS):
            continue
        fname = f"{basin}{num}{year}.dat"
        found.append({"storm": f"{basin.upper()}{num}{year}",
                      "adeck": f"{NCAR}adecks_open/a{fname}",
                      "bdeck": [f"{NCAR}bdecks_open/b{fname}", f"{NCAR}bdecks_open/{year}/b{fname}"]})
    print("自動找到的颱風：", "、".join(s["storm"] for s in found) or "（無）")
    return found


# ── 模式代碼 → 顯示名稱與分類 ──────────────────────────────────
# category: official（官方）、nwp（數值模式）、ai（AI 模式）
# 代碼會隨年度增減，請以資料來源當季的說明為準；不在表內的代碼會被略過。
MODELS = {
    # 官方預報
    "OFCL": ("NHC 官方", "official"),
    "JTWC": ("JTWC 官方", "official"),
    "RJTD": ("JMA 官方", "official"),
    "RCTP": ("CWA 官方", "official"),
    # GFS（單一預報另由 src_gfs.py 補上）
    "AVNO": ("GFS", "nwp"),
    "AEMN": ("GEFS 系集平均", "nwp"),
    # ECMWF
    "EMX":  ("ECMWF IFS", "nwp"),
    "ECMF": ("ECMWF IFS", "nwp"),
    "EEMN": ("ECMWF 系集平均", "nwp"),
}


# 系集成員：代碼開頭 → 群組名稱。同一群組在網站上用一個勾選框一起開關。
# 例：AP01~AP30 是 GEFS 的 30 個成員，AC00 是控制組。
ENSEMBLES = {
    "AP": "GEFS 系集成員", "AC": "GEFS 系集成員",
    "EP": "ECMWF 系集成員", "EC": "ECMWF 系集成員",
}


def classify(tech):
    """回傳 (顯示名稱, 分類, 系集群組)；不認得的代碼回傳 None。"""
    if tech in MODELS:
        label, cat = MODELS[tech]
        return label, cat, None
    if len(tech) == 4 and tech[2:].isdigit() and tech[:2] in ENSEMBLES:
        return tech, "ens", ENSEMBLES[tech[:2]]
    return None


def fetch_text(url) -> str:
    """url 可以是單一網址或清單；清單會依序嘗試，回傳第一個成功的。"""
    if isinstance(url, (list, tuple)):
        last = None
        for u in url:
            try:
                return fetch_text(u)
            except Exception as e:
                last = e
        raise last
    req = urllib.request.Request(url, headers={"User-Agent": "tc-site/0.1 (personal project)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def parse_latlon(lat: str, lon: str):
    """'215N' → 21.5，'1302E' → 130.2（南緯、西經為負值）"""
    la = int(lat[:-1]) / 10.0 * (1 if lat[-1] == "N" else -1)
    lo = int(lon[:-1]) / 10.0 * (1 if lon[-1] == "E" else -1)
    return round(la, 1), round(lo, 1)


def parse_atcf(text: str):
    """回傳 {(tech, init): [點...]}；同一時距只留一筆（風半徑會讓同一點重複多行）。"""
    runs = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 9 or not f[6] or not f[7]:
            continue
        try:
            init, tech, tau = f[2], f[4], int(f[5])
            lat, lon = parse_latlon(f[6], f[7])
        except (ValueError, IndexError):
            continue
        vmax = int(f[8]) if f[8].isdigit() and int(f[8]) > 0 else None
        mslp = int(f[9]) if len(f) > 9 and f[9].isdigit() and int(f[9]) > 0 else None
        pts = runs.setdefault((tech, init), {})
        if tau not in pts:
            pts[tau] = {"tau": tau, "lat": lat, "lon": lon, "vmax": vmax, "mslp": mslp}
    return {k: sorted(v.values(), key=lambda p: p["tau"]) for k, v in runs.items()}


NOT_NAMES = {"", "INVEST", "NONAME", "UNNAMED", "NAMELESS"}
# 還沒命名時，JTWC 會用英文數字當名稱（例如 THIRTY、TWENTYNINE），這些不算正式名稱
NUMBER_WORD = re.compile(r"^(ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|THIRTEEN|FOURTEEN|"
                         r"FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|NINETEEN|TWENTY|THIRTY|FORTY|FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY)+$")


def find_name(text: str):
    """從 ATCF 第 28 欄（STORMNAME）找最新一筆颱風名稱，例如 CHAMPI → Champi。"""
    best = ("", None)
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if (len(f) > 27 and f[27].upper() not in NOT_NAMES and f[27].replace("-", "").isalpha()
                and not NUMBER_WORD.match(f[27].upper().replace("-", ""))):
            if f[2] >= best[0]:
                best = (f[2], f[27])
    return best[1].capitalize() if best[1] else None


def latest_runs(runs):
    """每個模式只保留最新一次起報。"""
    best = {}
    for (tech, init), pts in runs.items():
        if classify(tech) and (tech not in best or init > best[tech][0]):
            best[tech] = (init, pts)
    return best


def iso(yyyymmddhh: str) -> str:
    t = datetime.strptime(yyyymmddhh, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return t.isoformat().replace("+00:00", "Z")


def build_storm(src):
    """a-deck 與 b-deck 分開處理：其中一個抓不到，另一個仍會顯示。"""
    storm = {"id": src["storm"], "basin": src["storm"][:2].upper(), "name": src.get("name") or src["storm"], "best": [], "forecasts": []}
    auto_name = None
    if src.get("bdeck"):
        try:
            text = fetch_text(src["bdeck"])
            auto_name = find_name(text)
            b = parse_atcf(text)
            pts = sorted(((init, p[0]) for (_, init), p in b.items() if p), key=lambda x: x[0])
            storm["best"] = [{**p, "time": iso(init)} for init, p in pts]
            print(f"  b-deck：{len(storm['best'])} 個實測點")
        except Exception as e:
            print("  b-deck 失敗：", e)
    if src.get("adeck"):
        try:
            text = fetch_text(src["adeck"])
            auto_name = auto_name or find_name(text)
            runs = parse_atcf(text)
            # CARQ = 作業單位每次的即時定位；b-deck 抓不到時，用它當實測路徑
            carq = sorted((init, pts[0]) for (tech, init), pts in runs.items() if tech == "CARQ" and pts and pts[0]["tau"] == 0)
            if carq and not storm["best"]:
                storm["best"] = [{k: v for k, v in p.items() if k != "tau"} | {"time": iso(init)} for init, p in carq]
                print(f"  b-deck 沒有資料，改用 CARQ 即時定位：{len(carq)} 個點")
            found = sorted({tech for tech, _ in runs})
            print("  a-deck 內的模式代碼：", " ".join(found))
            for tech, (init, pts) in latest_runs(runs).items():
                if len(pts) < 2:
                    continue
                label, cat, group = classify(tech)
                fc = {"model": tech, "label": label, "category": cat, "init": iso(init), "points": pts}
                if group:
                    fc["group"] = group
                storm["forecasts"].append(fc)
            shown = [f["model"] for f in storm["forecasts"] if f["category"] != "ens"]
            groups = sorted({f["group"] for f in storm["forecasts"] if f["category"] == "ens"})
            print("  會顯示的預報：", " ".join(shown) or "（無）", "｜系集：", "、".join(groups) or "（無）")
        except Exception as e:
            print("  a-deck 失敗：", e)
    # 只記錄檔案裡最新資料的時間，不在這裡刪掉颱風：
    # 是否仍活躍，最後統一由 filter_active 依官方預報判斷（避免 NCAR 檔案更新較慢時，把活躍颱風的過去路徑一起丟掉）
    latest = max([f["init"] for f in storm["forecasts"]] + [b["time"] for b in storm["best"]], default=None)
    if latest:
        from datetime import datetime, timezone
        hours = (datetime.now(timezone.utc) - datetime.fromisoformat(latest.replace("Z", "+00:00"))).total_seconds() / 3600
        print(f"  檔案最新資料：{hours:.0f} 小時前")
    if auto_name:
        storm["name_en"] = auto_name
    print("  英文名：", auto_name or "（尚未命名）")
    set_current(storm)
    return storm


def set_current(storm):
    """記下颱風目前位置，用來把其他來源（JMA、CWA 等）對應到同一個颱風。"""
    if storm["best"]:
        p = storm["best"][-1]
        storm["current"] = {"lat": p["lat"], "lon": p["lon"], "time": p["time"]}
    elif storm["forecasts"]:
        f = max(storm["forecasts"], key=lambda f: f["init"])
        storm["current"] = {"lat": f["points"][0]["lat"], "lon": f["points"][0]["lon"], "time": f["init"]}


def distance_deg(a, b):
    dlon = (a["lon"] - b["lon"] + 540) % 360 - 180
    return math.hypot(a["lat"] - b["lat"], dlon * math.cos(math.radians(a["lat"])))


def has_number(storm):
    """颱風代碼裡有沒有 JTWC 編號（例如 WP302026 有；JMA-2 沒有）。"""
    sid = storm["id"]
    return len(sid) >= 4 and sid[:2].isalpha() and sid[2:4].isdigit()


def attach(storms, it, create=True):
    """把一筆外部來源的預報併入對應的颱風；找不到時，create=True 才新增，否則略過。"""
    from datetime import datetime, timezone
    year = datetime.now(timezone.utc).year
    target = None
    if it["number"]:
        basin, num = it["number"]
        target = next((s for s in storms if has_number(s) and s["id"][:2] == basin and int(s["id"][2:4]) == num), None)
    if target is None:
        near = [(distance_deg(s["current"], it), s) for s in storms if s.get("current")]
        near = [x for x in near if x[0] < 5]   # 5 度以內視為同一個颱風
        if near:
            target = min(near, key=lambda x: x[0])[1]
    if target is None and not create:
        return None
    if target is None:
        sid = f"{it['number'][0]}{it['number'][1]:02d}{year}" if it["number"] else f"{it['source']}-{len(storms) + 1}"
        # 沒有 JTWC 編號的只會來自 JMA、CWA，都是西北太平洋
        basin = it["number"][0] if it["number"] else "WP"
        target = {"id": sid, "basin": basin, "name": it["name"] or sid, "best": [], "forecasts": [],
                  "current": {"lat": it["lat"], "lon": it["lon"], "time": it["time"]}}
        storms.append(target)
    # 原本沒有編號的颱風（只從 JMA、CWA 得知），遇到帶編號的來源（GFS、ECMWF 等）時補上編號，
    # 之後 JTWC 才知道要下載哪一份警報，名稱也能加上（30W）這類編號
    if it["number"] and not has_number(target):
        old = target["id"]
        target["id"], target["basin"] = f"{it['number'][0]}{it['number'][1]:02d}{year}", it["number"][0]
        print(f"  （{old} 依編號改為 {target['id']}）")
    codes = {f["model"] for f in it["forecasts"]}
    target["forecasts"] = [f for f in target["forecasts"] if f["model"] not in codes] + it["forecasts"]
    if not target["best"] and it["best"]:
        target["best"] = it["best"]
        set_current(target)
    if it.get("name") and not target.get("name_en"):
        target["name_en"] = it["name"]
    if it.get("name_zh") and not target.get("name_zh"):
        target["name_zh"] = it["name_zh"]
    return target


# ── 模式資料的更新排程 ─────────────────────────────────────────
# 各模式在每天固定的時間（UTC）嘗試更新；沒有新資料時，每次執行（約 20 分鐘）再試一次，
# 兩小時內都沒有就放棄，等下一個排程時間。手動執行「更新資料」或上傳檔案時，一律立刻更新全部。
SCHEDULE = {
    "ECMWF": [1, 7, 13, 19],
    "GFS": [5, 11, 17, 23],
    "AIFS": [1, 7, 13, 19],
    "FNV3": [2, 8, 14, 20],
    "WN3C": [2, 8, 14, 20],
}
RETRY_MINUTES = 20
GIVE_UP_HOURS = 2
LOG_HOURS = 48
CACHE = Path(__file__).resolve().parent.parent / ".cache"
LOG_OUT = OUT.parent / "update_log.json"


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _latest_init(items):
    inits = [f["init"] for it in items for f in it.get("forecasts", [])]
    return max(inits) if inits else None


class Scheduler:
    """記錄每個模式上次成功更新的排程時段，並決定這次要不要去抓；所有結果寫進更新紀錄。"""

    def __init__(self):
        from datetime import datetime, timezone
        self.now = datetime.now(timezone.utc)
        self.force = os.environ.get("FORCE_ALL") == "1"
        self.state = _load(CACHE / "model_state.json", {})
        self.log = [e for e in _load(CACHE / "update_log.json", [])
                    if (self.now - datetime.fromisoformat(e["time"].replace("Z", "+00:00"))).total_seconds() < LOG_HOURS * 3600]

    def slot(self, name):
        """目前所屬的排程時段（最近一個已經到達的排程時間）。"""
        from datetime import timedelta
        for back in range(0, 25):
            t = (self.now - timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
            if t.hour in SCHEDULE[name]:
                return t
        return None

    def record(self, name, result, init=None, note=""):
        self.log.append({"time": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), "model": name,
                         "result": result, "init": init, "note": note})
        print(f"  [{name}] {result}" + (f"（起報 {init}）" if init else "") + (f"：{note}" if note else ""))

    def due(self, name, has_cache):
        st = self.state.setdefault(name, {})
        if self.force or not has_cache:
            return True, "手動更新" if self.force else "第一次執行"
        slot = self.slot(name)
        key = slot.strftime("%Y-%m-%dT%HZ")
        if st.get("done_slot") == key:
            return False, None
        if (self.now - slot).total_seconds() > GIVE_UP_HOURS * 3600:
            if st.get("gave_up_slot") != key:
                st["gave_up_slot"] = key
                self.record(name, "放棄", note=f"{slot:%HZ} 排程後兩小時內都沒有新資料，等下一個排程時間")
            return False, None
        return True, f"{slot:%HZ} 排程"

    def finish(self, name, ok):
        if ok:
            self.state.setdefault(name, {})["done_slot"] = self.slot(name).strftime("%Y-%m-%dT%HZ")

    def save(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / "model_state.json").write_text(json.dumps(self.state, ensure_ascii=False))
        (CACHE / "update_log.json").write_text(json.dumps(self.log, ensure_ascii=False))
        LOG_OUT.parent.mkdir(parents=True, exist_ok=True)
        LOG_OUT.write_text(json.dumps({"updated": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), "entries": self.log},
                                      ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def scheduled_items(sched, name, fn):
    """依排程決定要不要重新抓；不抓或沒抓到新資料時，沿用上次存下的結果。"""
    path = CACHE / "sources" / f"{name}.json"
    cached = _load(path, None)
    go, why = sched.due(name, cached is not None)
    if not go:
        print(f"  [{name}] 不在更新時段（或本時段已完成），沿用上次的資料（起報 {cached and cached.get('init')}）")
        return cached["items"] if cached else []
    try:
        items = fn()
    except Exception as e:
        sched.record(name, "失敗", note=f"{why}：{str(e)[:120]}")
        return cached["items"] if cached else []
    init = _latest_init(items)
    old = cached.get("init") if cached else None
    if items and (old is None or (init and init > old) or sched.force):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"init": init, "items": items}, ensure_ascii=False))
        sched.finish(name, init is not None and (old is None or init > old))
        sched.record(name, "更新成功" if (old is None or (init and init > old)) else "已是最新",
                     init=init, note=why)
        return items
    sched.record(name, "尚無新資料", init=old, note=f"{why}，{RETRY_MINUTES} 分鐘後再試")
    return cached["items"] if cached else items


def merge_official(storms):
    """依序抓取各來源。單一來源失敗只會印出訊息，不影響其他來源。"""
    import src_cwa
    import src_ecmwf
    import src_gfs
    import src_jma
    import src_jtwc
    import src_weatherlab
    sched = Scheduler()
    # 第三個值：找不到對應颱風時要不要新增一個。
    # 官方單位（JMA、CWA、JTWC）每次執行都抓；模式依 SCHEDULE 的時間更新。
    # JTWC 放最後：前面的來源會替颱風補上編號，JTWC 才知道要下載哪幾份警報
    tasks = [("JMA", src_jma.fetch, None), ("CWA", src_cwa.fetch, None),
             ("GFS", src_gfs.fetch, "GFS"),
             ("ECMWF", lambda: src_ecmwf.fetch({"ifs"}), "ECMWF"),
             ("AIFS", lambda: src_ecmwf.fetch({"aifs-single", "aifs-ens"}), "AIFS"),
             ("FNV3", lambda: src_weatherlab.fetch("FNV3P2"), "FNV3"),
             ("WN3C", lambda: src_weatherlab.fetch("WNV3"), "WN3C"),
             ("JTWC", lambda: src_jtwc.fetch([s["id"] for s in storms if has_number(s)]), None)]
    for name, fn, sched_name in tasks:
        print("處理", name)
        try:
            items = scheduled_items(sched, sched_name, fn) if sched_name else fn()
            for it in items:
                t = attach(storms, it, True)
                if t:
                    print(f"  → 併入 {t['id']}")
        except Exception as e:
            print(f"  {name} 失敗：", e)
    sched.save()


def demo():
    """產生一個「虛構」颱風，讓網站在還沒接上真實資料時也看得到效果。"""
    t0 = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t0 -= timedelta(hours=t0.hour % 6)
    best = []
    for i in range(6, -1, -1):
        k = 6 - i
        best.append({"time": (t0 - timedelta(hours=6 * i)).isoformat().replace("+00:00", "Z"),
                     "lat": round(17.0 + 0.5 * k, 1), "lon": round(134.0 - 0.9 * k, 1),
                     "vmax": 45 + 10 * k, "mslp": 995 - 8 * k})
    lat0, lon0 = best[-1]["lat"], best[-1]["lon"]
    spread = {"JTWC": (1.0, 1.5), "AVNO": (1.3, 1.2), "ECMF": (0.8, 1.7), "AEMN": (1.15, 1.4), "EEMN": (0.9, 1.6)}
    fc = []
    for tech, (dn, dw) in spread.items():
        label, cat = MODELS[tech]
        pts = []
        for h in range(0, 121, 12):
            s = h / 12
            pts.append({"tau": h,
                        "lat": round(lat0 + dn * s + (0.04 * s * s if tech == "AVNO" else 0), 1),
                        "lon": round(lon0 - dw * s + 0.03 * s * s, 1),
                        "vmax": max(30, int(105 + 5 * s - 3 * s * s)), "mslp": None})
        fc.append({"model": tech, "label": label, "category": cat, "init": best[-1]["time"], "points": pts})
    return [{"id": "WP992026", "basin": "WP", "name": "示範颱風（虛構資料）", "demo": True, "best": best, "forecasts": fc}]


OFFICIAL = {"JTWC", "RJTD", "RCTP", "OFCL"}
ACTIVE_HOURS = 18   # 最新資料超過幾小時就視為已經不活躍


def filter_active(storms):
    """
    只留下目前活躍的熱帶氣旋：
      西北太平洋：JTWC、JMA、CWA 至少一個單位在 18 小時內還有發布預報（停止發布＝已減弱或消散）
      其他海域（目前沒有抓官方預報）：任何來源在 18 小時內還有資料
    如果這次所有官方來源都抓不到（例如網路問題），西北太平洋也改用「任何來源」判斷，避免颱風整批消失。
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    def age(iso):
        h = (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600
        return 9999 if h < -6 else h   # 時間在未來（資料有誤）也當作不活躍
    official_ok = any(f["model"] in OFFICIAL for s in storms for f in s["forecasts"])
    keep = []
    for s in storms:
        off = [f["init"] for f in s["forecasts"] if f["model"] in OFFICIAL]
        anyt = [f["init"] for f in s["forecasts"]] + ([s["best"][-1]["time"]] if s["best"] else [])
        wp = (s.get("basin") or s["id"][:2]).upper() == "WP"
        if wp and official_ok:
            ok = bool(off) and min(map(age, off)) <= ACTIVE_HOURS
        else:
            ok = bool(anyt) and min(map(age, anyt)) <= ACTIVE_HOURS
        (keep.append(s) if ok else print(f"略過已不活躍的 {s['id']}（{s.get('name_en') or '未命名'}）"))
    return keep


def jtwc_number(storm):
    """由颱風代碼算出 JTWC 編號，例如 WP292026 → 29W。"""
    sid = storm["id"]
    if len(sid) < 4 or not sid[2:4].isdigit():
        return None
    basin, num = sid[:2].upper(), int(sid[2:4])
    lon = (storm.get("current") or {}).get("lon", 0)
    suffix = {"WP": "W", "EP": "E", "CP": "C", "AL": "L"}.get(basin)
    if basin == "IO":
        suffix = "A" if lon < 78 else "B"        # 阿拉伯海 A、孟加拉灣 B
    if basin == "SH":
        suffix = "P" if 135 <= lon % 360 <= 240 else "S"   # 南太平洋 P、南印度洋 S
    return f"{num:02d}{suffix}" if suffix else None


def display_name(storm):
    """組成「國際英文名 中文譯名（JTWC 編號）」，例如「Champi 薔琵（29W）」；缺的部分自動省略。"""
    en = storm.get("name_en")
    zh = storm.get("name_zh") or (NAMES_ZH.get(_key(en)) if en else None)
    num = jtwc_number(storm)
    head = " ".join(x for x in (en, zh) if x)
    if not head:
        head = "未命名熱帶氣旋"
    return f"{head}（{num}）" if num else head


def main():
    storms = []
    if "--demo" not in sys.argv:
        known = {s["storm"] for s in SOURCES}
        for src in SOURCES + [d for d in discover_sources() if d["storm"] not in known]:
            print("處理", src["storm"])
            storm = build_storm(src)
            if storm["best"] or storm["forecasts"]:
                storms.append(storm)
        merge_official(storms)
        storms = filter_active(storms)
    if not storms:
        storms = demo()
        print("沒有任何颱風資料，輸出示範資料。")
    for s in storms:
        if not s.get("demo"):
            s["name"] = display_name(s)
        s.pop("current", None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "storms": storms}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已寫入", OUT)
    # 另存一份到快取，讓「更新測站」發布網站時也能帶上最新的路徑資料
    (CACHE / "published").mkdir(parents=True, exist_ok=True)
    shutil.copy(OUT, CACHE / "published" / "tracks.json")
    if LOG_OUT.exists():
        shutil.copy(LOG_OUT, CACHE / "published" / "update_log.json")


if __name__ == "__main__":
    main()
