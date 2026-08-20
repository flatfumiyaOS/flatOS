"""図面をもとに工程表（ガントチャート）のデータを自動生成するロジック。

体裁・生成処理そのものは「工程表作成」スキル（.claude/skills/工程表作成/scripts/build_schedule.py）を
そのまま読み込んで利用する。ここではその手前の工程（図面をClaudeで解析し、
build_schedule.py が読めるconfig辞書を作る部分）のみを行う。
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path

import streamlit as st

try:
    import anthropic
except ImportError:
    anthropic = None

MODEL_NAME = "claude-sonnet-5"

# 業務管理アプリ/ の一つ上（プロジェクトルート）にある .claude/skills/工程表作成/ を参照する。
SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "工程表作成"
BUILD_SCRIPT_PATH = SKILL_DIR / "scripts" / "build_schedule.py"

SCHEDULE_RULES = """\
工程表（ガントチャート）を作るときは、次のルールを必ず守ってください。

- 工種（行）は、図面・仕様書の内容に合わせて過不足なく選ぶこと。使わない工種は入れない。
  代表的な工種の例: 仮設工事、解体工事、造作工事、電気工事、給排水・ガス設備工事、
  空調工事、内装仕上げ工事、竣工クリーニングなど。
- 同じ工種の中に複数の異なる作業がある場合（例: 造作工事の中に「ボード下地組み」と
  「ボード貼り」がある）は、日付が重なっていなくても1つの行のtasksにまとめず、
  同じlabelを持つ行をrows内で連続して複数並べること（例:
  {"label": "造作工事", "tasks": [...]} を2つ連続で並べる。noiseの値も揃えること）。
  1つの行のtasksに複数の作業をまとめてよいのは、表示上どうしても1行に収めたい
  ごく短い付随作業のみとし、基本は「1作業=1行」を優先する。
- 各工種の行の中で、日付範囲（start_day〜end_day）が重ならないようにすること
  （重ねたい場合はその工種の行を分けて別の行にする）。別の工種（別の行）同士なら
  同じ日程で重なってよい（並行作業）。
- 近隣住民に影響が出る工事（解体の振動・騒音、断水を伴う配管切り回しなど）がある場合は、
  最後に「騒音・近隣配慮工事」という行を追加し、"noise": true を指定すること。
  該当する工事が無ければこの行は入れなくてよい。
- 図面に「要検討」「未定」と書かれている項目は、勝手に含めたり除いたりせず、
  タスクの文字列の末尾に「（要確認）」を付けて含めること。
- start_dayとend_dayは、着工日を1日目とした通し日数（1始まり）。
"""

# Structured outputs（output_config.format）で使うJSONスキーマ。これを指定すると、
# Claudeの応答が必ずこのスキーマに従った構文的に正しいJSONになることがAPI側で保証される
# （正規表現でのJSON抽出やJSON構文エラーによる失敗が起きなくなる）。
SCHEDULE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "A1セルに表示するタイトル"},
        "start_date": {"type": "string"},
        "total_days": {"type": "integer"},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "工種名"},
                    "noise": {"type": "boolean"},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_day": {"type": "integer"},
                                "end_day": {"type": "integer"},
                                "text": {"type": "string"},
                            },
                            "required": ["start_day", "end_day", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["label", "noise", "tasks"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "start_date", "total_days", "rows"],
    "additionalProperties": False,
}


def _load_build_schedule_module():
    """build_schedule.py（工程表作成スキルの生成スクリプト）をそのまま読み込む。"""
    if not BUILD_SCRIPT_PATH.exists():
        raise FileNotFoundError(
            f"工程表作成スキルのスクリプトが見つかりません: {BUILD_SCRIPT_PATH}"
        )
    spec = importlib.util.spec_from_file_location("build_schedule", BUILD_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_analysis_prompt(
    customer_name: str,
    project_name: str,
    start_date: str,
    total_days: int,
) -> str:
    defaults_note = (
        "図面に工期や着工順の詳細が無い工種については、標準的なリフォーム工事の"
        "工期・順序を仮に置いて下書きを作成してください。"
    )
    return (
        "添付した図面・仕様書をもとに、次の案件の工程表（ガントチャート）のデータを"
        "作成してください。\n\n"
        f"- 顧客名: {customer_name}\n"
        f"- 案件名: {project_name}\n"
        f"- 着工日: {start_date}\n"
        f"- 工期の目安: {total_days}日間\n\n"
        f"{SCHEDULE_RULES}\n"
        f"{defaults_note}"
    )


def _split_overlapping_tasks(rows: list[dict]) -> list[dict]:
    """同じ行の中でtasksの日付範囲が重なっている場合、ルールどおり行を分けて解消する。

    Claudeへの指示（同じ行では日付を重ねない）に従わない出力が来た場合の保険。
    重なりを放置するとbuild_schedule.py側でセル結合が衝突してエラーになるため、
    ここで必ず解消してから渡す。
    """
    result: list[dict] = []
    for row in rows:
        lanes: list[list[dict]] = []
        for task in sorted(row.get("tasks", []), key=lambda t: t["start_day"]):
            for lane in lanes:
                if task["start_day"] > lane[-1]["end_day"]:
                    lane.append(task)
                    break
            else:
                lanes.append([task])
        for lane_tasks in lanes:
            result.append(
                {"label": row["label"], "noise": row.get("noise", False), "tasks": lane_tasks}
            )
    return result


def _get_api_key() -> str | None:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


# Anthropic APIには1リクエストあたりのサイズ上限（base64込みで約32MB）があり、
# 数百MBの図面PDFをそのまま送ると413エラー(request_too_large)になる。
# これを超えるPDFは、直接送らずページごとに画像化・圧縮してから送る。
MAX_DIRECT_UPLOAD_BYTES = 20 * 1024 * 1024
PDF_IMAGE_MAX_DIM = 1800
PDF_IMAGE_QUALITY = 70


def _pdf_to_jpeg_blocks(file_bytes: bytes) -> list[dict]:
    """大きすぎるPDFの各ページをJPEG画像に変換し、Claudeに送れるサイズまで圧縮する。"""
    import io as _io

    import fitz  # PyMuPDF
    from PIL import Image

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    blocks = []
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            img = Image.open(_io.BytesIO(pix.tobytes("png")))
            img.thumbnail((PDF_IMAGE_MAX_DIM, PDF_IMAGE_MAX_DIM))
            buf = _io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=PDF_IMAGE_QUALITY)
            img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
                }
            )
    finally:
        doc.close()
    return blocks


def _prepare_file_blocks(file_bytes: bytes, media_type: str) -> list[dict]:
    """図面ファイルをAPIに送るcontentブロックに変換する。大きすぎるPDFは画像化する。"""
    if media_type == "application/pdf" and len(file_bytes) > MAX_DIRECT_UPLOAD_BYTES:
        return _pdf_to_jpeg_blocks(file_bytes)
    data_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    if media_type == "application/pdf":
        return [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": media_type, "data": data_b64},
            }
        ]
    return [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }
    ]


def analyze_drawing_to_config(
    file_bytes: bytes,
    media_type: str,
    customer_name: str,
    project_name: str,
    start_date: str,
    total_days: int,
) -> dict:
    """図面（画像・PDF）を読み込み、build_schedule.py用のconfig（dict）を生成する。"""
    api_key = _get_api_key()
    if anthropic is None or not api_key:
        raise RuntimeError("Anthropic APIキーが設定されていないため、自動生成できません。")

    file_blocks = _prepare_file_blocks(file_bytes, media_type)

    prompt = _build_analysis_prompt(customer_name, project_name, start_date, total_days)
    client = anthropic.Anthropic(api_key=api_key)

    # このモデルは既定で拡張思考(thinking)を使い、そのトークンもmax_tokensの枠を消費する。
    # 図面が複雑だと思考だけで枠を使い切り、肝心の出力が0文字になってしまうことが
    # あるため、十分な余裕を持たせる（max_tokensが大きいとタイムアウト防止のため
    # ストリーミングが必須になる）。それでも思考トークンの消費量は毎回変動するため、
    # 失敗した場合は数回リトライする。
    # output_config（Structured outputs）でJSONスキーマを指定することで、応答が必ず
    # 構文的に正しいJSONになることをAPI側で保証させ、正規表現での抜き出しやJSON構文
    # エラーによる失敗を無くす。
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with client.messages.stream(
                model=MODEL_NAME,
                max_tokens=32000,
                output_config={
                    "format": {"type": "json_schema", "schema": SCHEDULE_JSON_SCHEMA}
                },
                messages=[
                    {
                        "role": "user",
                        "content": [*file_blocks, {"type": "text", "text": prompt}],
                    }
                ],
            ) as stream:
                response = stream.get_final_message()
            text = "".join(block.text for block in response.content if block.type == "text")
            config = json.loads(text)
            break
        except Exception as exc:  # noqa: BLE001 — 失敗時はリトライし、尽きたら最後の例外を送出
            last_error = exc
            config = None
    if config is None:
        raise last_error

    # 着工日・工期はユーザーが確定させた値なので、Claudeの出力によらずそのまま使う。
    config["start_date"] = start_date
    config["total_days"] = total_days
    config["rows"] = _split_overlapping_tasks(config.get("rows", []))
    return config


def build_schedule_xlsx(
    config: dict, out_path: str, embed_logo: bool = True
) -> tuple[str, int, int, int]:
    """「工程表作成」スキルの生成スクリプト（build_schedule.py）をそのまま使ってxlsxを作る。

    戻り値は (出力ファイルパス, 最終列番号, 工種の開始行, 工種の最終行)（いずれも1始まり）。
    embed_logo=Falseにすると、xlsx内にはロゴ画像を埋め込まない
    （Googleスプレッドシート変換後にIMAGE()関数で別途表示する場合に使う）。
    """
    module = _load_build_schedule_module()
    return module.build(config, out_path, embed_logo=embed_logo)
