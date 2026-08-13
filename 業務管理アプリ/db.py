"""顧客データベース・書類データ用のSQLiteアクセス関数をまとめたモジュール。"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

import streamlit as st

import drive_storage
import google_auth

DB_PATH = Path(__file__).parent / "data" / "customers.db"
DB_DRIVE_FILENAME = "customers.db"

_TABLES_FOR_EMPTY_CHECK = ("customers", "documents", "memory_notes")


def _restore_db_from_drive_if_empty() -> None:
    """customers.dbが（未作成 or 中身が空の）状態で、Googleにログイン済みなら、
    Googleドライブのバックアップから復元を試みる。セッション中に一度だけ試す。

    Streamlit Cloud再起動直後はローカルにデータが無いだけでなく、ログイン前に
    このモジュールの他の関数がsqlite3.connect()するだけで空のdbファイルが
    自動的に作られてしまう。そのため「ファイルが存在しない」ではなく
    「中身が空（＝実質何も無い）」を復元の判定条件にしている。
    """
    if not google_auth.is_logged_in():
        return
    if st.session_state.get("_db_drive_restore_tried"):
        return
    st.session_state["_db_drive_restore_tried"] = True

    try:
        is_empty = True
        if DB_PATH.exists():
            conn = sqlite3.connect(DB_PATH)
            try:
                is_empty = all(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
                    for table in _TABLES_FOR_EMPTY_CHECK
                )
            except sqlite3.OperationalError:
                is_empty = True  # テーブル未作成 = 実質空
            finally:
                conn.close()
        if not is_empty:
            return

        credentials = google_auth.get_credentials()
        folder_id = drive_storage.get_folder_path(credentials, "app_data")
        file_id = drive_storage.find_file_id(credentials, folder_id, DB_DRIVE_FILENAME)
        if file_id is None:
            return
        data = drive_storage.download_bytes(credentials, file_id)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        DB_PATH.write_bytes(data)
    except Exception:
        pass


def _backup_db_to_drive() -> None:
    drive_storage.backup_file(DB_PATH, DB_DRIVE_FILENAME)


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _restore_db_from_drive_if_empty()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kana TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            memo TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            customer_name TEXT,
            subject TEXT NOT NULL,
            doc_date TEXT NOT NULL,
            items_json TEXT NOT NULL,
            total INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def add_customer(name, kana, phone, email, address, memo) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO customers (name, kana, phone, email, address, memo, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, kana, phone, email, address, memo, now, now),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def update_customer(customer_id, name, kana, phone, email, address, memo) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        UPDATE customers
        SET name = ?, kana = ?, phone = ?, email = ?, address = ?, memo = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, kana, phone, email, address, memo, now, customer_id),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def delete_customer(customer_id) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def get_all_customers():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
    conn.close()
    return rows


def get_customer(customer_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    conn.close()
    return row


def search_customers(keyword: str):
    conn = get_connection()
    like = f"%{keyword}%"
    rows = conn.execute(
        """
        SELECT * FROM customers
        WHERE name LIKE ? OR kana LIKE ? OR phone LIKE ? OR email LIKE ? OR address LIKE ?
        ORDER BY id DESC
        """,
        (like, like, like, like, like),
    ).fetchall()
    conn.close()
    return rows


def add_document(customer_id, customer_name, subject, doc_date, items, total) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO documents (customer_id, customer_name, subject, doc_date, items_json, total, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (customer_id, customer_name, subject, doc_date, json.dumps(items, ensure_ascii=False), total, now),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def get_all_documents():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
    conn.close()
    return rows


def get_document(document_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    conn.close()
    return row


def delete_document(document_id) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def add_memory_note(category: str, content: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        "INSERT INTO memory_notes (category, content, created_at) VALUES (?, ?, ?)",
        (category, content, now),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def get_memory_notes(category: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM memory_notes WHERE category = ? ORDER BY id ASC",
        (category,),
    ).fetchall()
    conn.close()
    return rows
