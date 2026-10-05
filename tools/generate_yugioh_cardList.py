# -*- coding: utf-8 -*-
"""
遊戯王OCG DB から 2000/04/20 から 2004/02/05 までの間に発売されたパック/書籍の収録カードを取得する
必要: pip install requests beautifulsoup4
"""
import json
import re
import time
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.db.yugioh-card.com"
LIST_URL = BASE + "/yugiohdb/card_list.action?request_locale=ja"

# ---- 対象期間の設定 ----
START_DATE = date(2000, 4, 20)
END_DATE = date(2004, 2, 5)

WAIT = 2.0                          # サーバ負荷軽減のための待機秒数
FETCH_CARD_NUMBER = False           # 詳細ページから card_number を取る（遅くなる）

# ---- HTMLセレクタ設定（カード一覧用） ----
SEL = {
    "card_row": "div.t_row",                         # カード一覧の1行
    "card_name": "span.card_name",
    "attr": "span.box_card_attribute span",
    "species": "span.card_info_species_and_other_item",
    "level": "span.box_card_level_rank span",
    "atk": "span.atk_power",
    "def": "span.def_power",
    "effect": "span.box_card_effect span",           # 魔法・罠の種類
    "desc": "dd.box_card_text",
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
})


def get_soup(url):
    time.sleep(WAIT)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def text(el):
    return el.get_text(" ", strip=True) if el else ""


def to_int(s):
    m = re.search(r"-?\d+", s or "")
    return int(m.group()) if m else None


# ---------- 1. パック一覧 ----------
def get_packs():
    soup = get_soup(LIST_URL)
    packs = []
    seen_urls = set()
    
    # クラス名に依存せず、URLに「pid=」を含む input または a タグを直接探す
    for link_el in soup.select("input[value*='pid='], a[href*='pid=']"):
        url_part = link_el.get("value") or link_el.get("href")
        if not url_part or "pid=" not in url_part:
            continue
            
        # 親要素を最大3階層さかのぼり、日付のテキストを探す
        parent = link_el.parent
        date_match = None
        raw_text = ""
        for _ in range(3):
            if not parent:
                break
            raw_text = parent.get_text(" ", strip=True)
            # 2000/04/01 や 2000.04.01 などのフォーマットに対応
            date_match = re.search(r"(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})", raw_text)
            if date_match:
                break
            parent = parent.parent
            
        if not date_match:
            continue
            
        d = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
        
        # --- 期間の判定 ---
        # 2000/04/20 以上 2004/02/05 以下かチェック
        if not (START_DATE <= d <= END_DATE):
            continue
            
        url = urljoin(BASE, url_part)
        
        # 1ページに全件表示するためのパラメータ調整
        if "rp=" in url:
            url = re.sub(r"rp=\d+", "rp=99999", url)
        else:
            url += "&rp=99999"
            
        if url in seen_urls:
            continue
        seen_urls.add(url)
            
        # パック名の抽出 (strongタグがあればそれ、なければ全体から日付を取り除いた文字列)
        name_el = parent.select_one("strong")
        if name_el:
            name = text(name_el)
        else:
            name = re.sub(r"(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})[日]?", "", raw_text).strip()
            
        packs.append({"date": d.isoformat(), "name": name, "url": url})
        
    # 日付の古い順に並び替え
    packs.sort(key=lambda x: x["date"])
    return packs


# ---------- 2. カード種別の判定 ----------
SPELL_SUB = {
    "装備": "equip", 
    "フィールド": "field", 
    "永続": "continuous",
    "速攻": "quickplay", 
    "儀式": "ritual", 
    "カウンター": "counter"
}
MONSTER_SUB = [
    ("融合", "fusion"), 
    ("儀式", "ritual"), 
    ("効果", "effect"),
    ("通常", "normal")
]


def parse_type(attr, species, effect):
    if attr in ("魔法", "罠"):
        t = "spell" if attr == "魔法" else "trap"
        sub = next((v for k, v in SPELL_SUB.items() if k in effect), "normal")
        return t, sub, None, None
        
    inner = species.strip("【】 ")
    parts = [p.strip() for p in inner.split("／")]
    race = parts[0].replace("族", "") if parts else ""
    sub = next((v for k, v in MONSTER_SUB if any(k in p for p in parts[1:])), "normal")
    return "monster", sub, race, attr.replace("属性", "")


# ---------- 3. カード詳細から card_number ----------
def get_card_number(detail_url, pack_name):
    try:
        soup = get_soup(detail_url)
    except Exception:
        return ""
    for row in soup.select("#update_list div.t_row"):
        if pack_name in text(row):
            num_el = row.select_one(".card_number")
            return text(num_el) if num_el else ""
    return ""


# ---------- 4. パック内カード一覧 ----------
def get_cards(pack):
    soup = get_soup(pack["url"])
    cards = []
    for row in soup.select(SEL["card_row"]):
        name = text(row.select_one(SEL["card_name"]))
        if not name:
            continue
            
        attr = text(row.select_one(SEL["attr"]))
        species = text(row.select_one(SEL["species"]))
        effect = text(row.select_one(SEL["effect"]))
        ctype, sub, race, attr_jp = parse_type(attr, species, effect)

        # カードURLの取得 (クラス名に依存せず cid= を含む要素を探す)
        cid_el = row.select_one("input[value*='cid='], a[href*='cid=']")
        detail_url = ""
        if cid_el:
            url_part = cid_el.get("value") or cid_el.get("href")
            detail_url = urljoin(BASE, url_part)

        card = {
            "id": "",
            "name": name,
            "type": ctype,
            "sub": sub,
            "desc": text(row.select_one(SEL["desc"])),
        }
        
        if ctype == "monster":
            card.update({
                "attr": attr_jp,
                "race": race,
                "level": to_int(text(row.select_one(SEL["level"]))),
                "atk": to_int(text(row.select_one(SEL["atk"]))),
                "def": to_int(text(row.select_one(SEL["def"]))),
            })
            
        if FETCH_CARD_NUMBER and detail_url:
            card["id"] = get_card_number(detail_url, pack["name"])
            
        card["pack"] = pack["name"]
        card["release"] = pack["date"]
        cards.append(card)
        
    return cards


def main():
    packs = get_packs()
    print(f"対象パック: {len(packs)} 件")
    all_cards = []
    
    for i, p in enumerate(packs, 1):
        print(f"[{i}/{len(packs)}] {p['date']} {p['name']}")
        cards = get_cards(p)
        print(f"   -> {len(cards)} 枚")
        all_cards.extend(cards)

    # 保存ファイル名を期間に合わせて変更
    filename = "cards_2000-04-20_to_2004-02-05.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_cards, f, ensure_ascii=False, indent=2)
        
    print(f"完了: {len(all_cards)} 件を {filename} に保存しました")


if __name__ == "__main__":
    main()
