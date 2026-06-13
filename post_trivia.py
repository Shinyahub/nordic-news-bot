#!/usr/bin/env python3
"""
Nordic Trivia Bot
北欧4カ国のトリビアをPushover経由でiPhoneに通知するスクリプト。
1日2回（朝・夜）実行される。
trivia.jsonのストックが尽きたらClaudeが新しいトリビアを即興生成する。
"""

import os
import json
import random
import requests
import anthropic
from datetime import datetime, timezone
from pathlib import Path

TRIVIA_FILE      = Path("trivia.json")
USED_IDS_FILE    = Path("used_trivia_ids.json")
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

COUNTRIES = [
    "🇳🇴 ノルウェー",
    "🇸🇪 スウェーデン",
    "🇫🇮 フィンランド",
    "🇩🇰 デンマーク",
]

# 国別ハッシュタグ
COUNTRY_HASHTAGS = {
    "ノルウェー": "#ノルウェー #Norway",
    "スウェーデン": "#スウェーデン #Sweden",
    "フィンランド": "#フィンランド #Finland",
    "デンマーク": "#デンマーク #Denmark",
}

# ──────────────────────────────────────────
# 使用済みトリビアID管理
# ──────────────────────────────────────────

def load_used_ids() -> set:
    if USED_IDS_FILE.exists():
        with open(USED_IDS_FILE, "r") as f:
            return set(json.load(f).get("ids", []))
    return set()


def save_used_ids(ids: set):
    with open(USED_IDS_FILE, "w") as f:
        json.dump({"ids": list(ids)}, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────
# トリビア取得（DB or Claude生成）
# ──────────────────────────────────────────

def load_trivia_db() -> list:
    if TRIVIA_FILE.exists():
        with open(TRIVIA_FILE, "r") as f:
            return json.load(f).get("trivia", [])
    return []


def pick_from_db(used_ids: set) -> dict | None:
    """DBから未使用のトリビアをランダムに1件選ぶ"""
    db = load_trivia_db()
    unused = [t for i, t in enumerate(db) if str(i) not in used_ids]
    if not unused:
        return None
    item = random.choice(unused)
    idx = db.index(item)
    return {"id": str(idx), **item}


def generate_trivia_with_claude(country: str) -> dict:
    """Claudeが新しいトリビアを即興生成する"""
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


# ──────────────────────────────────────────
# ツイート文・通知の組み立て
# ──────────────────────────────────────────

def build_trivia_tweet(item: dict) -> str:
    country = item["country"]
    fact    = item["fact"]
    comment = item["comment"]

    # 国別ハッシュタグ
    extra = ""
    for key, tag in COUNTRY_HASHTAGS.items():
        if key in country:
            extra = f" {tag}"
            break

    hashtags = f"#北欧トリビア #Nordic{extra}"
    tweet = f"{country} 豆知識\n{fact}\n\n💬 {comment}\n\n{hashtags}"

    # 280文字を超える場合はfactを切り詰める
    if len(tweet) > 280:
        over = len(tweet) - 280 + 3
        fact = fact[:max(20, len(fact) - over)] + "..."
        tweet = f"{country} 豆知識\n{fact}\n\n💬 {comment}\n\n{hashtags}"

    return tweet


# ──────────────────────────────────────────
# Pushover通知
# ──────────────────────────────────────────

def send_pushover(title: str, message: str) -> bool:
    payload = {
        "token":    os.environ["PUSHOVER_APP_TOKEN"],
        "user":     os.environ["PUSHOVER_USER_KEY"],
        "title":    title,
        "message":  message,
        "url":      "twitter://post",
        "url_title": "Xで投稿する →",
        "priority": 0,
    }
    try:
        resp = requests.post(PUSHOVER_API_URL, data=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[NOTIFIED] Pushover送信成功")
            return True
        else:
            print(f"[ERROR] Pushover失敗: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"[ERROR] Pushover例外: {e}")
        return False


# ──────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────

def main():
    print(f"\n=== Nordic Trivia Bot 起動 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===\n")

    used_ids = load_used_ids()

    # DBから未使用トリビアを選ぶ、なければClaudeが生成
    item = pick_from_db(used_ids)
    if item:
        print(f"[DB] トリビアを選択: {item['country']} / {item['fact'][:40]}...")
    else:
        # 全部使い切ったらリセット
        print("[INFO] DBのトリビアを全件使用済み → リセットして再利用 or Claude生成")
        used_ids = set()
        item = pick_from_db(used_ids)
        if not item:
            # DBが空の場合はClaude生成
            country = random.choice(COUNTRIES)
            print(f"[CLAUDE] {country} のトリビアを生成中...")
            item = generate_trivia_with_claude(country)

    tweet = build_trivia_tweet(item)
    print(f"[TWEET] ({len(tweet)}文字)\n{tweet}\n")

    # 通知タイトル：国旗＋国名のみ
    flag_and_name = item["country"]
    notif_title   = f"{flag_and_name} 豆知識"
    # 通知本文：ツイートから1行目（タイトル行）を除いたもの
    tweet_lines   = tweet.split("\n")
    notif_message = "\n".join(tweet_lines[1:]).lstrip("\n")

    if send_pushover(notif_title, notif_message):
        if item["id"] != "generated":
            used_ids.add(item["id"])
            save_used_ids(used_ids)
        print(f"[DONE] トリビア通知完了")
    else:
        print(f"[FAIL] 通知失敗")


if __name__ == "__main__":
    main()
