"""郵便番号と住所を相互に調べるための共通部品。

住所→郵便番号にはHeartRails Geo API、郵便番号→住所にはzipcloud API
（いずれも無料・登録不要）を使う。
"""

from __future__ import annotations

import re

import requests

API_URL = "https://geoapi.heartrails.com/api/json"
ZIPCLOUD_API_URL = "https://zipcloud.ibsnet.co.jp/api/search"

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]


def _split_address(address: str) -> tuple[str, str, str] | None:
    """住所を都道府県・市区町村・残り（町名以降）に分割する。分割できなければNone。"""
    prefecture = next((p for p in PREFECTURES if address.startswith(p)), None)
    if prefecture is None:
        return None
    rest = address[len(prefecture):]

    match = re.match(r"^(.+?郡.+?[町村])(.*)$", rest)
    if not match:
        # 政令指定都市（例:「大阪市北区」）は市＋区をまとめて市区町村として扱う
        match = re.match(r"^(.+?市.+?区)(.*)$", rest)
    if not match:
        match = re.match(r"^(.+?[市区町村])(.*)$", rest)
    if not match:
        return None

    city, remainder = match.group(1), match.group(2)
    return prefecture, city, remainder


def lookup_postal_code(address: str) -> str | None:
    """住所から郵便番号（例:"142-0063"）を調べる。見つからない場合はNone。"""
    address = address.strip()
    parsed = _split_address(address)
    if parsed is None:
        return None
    prefecture, city, remainder = parsed

    response = requests.get(
        API_URL,
        params={"method": "getTowns", "prefecture": prefecture, "city": city},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json().get("response", {})
    locations = data.get("location")
    if not locations:
        return None

    remainder = remainder.strip()
    matched = [loc for loc in locations if remainder.startswith(loc["town"])]
    if not matched:
        return None
    # 町名がより長く一致するものを優先する（例:「西五反田」が「五反田」より優先されるように）
    best = max(matched, key=lambda loc: len(loc["town"]))
    postal = best["postal"]
    return f"{postal[:3]}-{postal[3:]}"


def lookup_address_from_postal_code(postal_code: str) -> str | None:
    """郵便番号（ハイフンあり・なしどちらでも可）から住所（都道府県+市区町村+町域）を調べる。

    見つからない場合、または郵便番号の形式が不正な場合はNoneを返す。
    """
    digits = re.sub(r"\D", "", postal_code)
    if len(digits) != 7:
        return None

    response = requests.get(ZIPCLOUD_API_URL, params={"zipcode": digits}, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != 200:
        return None
    results = data.get("results")
    if not results:
        return None

    best = results[0]
    return f"{best['address1']}{best['address2']}{best['address3']}"
