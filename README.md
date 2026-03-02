# studybot

Discordサーバーで学習時間管理を行う `discord.py` 製ボットです。

主な機能:

- 指定時刻に `GAME` と `ANYTHINGOK_VOICE` から `STUDY` へメンバーを移動
- 通知チャンネルにメッセージ送信（任意のロールメンション付き）
- STUDY入退室から勉強時間を日次集計
- 今日の個人時間・ランキング表示
- 週次ランキング通知

## 動作環境

- Python 3.11+
- 依存: `discord.py>=2.3.2`

## 保存データ

- 設定ファイル: `config.json`
- 勉強時間DB: `study_time.db`（SQLite）

保存先は `DATA_DIR` 環境変数で指定できます。

- `DATA_DIR` 未設定: 実行ディレクトリ
- `DATA_DIR=/path/to/data` 設定時: 指定ディレクトリ配下

## ローカルセットアップ

1. 仮想環境と依存インストール

   uv venv
   uv pip install -e .

2. 環境変数を設定

   DISCORD_TOKEN を設定

3. 実行

   uv run python bot.py

## 通知・移動仕様

- 毎分ループで各Guildを判定
- `notify_time` と `timezone` に一致した分に1回だけ実行
- 実行時の処理順:
  1. `game_channel_id` の全員を `study_channel_id` へ移動
   2. `anythingok_voice_channel_id` が設定されていれば、その全員も `study_channel_id` へ移動
  3. `general_channel_id` へ通知メッセージ送信
  4. 週次通知条件を満たせば週次ランキングを送信

補足:

- `game` と `anythingok_voice` 両方に同じ人がいても移動は1回
- `excluded_role_ids` に含まれるロールを持つメンバーは移動対象外
- 以前の `target_user_ids` フィルタは撤廃済み
- `maintenance_enabled=true` の間は定時処理を停止

## 通知メッセージ仕様

- 本文は `notify_message`
- `notify_role_id` 設定時は先頭に `<@&ROLE_ID>` を付与して送信
- 送信先は `general_channel_id`

## 勉強時間計測仕様

- `study_channel_id` に入室した時点でセッション開始
- `study_channel_id` から退出した時点でセッション終了して加算
- 日付境界をまたぐセッションは `reset_time` を日次境界として分割して保存
- 「今日」の判定はGuildごとの `timezone` と `reset_time` を使用
- ミュート状態でも計測対象

保存テーブル:

- `study_sessions(guild_id, user_id, started_at)`
- `study_daily(guild_id, user_id, study_date, seconds)`

## 週次通知仕様

- `weekly_enabled=True` のとき有効
- `weekly_weekday`（0=月 ... 6=日）かつ `weekly_time` の時刻に送信
- 同一週の重複送信防止: `weekly_last_sent_week`
- 集計は `weekly_period_days` 日間（既定7日）の合計
- 通知本文に対象期間（開始〜現在）を表示
- 送信先: `general_channel_id`

## スラッシュコマンド

### 一般ユーザー

- `/config show` 現在設定を表示
- `/study me` 自分の今日の勉強時間を表示
- `/study rank [limit]` 今日のランキングを表示（1〜30、デフォルト10）

### 管理者のみ

- `/config set_general <text_channel>` 通知チャンネル設定
- `/config set_game <voice_channel>` 移動元ゲームチャンネル設定
- `/config set_anythingok_voice <voice_channel>` 移動元何でも可設定
- `/config set_study <voice_channel>` 移動先勉強チャンネル設定
- `/config set_time <HH:MM>` 通知時刻設定
- `/config set_timezone <tz_name>` タイムゾーン設定
- `/config set_message <text>` 通知文設定
- `/config set_reset_time <HH:MM>` 日次リセット時刻設定
- `/config set_notify_role <role>` 通知メンションロール設定
- `/config clear_notify_role` 通知メンションロール解除
- `/config add_exclude_role <role>` 移動除外ロール追加
- `/config remove_exclude_role <role>` 移動除外ロール削除
- `/config clear_exclude_roles` 移動除外ロール全削除
- `/config set_maintenance <true/false>` 定時処理停止スイッチ
- `/config set_error_channel <text_channel>` エラー通知先設定
- `/config clear_error_channel` エラー通知先解除
- `/config set_weekly <weekday> <HH:MM>` 週次通知スケジュール設定
- `/config set_weekly_enabled <true/false>` 週次通知ON/OFF
- `/config set_weekly_period <days>` 週次ランキング対象日数設定
- `/config move_game_to_study` ゲーム/何でも可の全員を勉強へ即時移動
- `/config dry_run` 移動対象人数と対象メンバー（先頭20人）を確認
- `/config run_now` 手動で移動＋通知を即時実行
- `/config move_study_to_game` STUDYの全員をGAMEへ即時移動

## 設定キー一覧（Guildごと）

- `notify_time` 既定: `21:00`
- `timezone` 既定: `Asia/Tokyo`
- `notify_message` 既定: `勉強の時間です`
- `notify_role_id` 既定: `null`
- `general_channel_id` 既定: `1477655666528096440`
- `game_channel_id` 既定: `1370726579021283334`
- `anythingok_voice_channel_id` 既定: `null`
- `study_channel_id` 既定: `1473956243486933145`
- `excluded_role_ids` 既定: `[]`
- `maintenance_enabled` 既定: `false`
- `error_channel_id` 既定: `null`
- `reset_time` 既定: `00:00`
- `weekly_enabled` 既定: `true`
- `weekly_weekday` 既定: `6`
- `weekly_time` 既定: `21:00`
- `weekly_period_days` 既定: `7`
- `weekly_last_sent_week` 既定: `""`

## 自宅サーバー運用（WinSW）

- `studybot-service.exe`（WinSWをリネーム）と `studybot-service.xml` を使用
- サービス管理コマンド例:

  .\studybot-service.exe install studybot-service.xml
  .\studybot-service.exe start studybot-service.xml
  .\studybot-service.exe restart studybot-service.xml
  .\studybot-service.exe status studybot-service.xml
  .\studybot-service.exe stop studybot-service.xml
  .\studybot-service.exe uninstall studybot-service.xml

## Renderデプロイ

`render.yaml` を利用:

- Service type: Background Worker
- Build: `pip install -r requirements.txt`
- Start: `python bot.py`
- 永続化: Disk mount `/var/data` + `DATA_DIR=/var/data`
- 必須環境変数: `DISCORD_TOKEN`

## Discord側の必要権限

- View Channels
- Send Messages
- Move Members

Privileged Gateway Intents は必須ではありません。
