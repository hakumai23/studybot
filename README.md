# studybot

RenderのBackground Workerで動かすdiscord.pyボットです。

## ローカルセットアップ

1. 依存関係をインストール

   uv venv
   uv pip install -e .

2. 環境変数を用意

   DISCORD_TOKEN を設定

## ローカル実行

uv run python bot.py

## 初期設定（アプリケーションコマンド）

/config set_general で通知チャンネルを設定
/config set_game で移動元ボイスを設定
/config set_study で移動先ボイスを設定
/config set_users で対象ユーザーIDを設定（未設定ならGAMEにいる全員が対象）
/config set_time で通知時刻を設定
/config set_timezone でタイムゾーンを設定
/config set_message で通知文を設定
/config set_weekly で週次通知曜日と時刻を設定（weekday: 0=月 ... 6=日）
/config set_weekly_enabled で週次通知の有効/無効を設定
/config move_study_to_game でSTUDYの全員をGAMEへ即時移動
/config show で現在設定を確認
/study me で自分の今日の勉強時間を確認
/study rank で今日の勉強時間ランキングを確認

## 勉強時間記録

STUDYチャンネルに入ると計測開始、出ると加算保存されます。
データは study_time.db に保存され、再起動後も残ります。
ミュート状態も勉強時間に含まれます。

## 週次通知

週1回、generalチャンネルにその週の勉強時間ランキングを通知します。
初期値は日曜21:00です。

## Renderデプロイ

1. RenderでBackground Workerを作成
2. Persistent Diskを追加（Mount Path: /var/data）
3. Environmentに DATA_DIR=/var/data を登録
4. Build Command: pip install -r requirements.txt
5. Start Command: python bot.py
6. Environmentに DISCORD_TOKEN を登録

config.json と study_time.db は DATA_DIR 配下に保存されます。

## Discord開発者ポータル設定

Privileged Gateway Intents は必須ではありません。
Botの権限として View Channels / Send Messages / Move Members を付与してください。
