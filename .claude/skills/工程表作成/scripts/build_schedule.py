"""
工程表(xlsx)生成スクリプト。

「【例】工程表.xlsx」のスタイル(パステルカラー、列幅、日付/曜日フォーマット、
結合セルでの複数日表示など)を踏襲したガントチャート形式の工程表を作る。

使い方:
    python3 build_schedule.py <config.json> <output.xlsx>

config.json のスキーマ:
{
  "title": "大蔵様邸　改修工事",
  "start_date": "2026-07-13",   # 着工日 (YYYY-MM-DD)
  "total_days": 21,              # 表に含める日数(工期の目安から決める)
  "sheet_name": "工程表",         # 省略可
  "rows": [
    {
      "label": "仮設工事",        # A列に入る工種名
      "noise": false,             # true にすると警告色(赤)で塗る。近隣配慮工事の行で使う
      "tasks": [
        {"start_day": 1, "end_day": 2, "text": "養生"}
        # start_day/end_day は着工日を1日目とした通し日数。1日だけなら start_day==end_day
      ]
    }
  ]
}

同じ行の tasks は日数が重ならないようにすること(同じ列に2つの内容は入れられない)。
別の行同士なら重なってよい(工事の並行作業)。
"""
import sys
import json
import datetime
import jpholiday
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

# 「【例】工程表.xlsx」から抽出したGoogleスプレッドシート標準パステルカラー
COLORS = ['FFF4CCCC', 'FFFFF2CC', 'FFD9EAD3', 'FFD9D2E9', 'FFC9DAF8', 'FFD9D9D9', 'FFEAD1DC']
NOISE_COLOR = 'FFE06666'

BASE_FONT = Font(name='Arial', size=10)
TITLE_FONT = Font(name='Arial', size=14, bold=True)
LABEL_FONT = Font(name='Arial', size=10, bold=True)
TASK_FONT = Font(name='Arial', size=8)
DATE_FONT = Font(name='Arial', size=9)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
LEFT_TITLE = Alignment(horizontal='left', vertical='center')


WEEKEND_HOLIDAY_FILL = PatternFill(fill_type='solid', fgColor='FFF4CCCC')  # 薄い赤


def _is_working_day(d: datetime.date) -> bool:
    return d.weekday() < 5 and not jpholiday.is_holiday(d)


def _build_calendar(start: datetime.date, n_working_days: int):
    """startから、実働日数がn_working_days分になるまでの連続した暦日リストを作る。

    土日・祝日も歯抜けにせず列として並べる（作業内容の予定を入れないだけ）。
    「着工日を1日目とした通し日数」で組まれたタスクの日数(start_day/end_day)は
    実働日の通し番号を指すため、実働日の通し番号→暦日リスト内のインデックスの
    対応表も合わせて返す。startそのものが土日・祝日の場合は、その次の実働日から
    1日目として数える（工期は常に実働日数として扱うため）。
    """
    calendar_dates = []
    working_index_to_calendar_index = {}
    working_count = 0
    d = start
    while working_count < n_working_days:
        calendar_dates.append(d)
        if _is_working_day(d):
            working_count += 1
            working_index_to_calendar_index[working_count] = len(calendar_dates) - 1
        d += datetime.timedelta(days=1)
    return calendar_dates, working_index_to_calendar_index


def build(config: dict, out_path: str):
    start = datetime.date.fromisoformat(config['start_date'])
    n_working_days = int(config['total_days'])
    dates, working_col = _build_calendar(start, n_working_days)
    n_cols = len(dates)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = config.get('sheet_name', '工程表')

    # 列幅: A=工種名(広め), B=着工日(狭め,例に合わせる), C以降=標準幅
    ws.column_dimensions['A'].width = 32
    ws.column_dimensions['B'].width = 4.25
    for i in range(3, n_cols + 2):
        ws.column_dimensions[get_column_letter(i)].width = 12.63

    first_col = 2  # B列が1日目

    # Row1: タイトル + 月またぎで見出しを分割
    ws['A1'] = config['title']
    ws['A1'].font = TITLE_FONT
    ws.row_dimensions[1].height = 30.75

    month_ranges = []
    seg_start = 0
    for i in range(1, n_cols + 1):
        if i == n_cols or dates[i].month != dates[seg_start].month:
            month_ranges.append((seg_start, i - 1))
            seg_start = i
    for s, e in month_ranges:
        c1 = get_column_letter(first_col + s)
        c2 = get_column_letter(first_col + e)
        d = dates[s]
        ws.merge_cells(f'{c1}1:{c2}1')
        cell = ws[f'{c1}1']
        cell.value = f'{d.year}年{d.month}月'
        cell.font = BASE_FONT
        cell.alignment = LEFT_TITLE

    # Row2: 日付 (m/d), Row3: 曜日 (Row2を参照する数式、ddd表示)
    ws['A3'] = '工程名'
    ws['A3'].font = LABEL_FONT
    last_row = 3 + len(config['rows'])
    for i, d in enumerate(dates):
        col = get_column_letter(first_col + i)
        c2 = ws[f'{col}2']
        c2.value = d
        c2.number_format = 'm/d'
        c2.font = DATE_FONT
        c2.alignment = CENTER
        c3 = ws[f'{col}3']
        c3.value = f'={col}2'
        c3.number_format = 'ddd'
        c3.font = DATE_FONT
        c3.alignment = CENTER
        if not _is_working_day(d):
            # 土日・祝日は列ごと薄い赤で塗って、休みだと分かるようにする
            # （後で工種の行を塗るときに、実際に予定が入っている日は上書きされる）。
            for r in range(2, last_row + 1):
                ws.cell(row=r, column=first_col + i).fill = WEEKEND_HOLIDAY_FILL
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 18

    # Row4以降: 工種ごとの行
    start_row = 4
    for idx, row in enumerate(config['rows']):
        r = start_row + idx
        ws.row_dimensions[r].height = 30
        a = ws[f'A{r}']
        a.value = row['label']
        a.font = LABEL_FONT
        a.alignment = Alignment(horizontal='left', vertical='center')

        is_noise = bool(row.get('noise'))
        color = NOISE_COLOR if is_noise else COLORS[idx % len(COLORS)]
        fill = PatternFill(fill_type='solid', fgColor=color)
        task_font = Font(name='Arial', size=8, color='FFFFFF') if is_noise else TASK_FONT

        for task in row['tasks']:
            d1, d2, text = task['start_day'], task['end_day'], task['text']
            i1 = working_col[d1]
            i2 = working_col[d2]
            c1 = get_column_letter(first_col + i1)
            c2 = get_column_letter(first_col + i2)
            if i1 != i2:
                ws.merge_cells(f'{c1}{r}:{c2}{r}')
            cell = ws[f'{c1}{r}']
            cell.value = text
            cell.font = task_font
            cell.alignment = CENTER
            for col_idx in range(first_col + i1, first_col + i2 + 1):
                ws.cell(row=r, column=col_idx).fill = fill

    ws.freeze_panes = 'B4'
    wb.save(out_path)
    return out_path


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('usage: python3 build_schedule.py <config.json> <output.xlsx>')
        sys.exit(1)
    with open(sys.argv[1], encoding='utf-8') as f:
        cfg = json.load(f)
    path = build(cfg, sys.argv[2])
    print('saved:', path)
