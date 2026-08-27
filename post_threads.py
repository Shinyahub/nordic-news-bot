#!/usr/bin/env python3
"""
Nordic News Bot for Threads
北欧（ノルウェー・スウェーデン・フィンランド・デンマーク）のニュースを
Claude APIで日本語要約し、Threads APIで完全自動投稿する。

通常モード : 各国フィードから最新1件ずつ投稿
バズモード : 各国の最新5件をClaudeが「面白さ」採点し、8点以上なら追加投稿
"""

import os
import json
import time
import hashlib
import requests
import feedparser
import anthropic
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────

NEWS_FEEDS = {
    "🇳🇴 ノルウェー(NRK)":          "https://www.nrk.no/nyheter/siste.rss",
    "🇳🇴 ノルウェー(Aftenposten)":   "https://www.aftenposten.no/rss",
    "🇳🇴 ノルウェー(VG)":            "https://vg.no/rss/feed/?format=rss",
    "🇳🇴 ノルウェー(The Local)":     "https://feeds.thelocal.com/rss/no",
    "🇸🇪 スウェーデン(SVT国内)":     "https://www.svt.se/nyheter/inrikes/rss.xml",
    "🇸🇪 スウェーデン(Aftonbladet)": "https://www.aftonbladet.se/rss.xml",
    "🇸🇪 スウェーデン(The Local)":   "https://feeds.thelocal.com/rss/se",
    "🇫🇮 フィンランド(YLE)":         "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&formatId=49",
    "🇫🇮 フィンランド(YLE文化)":     "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&formatId=57",
    "🇫🇮 フィンランド(YLE英語)":     "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&language=fi-FI&region=fi&languageCodes=en",
    "🇩🇰 デンマーク(DR国内)":        "https://www.dr.dk/nyheder/service/feeds/indland",
    "🇩🇰 デンマーク(DR地域)":        "https://www.dr.dk/nyheder/service/feeds/regionale",
    "🇩🇰 デンマーク(CPH Post)":      "https://cphpost.dk/?format=feed&type=rss",
}

BUZZ_FEEDS = {
    "🇳🇴 ノルウェー(NRK人気)":      "https://www.nrk.no/toppsaker.rss",
    "🇳🇴 ノルウェー(The Local)":    "https://feeds.thelocal.com/rss/no",
    "🇸🇪 スウェーデン(SVT国内)":    "https://www.svt.se/nyheter/inrikes/rss.xml",
    "🇸🇪 スウェーデン(The Local)":  "https://feeds.thelocal.com/rss/se",
    "🇫🇮 フィンランド(YLE)":        "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&formatId=49",
    "🇩🇰 デンマーク(DR国内)":       "https://www.dr.dk/nyheder/service/feeds/indland",
    "🇩🇰 デンマーク(CPH Post)":     "https://cphpost.dk/?format=feed&type=rss",
}

BUZZ_THRESHOLD = 8
BUZZ_MAX_POSTS = 2

POSTED_IDS_FILE = Path("posted_ids_threads.json")
MAX_POST_LENGTH = 500  # Threadsの文字数上限

# Threads Graph API
THREADS_API_BASE = "https://graph.threads.net/v1.0"


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
    articles = []
    for country, url in feed_dict.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                print(f"[WARN] {country}: フィードが空です")
                continue
            for entry in feed.entries[:max_per_country]:
                link    = entry.get("link", "")
                title   = entry.get("title", "")
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
- 感想は50文字以内。以下のパターンを記事の内容に応じて使い分けること：
  【日本との対比】日本人目線のツッコミやシニカルな比較
    例：ストライキ →「ストライキの無い日本の地下鉄とJRの職員の皆さん、ありがとう（合掌）」
    例：福祉政策 →「日本にも輸入したい制度ランキング、ぶっちぎり1位へ」
  【知らない文化・スポーツ・概念へのリアクション】日本人が首をかしげるような北欧独自のものに素直に反応
    例：フロアボール →「そもそもフロアボールとは何ぞ…🤔」
    例：珍しい祭り →「北欧にはまだ知らない世界がある…」
  【そのままのユーモア】思わずくすっとくる一言
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
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        result = json.loads(raw)
        return {
            "summary": result.get("summary", "").strip(),
            "comment": result.get("comment", "").strip(),
        }
    except Exception:
        return {"summary": raw[:60], "comment": ""}


def is_local_news(article: dict) -> bool:
    """北欧・Nordic域内のニュースかどうかを判定する。国際ニュースはFalseを返す"""
    prompt = f"""以下は北欧メディアの記事です。
この記事が「北欧・Nordic域内（ノルウェー・スウェーデン・フィンランド・デンマーク・アイスランド・バルト三国など）で起きた出来事や社会・文化・政策に関するニュース」かどうか判定してください。

【判定基準】
- OK（現地ニュース）: 北欧国内の政治・社会・事件・スポーツ・文化・気候・地域の話題
- OK（Nordic域内）: 北欧諸国間の外交・Nordic地域全体に関わる話題
- NG（国際ニュース）: 北欧が主体でない国際政治（米国・中東・ロシアなど）、海外で起きた出来事
  ただし「北欧からみた国際ニュースへの反応・影響」はOK

「yes」か「no」の1単語だけ返してください。

【タイトル】{article['title']}
【概要】{article['summary'][:200]}"""

    try:
        message = _claude_client().messages.create(
            model="claude-sonnet-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = message.content[0].text.strip().lower()
        return answer.startswith("yes")
    except Exception as e:
        print(f"[WARN] ローカル判定失敗: {e} → 通過させる")
        return True


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
        return max(1, min(10, score))
    except Exception as e:
        print(f"[WARN] スコアリング失敗: {e}")
        return 0


# ──────────────────────────────────────────
# 投稿文の組み立て
# ──────────────────────────────────────────

def build_post_text(article: dict, summary: str, comment: str, is_buzz: bool = False) -> str:
    """Threads投稿文を組み立てる"""
    country_hashtags = {
        "ノルウェー": "#ノルウェー #Norway",
        "スウェーデン": "#スウェーデン #Sweden",
        "フィンランド": "#フィンランド #Finland",
        "デンマーク": "#デンマーク #Denmark",
    }
    extra = ""
    for key, tag in country_hashtags.items():
        if key in article["country"]:
            extra = f" {tag}"
            break

    hashtags = f"#北欧 #Nordic #北欧ニュース{extra}"
    flag_and_name = article["country"].split("(")[0].strip()
    label = f"{flag_and_name} 🔥 #話題\n" if is_buzz else f"{flag_and_name}\n"
    comment_line = f"💬 {comment}\n" if comment else ""
    post = f"{label}{summary}\n{comment_line}\n🔗 {article['url']}\n\n{hashtags}"

    if len(post) > MAX_POST_LENGTH:
        over = len(post) - MAX_POST_LENGTH + 3
        summary = summary[: max(10, len(summary) - over)] + "..."
        post = f"{label}{summary}\n{comment_line}\n🔗 {article['url']}\n\n{hashtags}"

    return post


# ──────────────────────────────────────────
# Threads API 投稿
# ──────────────────────────────────────────

def post_to_threads(text: str) -> bool:
    """Threads APIで投稿する（2段階：コンテナ作成 → 公開）"""
    user_id      = os.environ["THREADS_USER_ID"]
    access_token = os.environ["THREADS_ACCESS_TOKEN"]

    try:
        # ① メディアコンテナを作成
        create_url = f"{THREADS_API_BASE}/{user_id}/threads"
        create_params = {
            "media_type": "TEXT",
            "text": text,
            "access_token": access_token,
        }
        create_resp = requests.post(create_url, data=create_params, timeout=15)
        create_resp.raise_for_status()
        creation_id = create_resp.json()["id"]
        print(f"  [THREADS] コンテナ作成成功: {creation_id}")

        # 公開の前に少し待つ（Threads APIの推奨）
        time.sleep(2)

        # ② コンテナを公開
        publish_url = f"{THREADS_API_BASE}/{user_id}/threads_publish"
        publish_params = {
            "creation_id": creation_id,
            "access_token": access_token,
        }
        publish_resp = requests.post(publish_url, data=publish_params, timeout=15)
        publish_resp.raise_for_status()
        post_id = publish_resp.json()["id"]
        print(f"  [THREADS] 投稿成功: {post_id}")
        return True

    except requests.exceptions.HTTPError as e:
        print(f"  [ERROR] Threads投稿失敗: {e} / {e.response.text}")
        return False
    except Exception as e:
        print(f"  [ERROR] Threads投稿例外: {e}")
        return False


def process_and_post(article: dict, posted_ids: set, is_buzz: bool = False) -> bool:
    """要約→投稿文組み立て→Threads投稿 の一連処理"""
    try:
        result = summarize_with_claude(article)
        summary = result["summary"]
        comment = result["comment"]
        print(f"  [SUMMARY] {summary}")
        print(f"  [COMMENT] {comment}")
    except Exception as e:
        print(f"  [ERROR] 要約失敗: {e}")
        return False

    post_text = build_post_text(article, summary, comment, is_buzz=is_buzz)
    print(f"  [POST] ({len(post_text)}文字)\n{post_text}\n")

    if post_to_threads(post_text):
        posted_ids.add(article["id"])
        return True
    return False


# ──────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────

def main():
    print(f"\n=== Nordic News Bot (Threads) 起動 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===\n")

    posted_ids = load_posted_ids()
    total_posted = 0

    print("── 通常投稿フェーズ ──────────────────────────")
    regular_articles = parse_feed_entries(NEWS_FEEDS, max_per_country=1)

    for article in regular_articles:
        if article["id"] in posted_ids:
            print(f"[SKIP] 投稿済み: {article['country']}")
            continue
        print(f"\n[REGULAR] {article['country']} / {article['title'][:50]}...")
        if not is_local_news(article):
            print(f"  [SKIP] 国際ニュースのため除外")
            posted_ids.add(article["id"])
            continue
        if process_and_post(article, posted_ids, is_buzz=False):
            total_posted += 1
        time.sleep(3)  # API連投を避けるための小休止

    print("\n── バズ検知フェーズ ──────────────────────────")
    buzz_articles = parse_feed_entries(BUZZ_FEEDS, max_per_country=5)
    buzz_posted = 0

    candidates = [a for a in buzz_articles if a["id"] not in posted_ids]
    print(f"[INFO] スコアリング対象: {len(candidates)}件")

    scored = []
    for article in candidates:
        if not is_local_news(article):
            print(f"  [SKIP] 国際ニュースのため除外: {article['title'][:40]}...")
            posted_ids.add(article["id"])
            continue
        score = score_buzz(article)
        print(f"  [{score:2d}点] {article['country']} / {article['title'][:50]}...")
        if score >= BUZZ_THRESHOLD:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)

    for score, article in scored:
        if buzz_posted >= BUZZ_MAX_POSTS:
            print(f"[INFO] バズ投稿上限({BUZZ_MAX_POSTS}件)に達しました")
            break
        print(f"\n[BUZZ🔥 {score}点] {article['country']} / {article['title'][:50]}...")
        if process_and_post(article, posted_ids, is_buzz=True):
            total_posted += 1
            buzz_posted += 1
        time.sleep(3)

    save_posted_ids(posted_ids)
    print(f"\n=== 完了: 通常{total_posted - buzz_posted}件 + バズ{buzz_posted}件 = 計{total_posted}件投稿 ===\n")


if __name__ == "__main__":
    main()
