"""Googleスプレッドシート操作の共通部品。

既存のシートの読み書きにはサービスアカウントを使う（あらかじめ編集権限が
共有されているファイルを操作するだけなので、保存容量は使わない）。
一方、新しい見積書スプレッドシートの「作成（テンプレートのコピー）」は
サービスアカウント自身に保存容量が無いため実行できず、ユーザー本人の
Googleアカウント（OAuthログイン）の権限で行う必要がある。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import gspread
import streamlit as st
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from gspread.utils import ValueInputOption

import drive_storage

TEMPLATE_SPREADSHEET_ID = "1-vIOJ7nWTUZi0ChwSSsc6N2j5mq-IX3eHjVHXYd-H-A"

# サービスアカウントの鍵ファイル（JSON）は、ローカル開発ではこのファイルと同じ
# フォルダに「service_account.json」という名前で置く（gitには含めない）。
# Streamlit Community Cloudなどのデプロイ先ではファイルを置けないため、
# その場合は st.secrets["gcp_service_account"] から読み込む。
SERVICE_ACCOUNT_FILE = Path(__file__).resolve().parent / "service_account.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def _get_service_account_info() -> dict:
    """サービスアカウントの鍵情報を返す。

    ローカルの鍵ファイルがあればそちらを優先し、無ければ
    st.secrets["gcp_service_account"]（デプロイ先のSecrets設定）を使う。
    """
    if SERVICE_ACCOUNT_FILE.exists():
        return json.loads(SERVICE_ACCOUNT_FILE.read_text(encoding="utf-8"))
    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:
        pass
    raise FileNotFoundError(
        f"サービスアカウントの鍵情報が見つかりません: "
        f"{SERVICE_ACCOUNT_FILE} も st.secrets['gcp_service_account'] もありません。"
    )


def _get_service_account_credentials() -> ServiceAccountCredentials:
    info = _get_service_account_info()
    return ServiceAccountCredentials.from_service_account_info(info, scopes=SCOPES)


def _get_service_account_email() -> str:
    return _get_service_account_info()["client_email"]


def _get_client() -> gspread.Client:
    return gspread.authorize(_get_service_account_credentials())


def _get_spreadsheet(spreadsheet_id: str) -> gspread.Spreadsheet:
    return _get_client().open_by_key(spreadsheet_id)


def list_sheet_names(spreadsheet_id: str) -> list[str]:
    """スプレッドシート内のシート（タブ）名の一覧を返す。"""
    return [ws.title for ws in _get_spreadsheet(spreadsheet_id).worksheets()]


def rename_worksheet(spreadsheet_id: str, sheet_name: str, new_name: str) -> None:
    """指定したシートの名前を変更する。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    worksheet.update_title(new_name)


def add_worksheet(spreadsheet_id: str, title: str, rows: int, cols: int) -> None:
    """スプレッドシートに、指定した行数・列数の新しい空のシートを追加する。"""
    _get_spreadsheet(spreadsheet_id).add_worksheet(title=title, rows=rows, cols=cols)


def duplicate_worksheet_into(
    source_spreadsheet_id: str,
    source_sheet_name: str,
    destination_spreadsheet_id: str,
    new_sheet_name: str,
) -> None:
    """source_spreadsheet_idのsource_sheet_nameシートを、destination_spreadsheet_id
    （別のスプレッドシート）にシートごと複製し、new_sheet_nameという名前にする。

    定期請求の合算請求書のように、複数の見積書スプレッドシートの内容を
    1つのファイルにシートとしてまとめたい場合に使う。コピー元・コピー先の
    両方に、この関数の呼び出し元（サービスアカウント）の編集権限が必要。
    """
    source_worksheet = _get_spreadsheet(source_spreadsheet_id).worksheet(source_sheet_name)
    result = source_worksheet.copy_to(destination_spreadsheet_id)
    destination_spreadsheet = _get_spreadsheet(destination_spreadsheet_id)
    new_worksheet = destination_spreadsheet.get_worksheet_by_id(result["sheetId"])
    new_worksheet.update_title(new_sheet_name)


def read_cell(spreadsheet_id: str, sheet_name: str, cell: str) -> str | None:
    """指定したシートの指定したセル（例:"A1"）の値を読み取る。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    return worksheet.acell(cell).value


def write_cell(spreadsheet_id: str, sheet_name: str, cell: str, value: str) -> None:
    """指定したシートの指定したセルに値を書き込む。「=SUM(...)」のような数式もそのまま解釈される。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    worksheet.update_acell(cell, value)


def read_range(spreadsheet_id: str, sheet_name: str, range_a1: str) -> list[list[str]]:
    """指定した範囲（例:"A30:F40"）のセルの値をまとめて読み取る。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    return worksheet.get(range_a1)


def write_range(
    spreadsheet_id: str, sheet_name: str, range_a1: str, values: list[list[str]]
) -> None:
    """指定した範囲に複数のセルの値をまとめて書き込む。数式もそのまま解釈される。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    worksheet.update(
        range_name=range_a1, values=values, value_input_option=ValueInputOption.user_entered
    )


def write_cells(spreadsheet_id: str, sheet_name: str, cell_values: dict[str, str]) -> None:
    """複数の飛び飛びのセルに、まとめて1回のAPI呼び出しで値を書き込む。

    セルを1つずつwrite_cellで書き込むと、呼び出し回数が多い場合に一部だけ
    反映されないことがあったため、まとめて書き込めるようにした。
    """
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    data = [{"range": cell, "values": [[value]]} for cell, value in cell_values.items()]
    worksheet.batch_update(data, value_input_option=ValueInputOption.user_entered)


def delete_rows(spreadsheet_id: str, sheet_name: str, start_row: int, end_row: int) -> None:
    """指定した行範囲（1始まり、end_rowを含む）を削除する。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    worksheet.delete_rows(start_row, end_row)


def insert_rows(spreadsheet_id: str, sheet_name: str, row_index: int, num_rows: int = 1) -> None:
    """指定した行位置（1始まり）に空白の行をnum_rows行挿入する。row_index以降の
    既存の行は、その分だけ下にずれる（Googleスプレッドシート側で、ずれた行を
    参照する数式は自動的に調整される）。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    worksheet.insert_rows([[] for _ in range(num_rows)], row_index)


def find_marker_row(
    spreadsheet_id: str, sheet_name: str, marker: str, start_row: int, search_rows: int
) -> int | None:
    """指定したシートのA列を、start_rowからsearch_rows行ぶん上から順に探し、
    marker文字列（例:「【諸経費】」）が最初に完全一致する行番号を返す。
    見つからなければNoneを返す。
    """
    end_row = start_row + search_rows - 1
    values = read_range(spreadsheet_id, sheet_name, f"A{start_row}:A{end_row}")
    for i, row in enumerate(values):
        if row and row[0] == marker:
            return start_row + i
    return None


def compact_gap_above_marker(
    spreadsheet_id: str,
    sheet_name: str,
    marker: str,
    item_start_row: int,
    search_rows: int = 400,
) -> int:
    """item_start_row以降でmarker文字列（例:「【諸経費】」）が現れる行を探し、その
    直前に実際の内容が無い空白行が残っていれば、空白を1行だけ残してまとめて削除する
    （marker行より下は、削除した分だけそのまま上に詰まる）。

    見積書の明細をAIが書き終えた行数がテンプレート作成時の想定より少ないと、明細と
    固定セクション（【諸経費】など）の間に大きな空白ができてしまう問題への対策。
    削除した行数を返す（詰める必要が無ければ0）。
    """
    marker_row = find_marker_row(spreadsheet_id, sheet_name, marker, item_start_row, search_rows)
    if marker_row is None:
        return 0

    values = read_range(spreadsheet_id, sheet_name, f"A{item_start_row}:F{marker_row - 1}")
    last_content_offset = None
    for i, row in enumerate(values):
        if any(cell for cell in row):
            last_content_offset = i
    if last_content_offset is None:
        return 0
    last_content_row = item_start_row + last_content_offset

    # last_content_rowの次の1行は区切りの空白として残し、その次からmarker_rowの
    # 手前までを削除する。
    delete_start = last_content_row + 2
    delete_end = marker_row - 1
    if delete_end < delete_start:
        return 0

    delete_rows(spreadsheet_id, sheet_name, delete_start, delete_end)
    return delete_end - delete_start + 1


def add_estimate_interim_total(
    spreadsheet_id: str,
    detail_sheet_name: str,
    summary_sheet_name: str,
    marker: str,
    item_start_row: int,
    rounding_unit: int,
    search_rows: int = 400,
) -> dict:
    """御見積内訳書の【諸経費】の小計行の直後に「中計」行を挿入し、そこまでの
    各工種・弊社直接施工等・諸経費それぞれの小計を合算した金額を書き込む。
    続く「出精値引き」行は、中計の端数（rounding_unit未満の部分）を切り捨てる
    マイナス金額の数式に書き換える。御見積書側の「出精値引き」（固定49行目・F列）も、
    この値をそのまま参照する数式にして、2つのシートの金額がずれないようにする。

    戻り値: {"interim_row": 中計を書き込んだ行番号, "discount_row": 出精値引きの
    行番号（挿入後）, "subtotal_rows_summed": 合算に使った小計セルの行番号一覧}。
    """
    marker_row = find_marker_row(spreadsheet_id, detail_sheet_name, marker, item_start_row, search_rows)
    if marker_row is None:
        raise ValueError(f"{marker} が見つかりませんでした。")

    # 諸経費自身の小計行（marker_rowより後で、最初にE列が「小計」の行）を探す。
    after_marker = read_range(
        spreadsheet_id, detail_sheet_name, f"A{marker_row + 1}:F{marker_row + search_rows}"
    )
    zei_kei_subtotal_row = None
    for i, row in enumerate(after_marker):
        e = row[4] if len(row) > 4 else ""
        if e == "小計":
            zei_kei_subtotal_row = marker_row + 1 + i
            break
    if zei_kei_subtotal_row is None:
        raise ValueError("諸経費の小計行が見つかりませんでした。")

    # item_start_rowから諸経費の小計行までの間で、E列が「小計」の行
    # （各工種・弊社直接施工等・諸経費それぞれの小計）をすべて集める。
    all_values = read_range(
        spreadsheet_id, detail_sheet_name, f"A{item_start_row}:F{zei_kei_subtotal_row}"
    )
    subtotal_rows = []
    for i, row in enumerate(all_values):
        e = row[4] if len(row) > 4 else ""
        if e == "小計":
            subtotal_rows.append(item_start_row + i)
    if not subtotal_rows:
        raise ValueError("合算対象の小計行が見つかりませんでした。")

    # 諸経費の小計行の直後に1行挿入する（出精値引きなど、それ以降の行は自動的に1行下にずれる）。
    interim_row = zei_kei_subtotal_row + 1
    insert_rows(spreadsheet_id, detail_sheet_name, interim_row, 1)

    interim_formula = "=" + "+".join(f"F{r}" for r in subtotal_rows)
    write_cells(
        spreadsheet_id, detail_sheet_name, {f"E{interim_row}": "中計", f"F{interim_row}": interim_formula}
    )

    discount_row = interim_row + 1
    write_cells(
        spreadsheet_id,
        detail_sheet_name,
        {
            f"E{discount_row}": f"=-MOD(F{interim_row},{rounding_unit})",
            f"F{discount_row}": f"=C{discount_row}*E{discount_row}",
        },
    )

    write_cell(spreadsheet_id, summary_sheet_name, "F49", f"={detail_sheet_name}!E{discount_row}")

    return {
        "interim_row": interim_row,
        "discount_row": discount_row,
        "subtotal_rows_summed": subtotal_rows,
    }


def set_column_width(
    spreadsheet_id: str, sheet_name: str, start_col: int, end_col: int, width_px: int
) -> None:
    """指定した列範囲（1始まり、A=1、end_colを含む）の幅をピクセル単位で設定する。

    セルの値ではなく列そのものの書式（幅）を変更するAPI呼び出しが必要なため、
    read_cell/write_cellとは別に用意している。
    """
    spreadsheet = _get_spreadsheet(spreadsheet_id)
    worksheet = spreadsheet.worksheet(sheet_name)
    spreadsheet.batch_update(
        {
            "requests": [
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": worksheet.id,
                            "dimension": "COLUMNS",
                            "startIndex": start_col - 1,
                            "endIndex": end_col,
                        },
                        "properties": {"pixelSize": width_px},
                        "fields": "pixelSize",
                    }
                }
            ]
        }
    )


def set_row_height(
    spreadsheet_id: str, sheet_name: str, start_row: int, end_row: int, height_px: int
) -> None:
    """指定した行範囲（1始まり、end_rowを含む）の高さをピクセル単位で設定する。"""
    spreadsheet = _get_spreadsheet(spreadsheet_id)
    worksheet = spreadsheet.worksheet(sheet_name)
    spreadsheet.batch_update(
        {
            "requests": [
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": worksheet.id,
                            "dimension": "ROWS",
                            "startIndex": start_row - 1,
                            "endIndex": end_row,
                        },
                        "properties": {"pixelSize": height_px},
                        "fields": "pixelSize",
                    }
                }
            ]
        }
    )


def set_date_format(
    spreadsheet_id: str,
    sheet_name: str,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    pattern: str,
) -> None:
    """指定したセル範囲に日付の表示形式（例: 'm/d'は日付、'ddd'は曜日表示）を設定する。

    セルの値（数式）自体は変更しない。工程表の曜日行（2行目の日付セルを参照する数式）
    のように、値は変えずに表示形式だけ揃えたい場合に使う。
    """
    spreadsheet = _get_spreadsheet(spreadsheet_id)
    worksheet = spreadsheet.worksheet(sheet_name)
    spreadsheet.batch_update(
        {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": start_row - 1,
                            "endRowIndex": end_row,
                            "startColumnIndex": start_col - 1,
                            "endColumnIndex": end_col,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": "DATE", "pattern": pattern}
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            ]
        }
    )


def set_cell_color(
    spreadsheet_id: str,
    sheet_name: str,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    red: float,
    green: float,
    blue: float,
) -> None:
    """指定したセル範囲の背景色を設定する（0〜1の割合でRGBを指定。値・数式は変更しない）。"""
    spreadsheet = _get_spreadsheet(spreadsheet_id)
    worksheet = spreadsheet.worksheet(sheet_name)
    spreadsheet.batch_update(
        {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": start_row - 1,
                            "endRowIndex": end_row,
                            "startColumnIndex": start_col - 1,
                            "endColumnIndex": end_col,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {"red": red, "green": green, "blue": blue}
                            }
                        },
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            ]
        }
    )


def set_border(
    spreadsheet_id: str,
    sheet_name: str,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    red: float = 0.718,
    green: float = 0.718,
    blue: float = 0.718,
) -> None:
    """指定したセル範囲の全セルに、指定色の細い罫線を四辺とも設定する（既定は薄いグレー）。"""
    spreadsheet = _get_spreadsheet(spreadsheet_id)
    worksheet = spreadsheet.worksheet(sheet_name)
    side = {"style": "SOLID", "width": 1, "color": {"red": red, "green": green, "blue": blue}}
    spreadsheet.batch_update(
        {
            "requests": [
                {
                    "updateBorders": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": start_row - 1,
                            "endRowIndex": end_row,
                            "startColumnIndex": start_col - 1,
                            "endColumnIndex": end_col,
                        },
                        "top": side,
                        "bottom": side,
                        "left": side,
                        "right": side,
                        "innerHorizontal": side,
                        "innerVertical": side,
                    }
                }
            ]
        }
    )


def get_column_count(spreadsheet_id: str, sheet_name: str) -> int:
    """指定したシートの列数（グリッドの実際の列数）を返す。"""
    worksheet = _get_spreadsheet(spreadsheet_id).worksheet(sheet_name)
    return worksheet.col_count


def _share_with_service_account(drive_service, file_id: str) -> None:
    """作成したファイルに、サービスアカウントの編集権限を付与する（チャットなどからも読み書きできるように）。"""
    drive_service.permissions().create(
        fileId=file_id,
        body={
            "type": "user",
            "role": "writer",
            "emailAddress": _get_service_account_email(),
        },
        fields="id",
        sendNotificationEmail=False,
    ).execute()


def create_estimate_spreadsheet(project_name: str, user_credentials: UserCredentials) -> str:
    """テンプレートをユーザー本人のGoogleアカウントの権限でコピーし、
    新しい見積書スプレッドシートを作成する。

    ファイル名は「見積書 YYYYMMDD 案件名」の形式にする（作成日の日付）。
    サービスアカウントには保存容量が無いため、コピーの実行はユーザー本人の
    権限（OAuthログイン）で行う。作成後、チャットからも読み書きできるよう、
    サービスアカウントにも編集権限を共有しておく。
    """
    drive_service = build("drive", "v3", credentials=user_credentials)

    date_str = datetime.date.today().strftime("%Y%m%d")
    title = f"見積書 {date_str} {project_name}"
    new_file = (
        drive_service.files()
        .copy(
            fileId=TEMPLATE_SPREADSHEET_ID,
            body={"name": title},
            supportsAllDrives=True,
        )
        .execute()
    )
    new_id = new_file["id"]
    _share_with_service_account(drive_service, new_id)
    return new_id


def copy_spreadsheet(
    source_spreadsheet_id: str, new_name: str, user_credentials: UserCredentials
) -> str:
    """既存の任意のスプレッドシートを、ユーザー本人のGoogleアカウントの権限でコピーする。

    請求書を見積書スプレッドシートから複製する際などに使う汎用版
    （create_estimate_spreadsheetのコピー元をテンプレート固定ではなく指定できるようにしたもの）。
    """
    drive_service = build("drive", "v3", credentials=user_credentials)

    new_file = (
        drive_service.files()
        .copy(
            fileId=source_spreadsheet_id,
            body={"name": new_name},
            supportsAllDrives=True,
        )
        .execute()
    )
    new_id = new_file["id"]
    _share_with_service_account(drive_service, new_id)
    return new_id


def create_schedule_spreadsheet(
    xlsx_path: str, file_name: str, user_credentials: UserCredentials
) -> str:
    """生成済みの工程表xlsxファイルをGoogleドライブにアップロードし、
    Googleスプレッドシートとして変換・保存する。

    見積書と同様、サービスアカウントには保存容量が無いため、アップロードは
    ユーザー本人の権限（OAuthログイン）で行う。作成後、サービスアカウントにも
    編集権限を共有しておく。
    """
    drive_service = build("drive", "v3", credentials=user_credentials)

    media = MediaFileUpload(
        xlsx_path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    new_file = (
        drive_service.files()
        .create(
            body={
                "name": file_name,
                "mimeType": "application/vnd.google-apps.spreadsheet",
            },
            media_body=media,
            supportsAllDrives=True,
        )
        .execute()
    )
    new_id = new_file["id"]
    _share_with_service_account(drive_service, new_id)
    return new_id


SCHEDULE_LOGO_ASSET_PATH = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "skills"
    / "工程表作成"
    / "scripts"
    / "assets"
    / "corp_logo.png"
)


def get_or_upload_schedule_logo_url(user_credentials: UserCredentials) -> str | None:
    """工程表の表紙用ロゴ画像をGoogleドライブにアップロード(未アップロードなら)し、
    Googleスプレッドシートの=IMAGE()関数から参照できる直リンクURLを返す。

    Googleスプレッドシートの「セル内に画像を挿入」機能はAPIから操作できないため、
    代わりにIMAGE()関数でセルに収まる画像を表示する。IMAGE()はURL先の画像を
    取得しにきてしまうため、この画像だけ「リンクを知っている全員が閲覧可」に
    設定している（会社ロゴのため公開範囲上の問題はない）。
    一度アップロードしたファイルは使い回し、毎回アップロードし直さない。
    """
    if not SCHEDULE_LOGO_ASSET_PATH.exists():
        return None
    folder_id = drive_storage.get_folder_path(user_credentials, "schedule_assets")
    filename = "corp_logo.png"
    file_id = drive_storage.find_file_id(user_credentials, folder_id, filename)
    if file_id is None:
        data = SCHEDULE_LOGO_ASSET_PATH.read_bytes()
        file_id = drive_storage.upload_bytes(
            user_credentials, folder_id, filename, data, mime_type="image/png"
        )
        drive_storage.make_public(user_credentials, file_id)
    return f"https://drive.google.com/uc?export=view&id={file_id}"
