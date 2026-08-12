"""顧客データベース・書類データ用のSQLiteアクセス関数をまとめたモジュール。"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "data" / "customers.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def delete_customer(customer_id) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    conn.commit()
    conn.close()


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


def add_memory_note(category: str, content: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    conn.execute(
        "INSERT INTO memory_notes (category, content, created_at) VALUES (?, ?, ?)",
        (category, content, now),
    )
    conn.commit()
    conn.close()


def get_memory_notes(category: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM memory_notes WHERE category = ? ORDER BY id ASC",
        (category,),
    ).fetchall()
    conn.close()
    return rows
