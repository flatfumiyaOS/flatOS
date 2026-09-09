"""顧客請求書（見積書スプレッドシートを複製して作る）まわりのロジック。

案件の見積書スプレッドシートを複製し、請求書としての体裁に書き換える。
合計金額は見積書スプレッドシートから直接読み取る（AI-OCRは使わない。画像認識の
誤読リスクを避け、見積書に既に入っている数値をそのまま使うため）。
"""

from __future__ import annotations

import calendar
import datetime

from google.oauth2.credentials import Credentials as UserCredentials

import billing_store
import recurring_billing_store
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

    「御見積内訳書」シートを持たない、「御見積書」だけの簡易なスプレッドシートにも
    対応する（その場合は御見積書側だけを請求書の体裁にする）。

    返り値は新しく作成した請求書スプレッドシートのID。
    """
    date_str = billing_date.strftime("%Y%m%d")
    title = f"請求書 {date_str} {project_name}"
    new_id = sheets.copy_spreadsheet(source_spreadsheet_id, title, user_credentials)

    due_date = next_month_last_day(billing_date)
    sheet_names = sheets.list_sheet_names(new_id)

    _apply_header_rules(new_id, SUMMARY_SHEET, "請　求　書", billing_date, due_date)
    _clear_notes_section(new_id, SUMMARY_SHEET)
    if DETAIL_SHEET in sheet_names:
        _apply_header_rules(new_id, DETAIL_SHEET, "請　求　内　訳　書", billing_date, due_date)
        _clear_notes_section(new_id, DETAIL_SHEET)

    return new_id


def read_estimate_total(spreadsheet_id: str) -> int | None:
    """見積書スプレッドシート（御見積書）の「合計」金額（税込）を読み取る。見つからなければNone。"""
    try:
        values = sheets.read_range(spreadsheet_id, SUMMARY_SHEET, "E1:F100")
    except Exception:
        return None
    for row in values:
        if row and row[0] == "合計" and len(row) > 1 and row[1]:
            try:
                return int(str(row[1]).replace(",", "").replace("円", "").strip())
            except ValueError:
                return None
    return None


def _create_combined_recurring_invoice(
    items: list[tuple[dict, str, datetime.date]],
    totals: list[int],
    customer_name: str,
    billing_date: datetime.date,
    due_date: datetime.date,
    user_credentials: UserCredentials,
) -> str:
    """同一顧客・同一請求日で複数の定期請求が重なった場合に、1つのスプレッドシートに
    まとめた請求書を作る。

    1件目のベース見積書をコピーしてファイルの土台にし、そのシートを「個別1_...」に
    リネーム。2件目以降は元のベース見積書からシートをコピーして追加する
    （見積書の内容そのものは書き換えず、参照用としてそのまま残す）。最後に、
    金額の内訳と合計を示す「合算請求書」シートを追加する。

    戻り値は作成したスプレッドシートのID。
    """
    date_str = billing_date.strftime("%Y%m%d")
    title = f"請求書（合算） {date_str} {customer_name}"
    first_record = items[0][0]
    new_id = sheets.copy_spreadsheet(first_record["base_spreadsheet_id"], title, user_credentials)

    # 「御見積内訳書」を持たない、「御見積書」だけの簡易なスプレッドシートにも対応する
    # （その場合は御見積書のシートだけをコピー・リネームする）。
    first_sheet_names = sheets.list_sheet_names(new_id)
    sheets.rename_worksheet(new_id, SUMMARY_SHEET, f"個別1_{SUMMARY_SHEET}")
    if DETAIL_SHEET in first_sheet_names:
        sheets.rename_worksheet(new_id, DETAIL_SHEET, f"個別1_{DETAIL_SHEET}")

    for i, (record, _period_key, _due) in enumerate(items[1:], start=2):
        source_sheet_names = sheets.list_sheet_names(record["base_spreadsheet_id"])
        sheets.duplicate_worksheet_into(
            record["base_spreadsheet_id"], SUMMARY_SHEET, new_id, f"個別{i}_{SUMMARY_SHEET}"
        )
        if DETAIL_SHEET in source_sheet_names:
            sheets.duplicate_worksheet_into(
                record["base_spreadsheet_id"], DETAIL_SHEET, new_id, f"個別{i}_{DETAIL_SHEET}"
            )

    combined_sheet_name = "合算請求書"
    sheets.add_worksheet(new_id, combined_sheet_name, rows=10 + len(items), cols=6)

    rows = [
        ["請求書（合算）", "", "", "", "", ""],
        [f"請求日: {_format_japanese_date(billing_date)}", "", "", "", "", ""],
        [f"支払期限: {_format_japanese_date(due_date)}", "", "", "", "", ""],
        [f"宛先: {customer_name} 様", "", "", "", "", ""],
        ["", "", "", "", "", ""],
        ["内訳（詳細は各「個別N」シートを参照）", "", "", "", "", "金額"],
    ]
    for i, total in enumerate(totals, start=1):
        rows.append([f"個別{i}（個別{i}_{SUMMARY_SHEET} 参照）", "", "", "", "", f"¥{total:,}"])
    rows.append(["合計", "", "", "", "", f"¥{sum(totals):,}"])

    sheets.write_range(new_id, combined_sheet_name, f"A1:F{len(rows)}", rows)

    return new_id


def check_and_generate_due_recurring_billings(user_credentials: UserCredentials) -> list[str]:
    """有効な定期請求のうち、まだ生成されていない直近の請求期に達しているものをまとめて
    生成する。同一顧客・同一請求日の定期請求が複数あれば、1つの請求書（合算）にまとめる。

    Streamlitアプリには裏側で定期実行する仕組みが無いため、この関数は「定期請求」ページや
    「会計・原価管理」ページを開いたタイミングで呼び出し、その時点までに到来している
    未生成分をまとめて生成する（多少開くのが遅れても、次に開いたときに追いつく）。

    生成した内容の説明文リストを返す（呼び出し側でst.successするなど通知用に使う）。
    """
    today = datetime.date.today()
    active_records = [
        r
        for r in recurring_billing_store.get_all_recurring_billings()
        if r["status"] == recurring_billing_store.STATUS_ACTIVE
    ]

    due_items: list[tuple[dict, str, datetime.date]] = []
    for record in active_records:
        result = recurring_billing_store.is_due(record, today)
        if result is not None:
            period_key, due_billing_date = result
            due_items.append((record, period_key, due_billing_date))

    groups: dict[int, list[tuple[dict, str, datetime.date]]] = {}
    for item in due_items:
        groups.setdefault(item[0]["customer_id"], []).append(item)

    messages: list[str] = []
    for items in groups.values():
        customer_name = items[0][0]["customer_name"]
        billing_date = max(item[2] for item in items)
        due_date = next_month_last_day(billing_date)

        totals = [read_estimate_total(record["base_spreadsheet_id"]) or 0 for record, _, _ in items]

        if len(items) == 1:
            record = items[0][0]
            amount = totals[0]
            new_id = create_invoice_from_estimate(
                record["base_spreadsheet_id"],
                f"定期請求（{customer_name}）",
                billing_date,
                user_credentials,
            )
            project_label = f"定期請求（{customer_name}）"
        else:
            amount = sum(totals)
            new_id = _create_combined_recurring_invoice(
                items, totals, customer_name, billing_date, due_date, user_credentials
            )
            project_label = f"定期請求 合算（{customer_name}）"

        billing_store.add_billing(
            project_id=None,
            project_name=project_label,
            customer_name=customer_name,
            ratio_percent=None,
            estimate_total=amount,
            amount=amount,
            billing_date=billing_date.isoformat(),
            due_date=due_date.isoformat(),
            spreadsheet_id=new_id,
            recurring_billing_ids=[record["id"] for record, _, _ in items],
        )
        for record, period_key, _ in items:
            recurring_billing_store.set_last_generated_period(record["id"], period_key)

        messages.append(f"「{customer_name}」の定期請求書を作成しました（¥{amount:,}）。")

    return messages
