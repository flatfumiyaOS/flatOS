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

_TABLES_FOR_EMPTY_CHECK = ("customers", "documents", "memory_notes", "vendors", "customer_contacts")


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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """既存のテーブルに列が無ければ追加する（法人/担当者欄追加のための移行用）。"""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            customer_name TEXT NOT NULL,
            name TEXT NOT NULL,
            kana TEXT,
            honorific TEXT NOT NULL DEFAULT '様',
            title TEXT,
            email TEXT,
            memo TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendors (
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
    # 協力会社側はまだ「法人/個人」＋複数担当者(JSON)の仕組みを使っているため、
    # entity_type/contacts_json列自体はvendors用に残す。顧客側はcontact_fields.py
    # 経由のこの仕組みを使わなくなったが、既存データを壊さないよう列は削除しない。
    _ensure_column(conn, "customers", "entity_type", "entity_type TEXT NOT NULL DEFAULT '個人'")
    _ensure_column(conn, "customers", "contacts_json", "contacts_json TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "vendors", "entity_type", "entity_type TEXT NOT NULL DEFAULT '個人'")
    _ensure_column(conn, "vendors", "contacts_json", "contacts_json TEXT NOT NULL DEFAULT '[]'")
    # 顧客データベースの敬称・郵便番号・FAX・紹介者欄。既存データは敬称=「様」扱いで
    # そのまま使える（これまで登録されていたのはすべて個人のため）。
    _ensure_column(conn, "customers", "honorific", "honorific TEXT NOT NULL DEFAULT '様'")
    _ensure_column(conn, "customers", "postal_code", "postal_code TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "customers", "fax", "fax TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "customers", "referrer", "referrer TEXT NOT NULL DEFAULT ''")
    # 協力会社データベースの敬称・郵便番号・FAX・紹介者・評価欄。
    _ensure_column(conn, "vendors", "honorific", "honorific TEXT NOT NULL DEFAULT '様'")
    _ensure_column(conn, "vendors", "postal_code", "postal_code TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "vendors", "fax", "fax TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "vendors", "referrer", "referrer TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "vendors", "quality_rating", "quality_rating TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "vendors", "service_rating", "service_rating TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "vendors", "communication_rating", "communication_rating TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "vendors", "it_literacy_rating", "it_literacy_rating TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()


def add_customer(
    name, kana, honorific, phone, fax, email, postal_code, address, referrer, memo, entity_type="個人"
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO customers
            (name, kana, honorific, phone, fax, email, postal_code, address, referrer, memo, entity_type, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, kana, honorific, phone, fax, email, postal_code, address, referrer, memo, entity_type, now, now),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def update_customer(
    customer_id, name, kana, honorific, phone, fax, email, postal_code, address, referrer, memo,
    entity_type="個人",
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        UPDATE customers
        SET name = ?, kana = ?, honorific = ?, phone = ?, fax = ?, email = ?,
            postal_code = ?, address = ?, referrer = ?, memo = ?, entity_type = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, kana, honorific, phone, fax, email, postal_code, address, referrer, memo, entity_type, now, customer_id),
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
        WHERE name LIKE ? OR kana LIKE ? OR phone LIKE ? OR fax LIKE ? OR email LIKE ?
           OR postal_code LIKE ? OR address LIKE ? OR referrer LIKE ?
        ORDER BY id DESC
        """,
        (like, like, like, like, like, like, like, like),
    ).fetchall()
    conn.close()
    return rows


def add_customer_contact(customer_id, customer_name, name, kana, honorific, title, email, memo) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO customer_contacts
            (customer_id, customer_name, name, kana, honorific, title, email, memo, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (customer_id, customer_name, name, kana, honorific, title, email, memo, now, now),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def update_customer_contact(contact_id, customer_id, customer_name, name, kana, honorific, title, email, memo) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        UPDATE customer_contacts
        SET customer_id = ?, customer_name = ?, name = ?, kana = ?, honorific = ?,
            title = ?, email = ?, memo = ?, updated_at = ?
        WHERE id = ?
        """,
        (customer_id, customer_name, name, kana, honorific, title, email, memo, now, contact_id),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def delete_customer_contact(contact_id) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM customer_contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def get_all_customer_contacts():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM customer_contacts ORDER BY id DESC").fetchall()
    conn.close()
    return rows


def get_customer_contacts_for_customer(customer_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM customer_contacts WHERE customer_id = ? ORDER BY id ASC",
        (customer_id,),
    ).fetchall()
    conn.close()
    return rows


def search_customer_contacts(keyword: str):
    conn = get_connection()
    like = f"%{keyword}%"
    rows = conn.execute(
        """
        SELECT * FROM customer_contacts
        WHERE name LIKE ? OR kana LIKE ? OR title LIKE ? OR email LIKE ? OR customer_name LIKE ?
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


def add_vendor(
    name, kana, phone, email, address, memo,
    honorific="様", fax="", postal_code="", referrer="",
    quality_rating="", service_rating="", communication_rating="", it_literacy_rating="",
    contacts=None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO vendors
            (name, kana, phone, email, address, memo, honorific, fax, postal_code, referrer,
             quality_rating, service_rating, communication_rating, it_literacy_rating,
             contacts_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name, kana, phone, email, address, memo, honorific, fax, postal_code, referrer,
            quality_rating, service_rating, communication_rating, it_literacy_rating,
            json.dumps(contacts or [], ensure_ascii=False), now, now,
        ),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def update_vendor(
    vendor_id, name, kana, phone, email, address, memo,
    honorific="様", fax="", postal_code="", referrer="",
    quality_rating="", service_rating="", communication_rating="", it_literacy_rating="",
    contacts=None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        """
        UPDATE vendors
        SET name = ?, kana = ?, phone = ?, email = ?, address = ?, memo = ?,
            honorific = ?, fax = ?, postal_code = ?, referrer = ?,
            quality_rating = ?, service_rating = ?, communication_rating = ?, it_literacy_rating = ?,
            contacts_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            name, kana, phone, email, address, memo, honorific, fax, postal_code, referrer,
            quality_rating, service_rating, communication_rating, it_literacy_rating,
            json.dumps(contacts or [], ensure_ascii=False), now, vendor_id,
        ),
    )
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def delete_vendor(vendor_id) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM vendors WHERE id = ?", (vendor_id,))
    conn.commit()
    conn.close()
    _backup_db_to_drive()


def get_all_vendors():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM vendors ORDER BY id DESC").fetchall()
    conn.close()
    return rows


def get_vendor(vendor_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    conn.close()
    return row


def search_vendors(keyword: str):
    conn = get_connection()
    like = f"%{keyword}%"
    rows = conn.execute(
        """
        SELECT * FROM vendors
        WHERE name LIKE ? OR kana LIKE ? OR phone LIKE ? OR fax LIKE ? OR email LIKE ?
           OR postal_code LIKE ? OR address LIKE ? OR referrer LIKE ? OR contacts_json LIKE ?
        ORDER BY id DESC
        """,
        (like, like, like, like, like, like, like, like, like),
    ).fetchall()
    conn.close()
    return rows
