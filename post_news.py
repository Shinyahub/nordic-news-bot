#!/usr/bin/env python3
"""
Nordic News Bot
北欧（ノルウェー・スウェーデン・フィンランド・デンマーク）のニュースを
Claude APIで日本語要約してXに自動投稿するスクリプト

通常モード : 各国フィードから最新1件ずつ投稿
バズモード : 各国の最新5件をClaudeが「面白さ」採点し、8点以上なら追加投稿
"""

import os
import json
import hashlib
import feedparser
import anthropic
import tweepy
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────

# 通常投稿フィード（最新1件）
NEWS_FEEDS = {
    "🇳🇴 ノルウェー(NRK)":  "https://www.nrk.no/nyheter/siste.rss",
    "🇸🇪 スウェーデン(SVT)": "https://www.svt.se/nyheter/rss.xml",
    "🇫🇮 フィンランド(YLE)": "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET",
    "🇩🇰 デンマーク(DR)":   "https://www.dr.dk/nyheder/service/feeds/allenyheder",
}

# バズ検知フィード（最新5件をスコアリング）
# 人気記事フィードがある国はそちらを、ない国は通常フィードを流用
BUZZ_FEEDS = {
    "🇳🇴 ノルウェー(NRK)":  "https://www.nrk.no/toppsaker.rss",          # NRK人気記事
    "🇸🇪 スウェーデン(SVT)": "https://www.svt.se/nyheter/rss.xml",        # 通常フィード流用
    "🇫🇮 フィンランド(YLE)": "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET",
    "🇩🇰 デンマーク(DR)":   "https://www.dr.dk/nyheder/service/feeds/allenyheder",
}

# バズ判定の閾値（この点数以上なら追加ツイート）
BUZZ_THRESHOLD = 8

# 1実行あたりのバズ追加投稿の上限（暴走防止）
BUZZ_MAX_POSTS = 2

POSTED_IDS_FILE = Path("posted_ids.json")
MAX_TWEET_LENGTH = 280


# ──────────────────────────────────────────
# 投稿済みID管理
# ──────────────────────────────────────────

def load_posted_ids() -> set:
    if POSTED_IDS_FILE.exists():
        with open(POSTED_IDS_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("ids", []))
    return set()


def save_posted_ids(ids: set):
    recent = list(ids)[-1000:]
    with open(POSTED_IDS_FILE, "w") as f:
        json.dump({"ids": recent}, f, ensure_ascii=False, indent=2)


def article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


# ──────────────────────────────────────────
# RSSフィード取得
# ──────────────────────────────────────────

def parse_feed_entries(feed_dict: dict, max_per_country: int = 1) -> list[dict]:
    """フィード辞書から記事リストを取得する汎用関数"""
    articles = []
    for country, url in feed_dict.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                print(f"[WARN] {country}: フィードが空です")
                continue

            for entry in feed.entries[:max_per_country]:
                link  = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                if not link or not title:
                    continue
                articles.append({
                    "country": country,
                    "title":   title,
                    "summary": summary[:500],
                    "url":     link,
                    "id":      article_id(link),
                })
            print(f"[OK] {country}: {len(feed.entries[:max_per_country])}件取得")

        except Exception as e:
            print(f"[ERROR] {country}: フィード取得失敗 - {e}")

    return articles


# ──────────────────────────────────────────
# Claude API
# ──────────────────────────────────────────

def _claude_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def summarize_with_claude(article: dict) -> dict:
    """要約＋ユーモア感想を生成。{"summary": str, "comment": str} を返す"""
    prompt = f"""以下は北欧のニュース記事です。日本語で要約と一言感想を生成してください。

【条件】
- 要約は60文字以内
- 感想は50文字以内。日本との対比・日本人目線のツッコミ・シニカルなユーモアを交えた一言にする
  （例：ストライキのニュース →「ストライキの無い日本の地下鉄とJRの職員の皆さん、ありがとう（合掌）」）
  （例：福祉政策のニュース →「日本にも輸入したい制度ランキング、ぶっちぎり1位へ」）
  （例：物価高のニュース →「北欧の物価高に震えつつ、日本の安さに一瞬感謝する月曜日」）
  ウケ狙いに走りすぎず、くすっと笑えるトーンで。絵文字は1〜2個まで
- 固有名詞（人名・地名）はそのまま使う
- ハッシュタグは付けない（後で付与する）
- 以下のJSON形式のみで返すこと（前置きや説明は不要）：
{{"summary": "要約文", "comment": "一言感想"}}

【タイトル】
{article['title']}

【本文・説明】
{article['summary']}"""

    message = _claude_client().messages.create(
        model="claude-sonnet-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    try:
        result = json.loads(raw)
        return {
            "summary": result.get("summary", "").strip(),
            "comment": result.get("comment", "").strip(),
        }
    except Exception:
        return {"summary": raw[:60], "comment": ""}


def score_buzz(article: dict) -> int:
    """記事の「日本人が読んで面白い度」を1〜10点で採点して返す"""
    prompt = f"""以下は北欧のニュース記事です。
「日本人が読んで面白い・驚く・笑える・シェアしたくなる度」を1〜10の整数で採点してください。

採点基準：
- 10点: 思わず誰かに話したくなるレベル（珍事件・仰天制度・ユニークな文化）
-  8点: へえ！と声が出るレベル（日本との対比が際立つ・意外な統計・ほっこりする話）
-  5点: 普通に興味深いニュース
-  3点: 政治・経済の一般的な報道
-  1点: 地域の細かい行政ニュースなど

整数1つだけ返してください（説明不要）。

【タイトル】{article['title']}
【概要】{article['summary']}"""

    try:
        message = _claude_client().messages.create(
            model="claude-sonnet-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        score = int(message.content[0].text.strip())
        return max(1, min(10, score))  # 1〜10にクランプ
    except Exception as e:
        print(f"[WARN] スコアリング失敗: {e}")
        return 0


# ──────────────────────────────────────────
# X (Twitter) 投稿
# ──────────────────────────────────────────

def get_x_client():
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def build_tweet(article: dict, summary: str, comment: str, is_buzz: bool = False) -> str:
    """ツイート文を組み立てる。バズ記事には🔥ラベルを付ける"""
    hashtags = "#北欧ニュース #Nordic"
    label = f"{article['country']} 🔥 #話題\n" if is_buzz else f"{article['country']}\n"
    comment_line = f"💬 {comment}\n" if comment else ""
    tweet = f"{label}{summary}\n{comment_line}\n🔗 {article['url']}\n\n{hashtags}"

    if len(tweet) > MAX_TWEET_LENGTH:
        over = len(tweet) - MAX_TWEET_LENGTH + 3
        summary = summary[: max(10, len(summary) - over)] + "..."
        tweet = f"{label}{summary}\n{comment_line}\n🔗 {article['url']}\n\n{hashtags}"

    return tweet


def post_to_x(tweet_text: str) -> bool:
    try:
        client = get_x_client()
        response = client.create_tweet(text=tweet_text)
        print(f"[POSTED] Tweet ID: {response.data['id']}")
        return True
    except tweepy.TweepyException as e:
        print(f"[ERROR] X投稿失敗: {e}")
        return False


def process_and_post(article: dict, posted_ids: set, is_buzz: bool = False) -> bool:
    """要約→ツイート組み立て→投稿 の一連処理。投稿成功でTrueを返す"""
    try:
        result = summarize_with_claude(article)
        summary = result["summary"]
        comment = result["comment"]
        print(f"  [SUMMARY] {summary}")
        print(f"  [COMMENT] {comment}")
    except Exception as e:
        print(f"  [ERROR] 要約失敗: {e}")
        return False

    tweet = build_tweet(article, summary, comment, is_buzz=is_buzz)
    print(f"  [TWEET] ({len(tweet)}文字)\n{tweet}\n")

    if post_to_x(tweet):
        posted_ids.add(article["id"])
        return True
    return False


# ──────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────

def main():
    print(f"\n=== Nordic News Bot 起動 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===\n")

    posted_ids = load_posted_ids()
    total_posted = 0

    # ── 1. 通常投稿：各国最新1件 ──────────────────
    print("── 通常投稿フェーズ ──────────────────────────")
    regular_articles = parse_feed_entries(NEWS_FEEDS, max_per_country=1)

    for article in regular_articles:
        if article["id"] in posted_ids:
            print(f"[SKIP] 投稿済み: {article['country']}")
            continue
        print(f"\n[REGULAR] {article['country']} / {article['title'][:50]}...")
        if process_and_post(article, posted_ids, is_buzz=False):
            total_posted += 1

    # ── 2. バズ検知：各国5件をスコアリング ────────
    print("\n── バズ検知フェーズ ──────────────────────────")
    buzz_articles = parse_feed_entries(BUZZ_FEEDS, max_per_country=5)
    buzz_posted = 0

    # スコアリング対象：未投稿記事のみ
    candidates = [a for a in buzz_articles if a["id"] not in posted_ids]
    print(f"[INFO] スコアリング対象: {len(candidates)}件")

    scored = []
    for article in candidates:
        score = score_buzz(article)
        print(f"  [{score:2d}点] {article['country']} / {article['title'][:50]}...")
        if score >= BUZZ_THRESHOLD:
            scored.append((score, article))

    # 点数の高い順に並べて上限まで投稿
    scored.sort(key=lambda x: x[0], reverse=True)

    for score, article in scored:
        if buzz_posted >= BUZZ_MAX_POSTS:
            print(f"[INFO] バズ投稿上限({BUZZ_MAX_POSTS}件)に達しました")
            break
        print(f"\n[BUZZ🔥 {score}点] {article['country']} / {article['title'][:50]}...")
        if process_and_post(article, posted_ids, is_buzz=True):
            total_posted += 1
            buzz_posted += 1

    save_posted_ids(posted_ids)
    print(f"\n=== 完了: 通常{total_posted - buzz_posted}件 + バズ{buzz_posted}件 = 計{total_posted}件投稿 ===\n")


if __name__ == "__main__":
    main()
