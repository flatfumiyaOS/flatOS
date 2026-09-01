"""物件データベースのローカルJSON保存用モジュール。

顧客に紐づく物件（マンション・戸建てなど）の情報を、data/properties.json に保存する。
project_store.py と同じ構成（_load_all/_save_all、連番ID）を踏襲する。
外観画像は1件につき1枚、data/property_files/<物件ID>/cover/ にローカル保存し、
ログイン中ならGoogleドライブにもバックアップする（project_store.pyの
現場建物写真（表紙）と同じ仕組み）。
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

import drive_storage
import google_auth

DATA_DIR = Path(__file__).parent / "data"
PROPERTIES_FILE = DATA_DIR / "properties.json"
PROPERTY_FILES_DIR = DATA_DIR / "property_files"

PROPERTY_TYPE_OPTIONS = ["マンション", "戸建", "オフィス", "店舗", "アパート", "その他"]
ADDRESS_TYPE_SAME_AS_CUSTOMER = "顧客情報の住所と同じ"
ADDRESS_TYPE_NEW = "新しい住所を入力"
ADDRESS_TYPE_OPTIONS = [ADDRESS_TYPE_SAME_AS_CUSTOMER, ADDRESS_TYPE_NEW]
OFFICE_OPTIONS = ["長野オフィス", "東京オフィス"]
STAFF_OPTIONS = ["平居靖弘", "平居史也"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    drive_storage.restore_if_missing(PROPERTIES_FILE, "properties.json")
    if not PROPERTIES_FILE.exists():
        return []
    return json.loads(PROPERTIES_FILE.read_text(encoding="utf-8"))


def _save_all(properties: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROPERTIES_FILE.write_text(
        json.dumps(properties, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    drive_storage.backup_file(PROPERTIES_FILE, "properties.json")


def get_all_properties() -> list[dict]:
    return _load_all()


def get_property(property_id: int) -> dict | None:
    return next((p for p in _load_all() if p["id"] == property_id), None)


def get_properties_for_customer(customer_id: int) -> list[dict]:
    return [p for p in _load_all() if p["customer_id"] == customer_id]


def add_property(
    customer_id: int, customer_name: str, name: str, kana: str,
    property_type: str, address_type: str, address: str, office: str, staff: str, memo: str,
) -> dict:
    properties = _load_all()
    new_id = max((p["id"] for p in properties), default=0) + 1
    now = _now()
    prop = {
        "id": new_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "name": name,
        "kana": kana,
        "property_type": property_type,
        "address_type": address_type,
        "address": address,
        "office": office,
        "staff": staff,
        "memo": memo,
        "image": None,
        "created_at": now,
        "updated_at": now,
    }
    properties.append(prop)
    _save_all(properties)
    return prop


def update_property(
    property_id: int, customer_id: int, customer_name: str, name: str, kana: str,
    property_type: str, address_type: str, address: str, office: str, staff: str, memo: str,
) -> None:
    properties = _load_all()
    for p in properties:
        if p["id"] == property_id:
            p.update(
                customer_id=customer_id,
                customer_name=customer_name,
                name=name,
                kana=kana,
                property_type=property_type,
                address_type=address_type,
                address=address,
                office=office,
                staff=staff,
                memo=memo,
                updated_at=_now(),
            )
            break
    _save_all(properties)


def _trash_image_file(record: dict, user_credentials) -> None:
    path = Path(record.get("path", ""))
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    drive_file_id = record.get("drive_file_id")
    if drive_file_id and user_credentials is not None:
        try:
            drive_storage.trash_file(user_credentials, drive_file_id)
        except Exception:
            pass


def delete_property(property_id: int, user_credentials=None) -> None:
    """物件を削除する。外観画像もローカルファイルを削除し、Googleドライブ上の
    ファイルはuser_credentialsが渡されていればゴミ箱に移動する（復元可能な状態にする）。
    """
    properties = _load_all()
    prop = next((p for p in properties if p["id"] == property_id), None)
    if prop is None:
        return
    if prop.get("image"):
        _trash_image_file(prop["image"], user_credentials)
    remaining = [p for p in properties if p["id"] != property_id]
    _save_all(remaining)


def _save_file(property_id: int, filename: str, file_bytes: bytes) -> tuple[str, str | None]:
    folder = PROPERTY_FILES_DIR / str(property_id) / "cover"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    if target.exists():
        stem, suffix = target.stem, target.suffix
        target = folder / f"{stem}_{datetime.now().strftime('%H%M%S%f')}{suffix}"
    target.write_bytes(file_bytes)

    drive_file_id = None
    if google_auth.is_logged_in():
        try:
            credentials = google_auth.get_credentials()
            drive_folder_id = drive_storage.get_folder_path(credentials, "property_cover", str(property_id))
            drive_file_id = drive_storage.upload_bytes(credentials, drive_folder_id, target.name, file_bytes)
        except Exception:
            drive_file_id = None
    return str(target), drive_file_id


def set_property_image(property_id: int, filename: str, file_bytes: bytes) -> None:
    """物件の外観画像を保存する（1物件につき1枚、上書き）。"""
    path, drive_file_id = _save_file(property_id, filename, file_bytes)
    properties = _load_all()
    for p in properties:
        if p["id"] == property_id:
            p["image"] = {
                "filename": Path(path).name,
                "path": path,
                "drive_file_id": drive_file_id,
                "uploaded_at": _now(),
            }
            p["updated_at"] = _now()
            break
    _save_all(properties)


def _get_image_bytes(record: dict) -> bytes | None:
    path = Path(record.get("path", ""))
    if path.exists():
        return path.read_bytes()

    drive_file_id = record.get("drive_file_id")
    if not drive_file_id or not google_auth.is_logged_in():
        return None
    try:
        data = drive_storage.download_bytes(google_auth.get_credentials(), drive_file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data
    except Exception:
        return None


def get_property_image_bytes(property_id: int) -> bytes | None:
    """物件の外観画像を、画面表示用のバイト列として返す（EXIFの回転情報を補正済み）。
    ローカルにキャッシュが無ければGoogleドライブから復元を試みる。無ければNone。
    """
    prop = get_property(property_id)
    if prop is None or not prop.get("image"):
        return None
    data = _get_image_bytes(prop["image"])
    if data is None:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        original_format = image.format or "JPEG"
        corrected = ImageOps.exif_transpose(image)
        if original_format == "JPEG" and corrected.mode not in ("RGB", "L"):
            corrected = corrected.convert("RGB")
        buffer = io.BytesIO()
        corrected.save(buffer, format=original_format)
        return buffer.getvalue()
    except Exception:
        return data
