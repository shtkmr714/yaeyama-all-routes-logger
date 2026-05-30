"""
yaeyama_dedup.py
yaeyama_operation_log シートの重複行を検出・削除する一回限りのスクリプト。

重複の定義: (date, route_id) の組み合わせが複数行存在する場合
保持方針: recorded_at が "08:15" を含まない行（日次ロガーが書いたもの）を優先。
         両方 "08:15" なら最初の行（行番号が小さい方）を保持。
"""

import os
import json

SHEET_NAME = "yaeyama_operation_log"


def connect_sheets():
    sheets_id = os.environ.get("GOOGLE_SHEETS_ID_YAEYAMA")
    svc_json  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheets_id or not svc_json:
        raise RuntimeError("環境変数 GOOGLE_SHEETS_ID_YAEYAMA / GOOGLE_SERVICE_ACCOUNT_JSON が未設定")

    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        json.loads(svc_json),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheets_id)
    return sh.worksheet(SHEET_NAME)


def main():
    print("=" * 60)
    print("Yaeyama Operation Log — 重複削除")
    print("=" * 60)

    ws = connect_sheets()

    print("\n全データ読み込み中...")
    all_values = ws.get_all_values()
    if not all_values:
        print("シートが空です。終了。")
        return

    header = all_values[0]
    rows   = all_values[1:]   # ヘッダー除く
    total  = len(rows)
    print(f"  ヘッダー行: {header[:4]}")
    print(f"  データ行数: {total:,}")

    # --- 重複検出 ---
    # key = (date, route_id) → list of (sheet_row_index, recorded_at)
    # sheet_row_index は 1-indexed（ヘッダー=1、データ先頭=2）
    from collections import defaultdict
    groups = defaultdict(list)

    date_col    = header.index("date")          if "date"        in header else 0
    recorded_col= header.index("recorded_at")   if "recorded_at" in header else 1
    route_col   = header.index("route_id")      if "route_id"    in header else 2

    for i, row in enumerate(rows):
        if len(row) <= max(date_col, route_col, recorded_col):
            continue
        key = (row[date_col], row[route_col])
        sheet_row = i + 2  # 1-indexed、ヘッダー分+1
        recorded_at = row[recorded_col] if len(row) > recorded_col else ""
        groups[key].append((sheet_row, recorded_at))

    # 重複あるグループを抽出
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"\n重複キー数（date×route）: {len(dup_groups)}")

    if not dup_groups:
        print("重複なし。終了。")
        return

    # --- 削除行を決定 ---
    # 保持: "08:15" を含まない行（日次ロガー）を優先。なければ先頭行。
    rows_to_delete = []
    for key, entries in sorted(dup_groups.items()):
        # 日次ロガー行（recorded_at に 08:15 が含まれないもの）を探す
        keeper = None
        for sheet_row, recorded_at in entries:
            if "08:15" not in recorded_at:
                keeper = sheet_row
                break
        if keeper is None:
            keeper = entries[0][0]  # 全部 08:15 なら先頭保持

        for sheet_row, recorded_at in entries:
            if sheet_row != keeper:
                rows_to_delete.append(sheet_row)

    print(f"削除対象行数: {len(rows_to_delete)}")
    print(f"重複の例 (先頭5件):")
    for key in list(dup_groups.keys())[:5]:
        print(f"  {key}: {dup_groups[key]}")

    if not rows_to_delete:
        print("削除対象なし。終了。")
        return

    # --- 削除実行（後ろから削除しないと行番号ズレる）---
    rows_to_delete_sorted = sorted(rows_to_delete, reverse=True)
    print(f"\n削除開始（{len(rows_to_delete_sorted)}行、後ろから順）...")

    import time
    deleted = 0
    for sheet_row in rows_to_delete_sorted:
        ws.delete_rows(sheet_row)
        deleted += 1
        if deleted % 10 == 0:
            print(f"  {deleted}/{len(rows_to_delete_sorted)} 行削除済み")
            time.sleep(1)  # Sheets API rate limit

    print(f"\n完了。削除: {deleted}行")
    print("=" * 60)


if __name__ == "__main__":
    main()
