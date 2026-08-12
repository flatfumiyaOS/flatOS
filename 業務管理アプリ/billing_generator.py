"""顧客請求書（見積書スプレッドシートを複製して作る）まわりのロジック。

案件の見積書スプレッドシートを複製し、請求書としての体裁に書き換える。
合計金額は見積書スプレッドシートから直接読み取る（AI-OCRは使わない。画像認識の
誤読リスクを避け、見積書に既に入っている数値をそのまま使うため）。
"""

from __future__ import annotations

import calendar
import datetime

from google.oauth2.credentials import Credentials as UserCredentials

import sheets

DETAIL_SHEET = "御見積内訳書"
SUMMARY_SHEET = "御見積書"
DEFAULT_BG_COLOR = (1, 1, 1)


def next_month_last_day(base_date: datetime.date) -> datetime.date:
    """base_dateの翌月末日を返す（支払期限の計算に使う）。"""
    if base_date.month == 12:
        year, month = base_date.year + 1, 1
    else:
        year, month = base_date.year, base_date.month + 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, last_day)


def _format_japanese_date(d: datetime.date) -> str:
    return f"{d.year}年{d.month:02d}月{d.day:02d}日"


def _apply_header_rules(
    spreadsheet_id: str,
    sheet_name: str,
    title_text: str,
    billing_date: datetime.date,
    due_date: datetime.date,
) -> None:
    """見積書ヘッダーの共通ルールを1シート分適用する（御見積書・御見積内訳書で共通）。

    セルを1つずつ書き込むと、呼び出し回数が多い場合に一部だけ反映されないことが
    あったため、値の書き込みはまとめて1回のAPI呼び出しで行う。
    """
    sheets.write_cells(
        spreadsheet_id,
        sheet_name,
        {
            "F5": title_text,
            "A12": "下記のとおり、御請求申し上げます。",
            "A16": "支払期限",
            "B16": _format_japanese_date(due_date),
            "A18": "振込先",
            "B18": "PayPay銀行 すずめ支店 普通 4318804 株式会社フラット",
            "A20": "",
            "B20": "",
            "E10": "請求日：",
            "F10": _format_japanese_date(billing_date),
        },
    )
    sheets.set_cell_color(spreadsheet_id, sheet_name, 20, 20, 1, 1, *DEFAULT_BG_COLOR)


def _clear_notes_section(spreadsheet_id: str, sheet_name: str) -> None:
    """指定したシートの最下部「備考」欄を空にし、振込手数料の注記だけを入れる。

    御見積書・御見積内訳書のどちらも、案件ごとに使う行数（工事項目数）が違うため、
    「備考」の位置は固定行ではなく、都度探して求める。
    """
    values = sheets.read_range(spreadsheet_id, sheet_name, "A1:A400")
    marker_row = None
    for i, row in enumerate(values, start=1):
        if row and row[0] == "備考":
            marker_row = i
            break
    if marker_row is None:
        return

    # 「備考」より下で、実際に文字が入っている行数を数える（最大20行まで確認すれば十分）。
    note_rows = 0
    for row in values[marker_row : marker_row + 20]:
        if row and row[0]:
            note_rows += 1
        else:
            break
    note_rows = max(note_rows, 1)
    end_row = marker_row + note_rows

    sheets.write_range(
        spreadsheet_id,
        sheet_name,
        f"A{marker_row + 1}:A{end_row}",
        [[""] for _ in range(end_row - marker_row)],
    )
    sheets.write_cell(spreadsheet_id, sheet_name, f"A{marker_row + 1}", "＊お振込手数料はご負担願います。")


def create_invoice_from_estimate(
    source_spreadsheet_id: str,
    project_name: str,
    billing_date: datetime.date,
    user_credentials: UserCredentials,
) -> str:
    """案件の見積書スプレッドシートを複製し、請求書としての体裁に書き換える。

    返り値は新しく作成した請求書スプレッドシートのID。
    """
    date_str = billing_date.strftime("%Y%m%d")
    title = f"請求書 {date_str} {project_name}"
    new_id = sheets.copy_spreadsheet(source_spreadsheet_id, title, user_credentials)

    due_date = next_month_last_day(billing_date)

    _apply_header_rules(new_id, SUMMARY_SHEET, "請　求　書", billing_date, due_date)
    _apply_header_rules(new_id, DETAIL_SHEET, "請　求　内　訳　書", billing_date, due_date)
    _clear_notes_section(new_id, SUMMARY_SHEET)
    _clear_notes_section(new_id, DETAIL_SHEET)

    return new_id
