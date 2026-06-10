# 🌍 Nordic News Bot

北欧4カ国（ノルウェー・スウェーデン・フィンランド・デンマーク）の最新ニュースを
Claude APIで日本語要約し、X（Twitter）に自動投稿するボットです。

---

## 構成ファイル

```
.
├── post_news.py                        # メインスクリプト
├── requirements.txt                    # Python依存パッケージ
├── posted_ids.json                     # 投稿済み記事ID（自動生成）
└── .github/
    └── workflows/
        └── nordic_news.yml             # GitHub Actions ワークフロー
```

---

## セットアップ手順

### 1. リポジトリを作成

GitHub で新しいリポジトリを作成し、このフォルダの中身をすべてプッシュします。

> **Note:** パブリックリポジトリにすると GitHub Actions が完全無料になります。
> APIキーはすべて Secrets に入れるので、コードに秘密情報は含まれません。

---

### 2. APIキーを取得する

#### 🤖 Anthropic API キー
1. https://console.anthropic.com にアクセス
2. 「API Keys」→「Create Key」

#### 🐦 X (Twitter) API キー
1. https://developer.twitter.com/en/portal/dashboard にアクセス
2. アプリを作成し、以下の4つを取得：
   - API Key（`X_API_KEY`）
   - API Key Secret（`X_API_KEY_SECRET`）
   - Access Token（`X_ACCESS_TOKEN`）
   - Access Token Secret（`X_ACCESS_TOKEN_SECRET`）
3. アプリの権限を **「Read and Write」** に設定すること（デフォルトは Read Only）

---

### 3. GitHub Secrets に登録

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** から
以下の5つを登録します：

| Secret名                  | 内容                        |
|---------------------------|-----------------------------|
| `ANTHROPIC_API_KEY`       | Anthropic API キー          |
| `X_API_KEY`               | X API Key                   |
| `X_API_KEY_SECRET`        | X API Key Secret            |
| `X_ACCESS_TOKEN`          | X Access Token              |
| `X_ACCESS_TOKEN_SECRET`   | X Access Token Secret       |

---

### 4. 動作確認（手動実行）

1. リポジトリの **Actions** タブを開く
2. 左側のワークフロー一覧から「Nordic News Bot」を選択
3. 「Run workflow」→「Run workflow」をクリック
4. ログを確認して問題なければ完了 ✅

---

### 5. cron-job.org で信頼性を上げる（推奨）

GitHub Actions のスケジュール実行は遅延・スキップが発生することがあります。
外部サービスから叩くことで安定性が増します。

1. https://cron-job.org に無料登録
2. 「Create cronjob」で以下を設定：

| 項目 | 値 |
|------|----|
| URL | `https://api.github.com/repos/【ユーザー名】/【リポジトリ名】/actions/workflows/nordic_news.yml/dispatches` |
| Method | POST |
| Headers | `Authorization: Bearer 【GitHub Personal Access Token】` / `Accept: application/vnd.github+json` / `Content-Type: application/json` |
| Body | `{"ref": "main"}` |
| 実行頻度 | 4時間ごと（0, 4, 8, 12, 16, 20時 UTC）|

**GitHub Personal Access Token の作成：**
Settings → Developer settings → Personal access tokens → Fine-grained tokens
→ 対象リポジトリに `Actions: Read and write` 権限を付与

---

## 投稿スケジュール

| UTC   | JST   |
|-------|-------|
| 00:00 | 09:00 |
| 04:00 | 13:00 |
| 08:00 | 17:00 |
| 12:00 | 21:00 |
| 16:00 | 翌01:00 |
| 20:00 | 翌05:00 |

---

## ニュースソース

| 国 | メディア | フィードURL |
|----|----------|-------------|
| 🇳🇴 ノルウェー | NRK | https://www.nrk.no/nyheter/siste.rss |
| 🇸🇪 スウェーデン | SVT | https://www.svt.se/nyheter/rss.xml |
| 🇫🇮 フィンランド | YLE | https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET |
| 🇩🇰 デンマーク | DR | https://www.dr.dk/nyheder/service/feeds/allenyheder |

---

## 投稿サンプル

```
🇳🇴 ノルウェー(NRK)
オスロで大規模な地下鉄ストライキが発生。通勤者に大きな影響が出ています。
💬 ストライキの無い日本の地下鉄とJRの職員の皆さん、ありがとう（合掌）

🔗 https://www.nrk.no/...

#北欧ニュース #Nordic
```

---

## コスト試算（月間）

| サービス | 費用 |
|---------|------|
| GitHub Actions（パブリックリポジトリ） | 無料 |
| Anthropic API（Claude Sonnet 4） | 約 $0.5〜1 / 月 |
| X API（Basic プラン） | $100 / 月 ※ |
| cron-job.org | 無料 |

> ※ X API の無料枠（Free）は月1,500ツイートまで書き込み可能ですが、
> 1日6回 × 4カ国 × 30日 = 720ツイート/月 なので**無料枠内に収まります**。

---

## カスタマイズ

### 投稿頻度を変更する
`.github/workflows/nordic_news.yml` の cron 式を編集してください。

### ニュースソースを追加・変更する
`post_news.py` の `NEWS_FEEDS` 辞書に追記するだけです：

```python
NEWS_FEEDS = {
    "🇮🇸 アイスランド(RÚV)": "https://www.ruv.is/rss/frettir",
    # ...
}
```

### ハッシュタグを変更する
`post_news.py` の `build_tweet` 関数内の `hashtags` 変数を編集してください。
