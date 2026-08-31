"""案件管理データのローカルJSON保存用モジュール。

案件ごとの基本情報・資料・工程表・写真・見積書スプレッドシートIDを、
data/projects.json（一覧）とdata/project_files/<案件ID>/（アップロードファイル本体）に保存する。
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

import billing_store
import cost_store
import drive_storage
import google_auth

DATA_DIR = Path(__file__).parent / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"
PROJECT_FILES_DIR = DATA_DIR / "project_files"

OFFICE_OPTIONS = ["長野オフィス", "東京オフィス"]
STAFF_OPTIONS = ["平居靖弘", "平居史也"]
PAYMENT_TERMS_OPTIONS = ["工事完了後10日以内", "ご契約時50%工事完了後50%", "月末締翌月末", "その他"]
ORDER_STATUS_OPTIONS = ["見積中", "受注確定", "受注済"]
BILLING_TIMING_OPTIONS = ["一括請求", "定期請求"]
CATEGORY1_OPTIONS = ["営繕", "リフォーム", "リノベーション", "店舗新装工事", "その他"]
CATEGORY2_OPTIONS = ["元請", "下請"]
CATEGORY3_OPTIONS = ["内装仕上げ工事", "その他建築工事", "その他"]
BILLING_STATUS_UNBILLED = "未請求"
BILLING_STATUS_BILLED = "請求済"
BILLING_STATUS_OPTIONS = [BILLING_STATUS_UNBILLED, BILLING_STATUS_BILLED]


def is_revenue_recognized(project: dict) -> bool:
    """受注ステータス＝受注済 かつ 請求ステータス＝請求済のとき、社内的に売上として扱う。

    freee/boardのような外部会計ソフトとの連携は行わず、アプリ内の判定のみで完結させる。
    """
    return project.get("order_status") == "受注済" and project.get("billing_status") == BILLING_STATUS_BILLED


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    drive_storage.restore_if_missing(PROJECTS_FILE, "projects.json")
    if not PROJECTS_FILE.exists():
        return []
    return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))


def _save_all(projects: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    drive_storage.backup_file(PROJECTS_FILE, "projects.json")


def get_all_projects() -> list[dict]:
    return _load_all()


def get_project(project_id: int) -> dict | None:
    return next((p for p in _load_all() if p["id"] == project_id), None)


def create_project(name: str) -> dict:
    projects = _load_all()
    new_id = max((p["id"] for p in projects), default=0) + 1
    now = _now()
    project = {
        "id": new_id,
        "name": name,
        "customer_name": "",
        "address": "",
        "start_date": "",
        "end_date": "",
        "overview": "",
        # 案件管理の新規登録フォームを経由せずに作成される場合（見積書ページからの
        # 簡易新規作成など）もあるため、支社は空文字列（未設定）をデフォルトにする。
        # "長野オフィス"をデフォルトにすると、案件管理を経由していない案件の見積書まで
        # 誤って長野の住所で上書きしてしまうため。
        "office": "",
        "staff": "",
        "payment_terms": "",
        "order_status": ORDER_STATUS_OPTIONS[0],
        "billing_timing": "",
        "billing_due_date": "",
        "category1": "",
        "category2": "",
        "category3": "",
        "billing_status": BILLING_STATUS_UNBILLED,
        "documents": [],
        "photos": [],
        "cover_photo": None,
        "spreadsheet_id": None,
        "schedule_spreadsheet_id": None,
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }
    projects.append(project)
    _save_all(projects)
    return project


def archive_project(project_id: int) -> None:
    """案件を非表示にする（データは削除せず、一覧・会計画面などから除外する）。"""
    _update_project(project_id, archived=True)


def unarchive_project(project_id: int) -> None:
    """非表示にした案件を、再び表示に戻す。"""
    _update_project(project_id, archived=False)


def _trash_record_file(record: dict, user_credentials) -> None:
    """写真・資料1件分のローカルファイルを削除し、可能ならGoogleドライブ側もゴミ箱に移動する。"""
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


def delete_project(project_id: int, user_credentials=None) -> None:
    """案件を完全に削除する。

    CLAUDE.mdの方針上、ユーザー本人の明示的な許可を得たうえでのみ呼び出すこと。
    案件本体（projects.json）に加えて、紐づく原価データ・顧客請求データも削除する。
    写真・資料のローカルファイルも削除し、user_credentials（ログイン中のGoogle
    アカウント）が渡されていれば、Googleドライブ上の写真・資料・見積書/工程表
    スプレッドシートも合わせてゴミ箱に移動する（完全削除ではなく復元可能な状態にする）。
    user_credentialsが無い場合、Googleドライブ上のファイルはそのまま残る
    （ローカルのデータだけが削除される）。
    """
    projects = _load_all()
    project = next((p for p in projects if p["id"] == project_id), None)
    if project is None:
        return

    for record in (project.get("photos") or []) + (project.get("documents") or []):
        _trash_record_file(record, user_credentials)
    if project.get("cover_photo"):
        _trash_record_file(project["cover_photo"], user_credentials)

    if user_credentials is not None:
        for field in ("spreadsheet_id", "schedule_spreadsheet_id"):
            file_id = project.get(field)
            if file_id:
                try:
                    drive_storage.trash_file(user_credentials, file_id)
                except Exception:
                    pass

    cost_store.delete_costs_for_project(project_id)
    billing_store.delete_billings_for_project(project_id)

    remaining = [p for p in projects if p["id"] != project_id]
    _save_all(remaining)


def get_or_create_project(name: str) -> dict:
    """案件名が一致する既存の案件を返す。無ければ新規に作成して返す。

    見積書ページのように、案件管理を経由せずに見積書を作成できる画面から
    呼び出し、その場で案件管理に案件として登録・紐付けするために使う。
    """
    existing = next((p for p in _load_all() if p["name"] == name), None)
    if existing is not None:
        return existing
    return create_project(name)


def _update_project(project_id: int, **fields) -> None:
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p.update(fields)
            p["updated_at"] = _now()
            break
    _save_all(projects)


def update_basic_info(
    project_id: int,
    customer_name: str,
    address: str,
    start_date: str,
    end_date: str,
    overview: str,
) -> None:
    _update_project(
        project_id,
        customer_name=customer_name,
        address=address,
        start_date=start_date,
        end_date=end_date,
        overview=overview,
    )


def update_case_details(
    project_id: int,
    office: str,
    staff: str,
    payment_terms: str,
    order_status: str,
    billing_timing: str,
    billing_due_date: str,
    category1: str,
    category2: str,
    category3: str,
    billing_status: str,
) -> None:
    _update_project(
        project_id,
        office=office,
        staff=staff,
        payment_terms=payment_terms,
        order_status=order_status,
        billing_timing=billing_timing,
        billing_due_date=billing_due_date,
        category1=category1,
        category2=category2,
        category3=category3,
        billing_status=billing_status,
    )


def set_spreadsheet_id(project_id: int, spreadsheet_id: str) -> None:
    _update_project(project_id, spreadsheet_id=spreadsheet_id)


def set_schedule_spreadsheet_id(project_id: int, spreadsheet_id: str) -> None:
    _update_project(project_id, schedule_spreadsheet_id=spreadsheet_id)


def _save_file(
    project_id: int, subdir: str, filename: str, file_bytes: bytes
) -> tuple[str, str | None]:
    folder = PROJECT_FILES_DIR / str(project_id) / subdir
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
            drive_folder_id = drive_storage.get_folder_path(
                credentials, subdir, str(project_id)
            )
            drive_file_id = drive_storage.upload_bytes(
                credentials, drive_folder_id, target.name, file_bytes
            )
        except Exception:
            drive_file_id = None
    return str(target), drive_file_id


def get_file_bytes(record: dict) -> bytes | None:
    """写真・資料のバイト列を返す。ローカルにキャッシュがあればそこから、
    無ければ（サーバー再起動などで消えていれば）Googleドライブから復元して返す。
    ドライブにも無い、あるいは未ログインの場合はNoneを返す。
    """
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


def get_photo_display_bytes(record: dict) -> bytes | None:
    """写真を画面に表示するためのバイト列を返す。

    スマートフォンで撮影した写真は、画素データ自体は横向きのまま、EXIFの回転情報
    だけで正しい向きを表現していることが多い。st.image()はこの回転情報を見ないため、
    そのまま渡すと向きがおかしく表示される。ここで回転情報を画素データに焼き込み
    直してから返す（保存済みのファイル自体は元のまま変更しない）。
    """
    data = get_file_bytes(record)
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


def add_document(project_id: int, filename: str, file_bytes: bytes) -> None:
    path, drive_file_id = _save_file(project_id, "documents", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["documents"].append(
                {
                    "filename": Path(path).name,
                    "path": path,
                    "drive_file_id": drive_file_id,
                    "uploaded_at": _now(),
                }
            )
            p["updated_at"] = _now()
            break
    _save_all(projects)


def add_photo(project_id: int, filename: str, file_bytes: bytes, phase: str) -> None:
    path, drive_file_id = _save_file(project_id, "photos", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["photos"].append(
                {
                    "filename": Path(path).name,
                    "path": path,
                    "drive_file_id": drive_file_id,
                    "phase": phase,
                    "uploaded_at": _now(),
                }
            )
            p["updated_at"] = _now()
            break
    _save_all(projects)


def set_cover_photo(project_id: int, filename: str, file_bytes: bytes) -> None:
    """案件一覧カードの表紙に使う「現場建物写真」を保存する（1案件につき1枚、上書き）。"""
    path, drive_file_id = _save_file(project_id, "cover", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["cover_photo"] = {
                "filename": Path(path).name,
                "path": path,
                "drive_file_id": drive_file_id,
                "uploaded_at": _now(),
            }
            p["updated_at"] = _now()
            break
    _save_all(projects)
