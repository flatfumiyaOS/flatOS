"""全ページ共通のヘッダー表示。"""

import base64
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

LOGO_PATH = Path(__file__).parent / "assets" / "corp_logo_white.svg"
HOME_SCREEN_ICON_PATH = Path(__file__).parent / "assets" / "app_icon_180.png"
APP_ICON_PATH = Path(__file__).parent / "assets" / "app_icon.png"  # ブラウザタブのfavicon用。st.set_page_config(page_icon=...)に渡す
HEADER_BG_COLOR = "#1a1311"  # ロゴ本来の色を背景に使い、白ロゴが映えるようにする


def show_header() -> None:
    svg_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
    st.markdown(
        f"""
        <div style="background-color:{HEADER_BG_COLOR}; padding: 14px 20px;
                    border-radius: 6px; margin-bottom: 12px;">
            <img src="data:image/svg+xml;base64,{svg_b64}" width="160">
        </div>
        """,
        unsafe_allow_html=True,
    )
    _inject_home_screen_icon()


def _inject_home_screen_icon() -> None:
    """スマホで「ホーム画面に追加」した際のアイコンを、ロゴ（Fマーク）に差し替える。

    StreamlitはページのHTML <head> を直接編集する手段を用意していないため、
    コンポーネント用iframe（親ページと同一オリジン）からJSで親ドキュメントの
    <head> にicon用のlinkタグを追加する。同じタグを何度も追加しないよう、
    追加済みかどうかを見てから実行する。
    """
    icon_b64 = base64.b64encode(HOME_SCREEN_ICON_PATH.read_bytes()).decode()
    icon_data_url = f"data:image/png;base64,{icon_b64}"
    components.html(
        f"""
        <script>
        (function() {{
            const doc = window.parent.document;
            if (doc.querySelector('link[data-flatos-icon]')) {{
                return;
            }}
            const iconUrl = "{icon_data_url}";

            const appleIcon = doc.createElement('link');
            appleIcon.rel = 'apple-touch-icon';
            appleIcon.href = iconUrl;
            appleIcon.setAttribute('data-flatos-icon', '1');
            doc.head.appendChild(appleIcon);

            const shortcutIcon = doc.createElement('link');
            shortcutIcon.rel = 'icon';
            shortcutIcon.href = iconUrl;
            shortcutIcon.setAttribute('data-flatos-icon', '1');
            doc.head.appendChild(shortcutIcon);

            const titleMeta = doc.createElement('meta');
            titleMeta.name = 'apple-mobile-web-app-title';
            titleMeta.content = '業務管理アプリ';
            titleMeta.setAttribute('data-flatos-icon', '1');
            doc.head.appendChild(titleMeta);

            // ホーム画面に追加した際、ブラウザのアドレスバー等を表示せず
            // 単独アプリのような画面（スタンドアロン表示）で起動させるための指定。
            // Safariの「ホーム画面に追加」からのみ有効（iOS版Chromeなど他ブラウザは非対応）。
            const capableMeta = doc.createElement('meta');
            capableMeta.name = 'apple-mobile-web-app-capable';
            capableMeta.content = 'yes';
            capableMeta.setAttribute('data-flatos-icon', '1');
            doc.head.appendChild(capableMeta);

            const statusBarMeta = doc.createElement('meta');
            statusBarMeta.name = 'apple-mobile-web-app-status-bar-style';
            statusBarMeta.content = 'black-translucent';
            statusBarMeta.setAttribute('data-flatos-icon', '1');
            doc.head.appendChild(statusBarMeta);

            const mobileCapableMeta = doc.createElement('meta');
            mobileCapableMeta.name = 'mobile-web-app-capable';
            mobileCapableMeta.content = 'yes';
            mobileCapableMeta.setAttribute('data-flatos-icon', '1');
            doc.head.appendChild(mobileCapableMeta);
        }})();
        </script>
        """,
        height=0,
        width=0,
    )
