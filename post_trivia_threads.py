#!/usr/bin/env python3
"""
Nordic Trivia Bot for Threads
北欧4カ国のトリビアをThreads APIで自動投稿する。1日2回（朝・夜）実行。
"""

import os
import json
import time
import random
import requests
import anthropic
from datetime import datetime, timezone
from pathlib import Path

TRIVIA_FILE       = Path("trivia.json")
USED_IDS_FILE      = Path("used_trivia_ids_threads.json")
THREADS_API_BASE   = "https://graph.threads.net/v1.0"

COUNTRIES = ["🇳🇴 ノルウェー", "🇸🇪 スウェーデン", "🇫🇮 フィンランド", "🇩🇰 デンマーク"]

COUNTRY_HASHTAGS = {
    "ノルウェー": "#ノルウェー #Norway",
    "スウェーデン": "#スウェーデン #Sweden",
    "フィンランド": "#フィンランド #Finland",
    "デンマーク": "#デンマーク #Denmark",
}


def load_used_ids() -> set:
    if USED_IDS_FILE.exists():
        with open(USED_IDS_FILE, "r") as f:
            return set(json.load(f).get("ids", []))
    return set()


def save_used_ids(ids: set):
    with open(USED_IDS_FILE, "w") as f:
        json.dump({"ids": list(ids)}, f, ensure_ascii=False, indent=2)


def load_trivia_db() -> list:
    if TRIVIA_FILE.exists():
        with open(TRIVIA_FILE, "r") as f:
            return json.load(f).get("trivia", [])
    return []


def pick_from_db(used_ids: set) -> dict | None:
    db = load_trivia_db()
    unused = [t for i, t in enumerate(db) if str(i) not in used_ids]
    if not unused:
        return None
    item = random.choice(unused)
    idx = db.index(item)
    return {"id": str(idx), **item}


def generate_trivia_with_claude(country: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"""「{country}」に関する、日本人が読んで「へえ！」「知らなかった！」と思うようなトリビアを1つ生成してください。

【条件】
- 具体的な数字・統計・固有名詞を含める
- 日本との対比で面白さが際立つものが望ましい
- factは80文字以内
- commentは50文字以内。日本人目線のユーモアある一言（絵文字1〜2個）
- 以下のJSON形式のみで返すこと：
{{"country": "{country}", "fact": "トリビア内容", "comment": "一言コメント"}}"""

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)
    return {"id": "generated", **result}


def build_trivia_post(item: dict) -> str:
    country = item["country"]
    fact    = item["fact"]
    comment = item["comment"]

    extra = ""
    for key, tag in COUNTRY_HASHTAGS.items():
        if key in country:
            extra = f" {tag}"
            break

    hashtags = f"#北欧トリビア #Nordic{extra}"
    post = f"{country} 豆知識\n{fact}\n\n💬 {comment}\n\n{hashtags}"

    if len(post) > 500:
        over = len(post) - 500 + 3
        fact = fact[:max(20, len(fact) - over)] + "..."
        post = f"{country} 豆知識\n{fact}\n\n💬 {comment}\n\n{hashtags}"

    return post


def post_to_threads(text: str) -> bool:
    user_id      = os.environ["THREADS_USER_ID"]
    access_token = os.environ["THREADS_ACCESS_TOKEN"]

    try:
        create_url = f"{THREADS_API_BASE}/{user_id}/threads"
        create_params = {
            "media_type": "TEXT",
            "text": text,
            "access_token": access_token,
        }
        create_resp = requests.post(create_url, data=create_params, timeout=15)
        create_resp.raise_for_status()
        creation_id = create_resp.json()["id"]
        print(f"[THREADS] コンテナ作成成功: {creation_id}")

        time.sleep(2)

        publish_url = f"{THREADS_API_BASE}/{user_id}/threads_publish"
        publish_params = {
            "creation_id": creation_id,
            "access_token": access_token,
        }
        publish_resp = requests.post(publish_url, data=publish_params, timeout=15)
        publish_resp.raise_for_status()
        post_id = publish_resp.json()["id"]
        print(f"[THREADS] 投稿成功: {post_id}")
        return True

    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] Threads投稿失敗: {e} / {e.response.text}")
        return False
    except Exception as e:
        print(f"[ERROR] Threads投稿例外: {e}")
        return False


def main():
    print(f"\n=== Nordic Trivia Bot (Threads) 起動 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===\n")

    used_ids = load_used_ids()
    item = pick_from_db(used_ids)

    if item:
        print(f"[DB] トリビアを選択: {item['country']} / {item['fact'][:40]}...")
    else:
        print("[INFO] DBのトリビアを全件使用済み → リセット")
        used_ids = set()
        item = pick_from_db(used_ids)
        if not item:
            country = random.choice(COUNTRIES)
            print(f"[CLAUDE] {country} のトリビアを生成中...")
            item = generate_trivia_with_claude(country)

    post_text = build_trivia_post(item)
    print(f"[POST] ({len(post_text)}文字)\n{post_text}\n")

    if post_to_threads(post_text):
        if item["id"] != "generated":
            used_ids.add(item["id"])
            save_used_ids(used_ids)
        print(f"[DONE] トリビア投稿完了")
    else:
        print(f"[FAIL] 投稿失敗")


if __name__ == "__main__":
    main()
