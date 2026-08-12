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

    data_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    if media_type == "application/pdf":
        file_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }
    else:
        file_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }

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
                    {"role": "user", "content": [file_block, {"type": "text", "text": prompt}]}
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


def build_schedule_xlsx(config: dict, out_path: str) -> str:
    """「工程表作成」スキルの生成スクリプト（build_schedule.py）をそのまま使ってxlsxを作る。"""
    module = _load_build_schedule_module()
    return module.build(config, out_path)
