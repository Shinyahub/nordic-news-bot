#!/usr/bin/env python3
"""
Nordic News Bot
北欧（ノルウェー・スウェーデン・フィンランド・デンマーク）のニュースを
Claude APIで日本語要約し、Pushover経由でiPhoneに通知する。
ユーザーが通知をタップするとXアプリがツイート画面を開く（半自動投稿）。

通常モード : 各国フィードから最新1件ずつ通知
バズモード : 各国の最新5件をClaudeが「面白さ」採点し、8点以上なら追加通知
"""

import os
import json
import hashlib
import requests
import feedparser
import anthropic
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────

# 通常投稿フィード
# 各国の「国内・地域ニュース」専用フィードを優先して使用
NEWS_FEEDS = {
    # ノルウェー
    "🇳🇴 ノルウェー(NRK国内)":      "https://www.nrk.no/norge/siste.rss",
    "🇳🇴 ノルウェー(NRK社会)":      "https://www.nrk.no/livsstil/siste.rss",
    # スウェーデン
    "🇸🇪 スウェーデン(SVT国内)":     "https://www.svt.se/nyheter/inrikes/rss.xml",
    "🇸🇪 スウェーデン(SVT地域)":     "https://www.svt.se/nyheter/lokalt/rss.xml",
    # フィンランド
    "🇫🇮 フィンランド(YLE国内)":     "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&formatId=49",
    "🇫🇮 フィンランド(YLE文化)":     "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&formatId=57",
    # デンマーク
    "🇩🇰 デンマーク(DR国内)":        "https://www.dr.dk/nyheder/service/feeds/indblik",
    "🇩🇰 デンマーク(DR地域)":        "https://www.dr.dk/nyheder/service/feeds/regionale",
}

# バズ検知フィード（各国人気・注目記事）
BUZZ_FEEDS = {
    "🇳🇴 ノルウェー(NRK人気)":      "https://www.nrk.no/toppsaker.rss",
    "🇸🇪 スウェーデン(SVT国内)":     "https://www.svt.se/nyheter/inrikes/rss.xml",
    "🇫🇮 フィンランド(YLE国内)":     "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&formatId=49",
    "🇩🇰 デンマーク(DR国内)":        "https://www.dr.dk/nyheder/service/feeds/indblik",
}

BUZZ_THRESHOLD = 8
BUZZ_MAX_POSTS = 2

POSTED_IDS_FILE = Path("posted_ids.json")
MAX_TWEET_LENGTH = 280

# Pushover API エンドポイント
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


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
    # Claudeが ```json ... ``` で返してくることがあるので除去
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
        return True  # 判定失敗時は通過させる



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
# ツイート文の組み立て
# ──────────────────────────────────────────

def build_tweet(article: dict, summary: str, comment: str, is_buzz: bool = False) -> str:
    """ツイート文を組み立てる。バズ記事には🔥ラベルを付ける"""

    # 国別ハッシュタグ
    country_hashtags = {
        "ノルウェー": "#ノルウェー #Norway",
        "スウェーデン": "#スウェーデン #Sweden",
        "フィンランド": "#フィンランド #Finland",
        "デンマーク": "#デンマーク #Denmark",
    }
    # 記事の国名に対応するハッシュタグを選択
    extra = ""
    for key, tag in country_hashtags.items():
        if key in article["country"]:
            extra = f" {tag}"
            break

    hashtags = f"#北欧 #Nordic #北欧ニュース{extra}"
    label = f"{article['country']} 🔥 #話題\n" if is_buzz else f"{article['country']}\n"
    comment_line = f"💬 {comment}\n" if comment else ""
    tweet = f"{label}{summary}\n{comment_line}\n🔗 {article['url']}\n\n{hashtags}"

    if len(tweet) > MAX_TWEET_LENGTH:
        over = len(tweet) - MAX_TWEET_LENGTH + 3
        summary = summary[: max(10, len(summary) - over)] + "..."
        tweet = f"{label}{summary}\n{comment_line}\n🔗 {article['url']}\n\n{hashtags}"

    return tweet


# ──────────────────────────────────────────
# Pushover 通知
# ──────────────────────────────────────────

def build_x_url_scheme() -> str:
    """XアプリのURLスキームを生成（タップするとXの投稿画面が開く）"""
    return "twitter://post"


def send_pushover(title: str, message: str, url: str, url_title: str) -> bool:
    """Pushover経由でiPhoneに通知を送る"""
    payload = {
        "token":     os.environ["PUSHOVER_APP_TOKEN"],
        "user":      os.environ["PUSHOVER_USER_KEY"],
        "title":     title,
        "message":   message,
        "url":       url,
        "url_title": url_title,
        "priority":  0,  # 通常通知（-1=静音 / 0=通常 / 1=高優先）
    }
    try:
        resp = requests.post(PUSHOVER_API_URL, data=payload, timeout=10)
        if resp.status_code == 200:
            print(f"  [NOTIFIED] Pushover送信成功")
            return True
        else:
            print(f"  [ERROR] Pushover失敗: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"  [ERROR] Pushover例外: {e}")
        return False


def process_and_notify(article: dict, posted_ids: set, is_buzz: bool = False) -> bool:
    """要約→ツイート文組み立て→Pushover通知 の一連処理"""
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

    # 通知タイトル：国旗＋国名のみ（メディア名は除く）
    # 例：「🇳🇴 ノルウェー」「🔥 🇸🇪 スウェーデン」
    flag_and_name = article["country"].split("(")[0].strip()  # "(NRK国内)"などを除去
    buzz_label = "🔥 " if is_buzz else ""
    notif_title = f"{buzz_label}{flag_and_name}"

    # 通知本文：ツイート文から冒頭の国ラベル行を除いたもの（タイトルと重複するため）
    # ツイートの1行目（国名ラベル）を除いて残りをそのまま使う
    tweet_lines = tweet.split("\n")
    notif_message = "\n".join(tweet_lines[1:]).lstrip("\n")

    # URLスキーム：タップするとXアプリの投稿画面が開く
    x_url = build_x_url_scheme()

    if send_pushover(notif_title, notif_message, url=x_url, url_title="Xで投稿する →"):
        posted_ids.add(article["id"])
        return True
    return False


# ──────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────

def main():
    print(f"\n=== Nordic News Bot 起動 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===\n")

    posted_ids = load_posted_ids()
    total_notified = 0

    # ── 1. 通常通知：各国最新1件 ──────────────────
    print("── 通常通知フェーズ ──────────────────────────")
    regular_articles = parse_feed_entries(NEWS_FEEDS, max_per_country=1)

    for article in regular_articles:
        if article["id"] in posted_ids:
            print(f"[SKIP] 通知済み: {article['country']}")
            continue
        print(f"\n[REGULAR] {article['country']} / {article['title'][:50]}...")
        # ローカルニュースフィルタリング
        if not is_local_news(article):
            print(f"  [SKIP] 国際ニュースのため除外")
            posted_ids.add(article["id"])  # 次回も除外されるよう記録
            continue
        if process_and_notify(article, posted_ids, is_buzz=False):
            total_notified += 1

    # ── 2. バズ検知：各国5件をスコアリング ────────
    print("\n── バズ検知フェーズ ──────────────────────────")
    buzz_articles = parse_feed_entries(BUZZ_FEEDS, max_per_country=5)
    buzz_notified = 0

    candidates = [a for a in buzz_articles if a["id"] not in posted_ids]
    print(f"[INFO] スコアリング対象: {len(candidates)}件")

    scored = []
    for article in candidates:
        # ローカルニュースフィルタリング
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
        if buzz_notified >= BUZZ_MAX_POSTS:
            print(f"[INFO] バズ通知上限({BUZZ_MAX_POSTS}件)に達しました")
            break
        print(f"\n[BUZZ🔥 {score}点] {article['country']} / {article['title'][:50]}...")
        if process_and_notify(article, posted_ids, is_buzz=True):
            total_notified += 1
            buzz_notified += 1

    save_posted_ids(posted_ids)
    print(f"\n=== 完了: 通常{total_notified - buzz_notified}件 + バズ{buzz_notified}件 = 計{total_notified}件通知 ===\n")


if __name__ == "__main__":
    main()
