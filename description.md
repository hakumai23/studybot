# studybot コマンド説明

このファイルは、`bot.py` に実装されているスラッシュコマンドの運用向け説明です。

## 前提

- `config` グループのうち設定変更系は管理者権限が必要です。
- `config show`、`study me`、`study rank` は一般メンバーも実行できます。
- 返信は基本的に実行者のみ表示です。

## `/config` コマンド

### `/config show`

- 目的: 現在のギルド設定を一覧表示。
- 主な確認項目: `notify_time`、`timezone`、`excluded_role_ids`、`aggregation_excluded_user_ids`、`maintenance_enabled`、`maintenance_until_epoch`、週次設定。
- 使いどころ: 設定変更後の確認、障害調査時の現状把握。

### `/config set_general channel:<text_channel>`

- 目的: 通知送信先テキストチャンネルを設定。
- 影響範囲: 定時通知、手動 `run_now`、週次ランキング送信先。
- 例: `/config set_general channel:#general`

### `/config set_game channel:<voice_channel>`

- 目的: 定時移動の移動元 `GAME` ボイスチャンネルを設定。
- 影響範囲: `move_game_to_study`、`dry_run`、`run_now`、定時処理。
- 例: `/config set_game channel:GAME`

### `/config set_anythingok_voice channel:<voice_channel>`

- 目的: 追加の移動元 `ANYTHINGOK_VOICE` を設定。
- 影響範囲: `GAME` と合算して重複除去後に移動対象化。
- 例: `/config set_anythingok_voice channel:ANYTHINGOK_VOICE`

### `/config set_study channel:<voice_channel>`

- 目的: 移動先 `STUDY` チャンネルを設定。
- 影響範囲: 定時移動、手動移動、勉強時間の入退室計測。
- 例: `/config set_study channel:STUDY`

### `/config set_time time:<HH:MM>`

- 目的: 定時実行時刻を設定。
- 仕様: `timezone` に基づくローカル時刻で判定。
- 例: `/config set_time time:21:00`

### `/config set_timezone timezone:<tz_name>`

- 目的: 時刻判定に使うタイムゾーンを設定。
- 仕様: IANA形式（例 `Asia/Tokyo`）。
- 例: `/config set_timezone timezone:Asia/Tokyo`

### `/config set_message message:<text>`

- 目的: 通知本文を設定。
- 仕様: `notify_role_id` がある場合は先頭にロールメンション付与。
- 例: `/config set_message message:勉強開始の時間です`

### `/config set_reset_time time:<HH:MM>`

- 目的: 日次集計の切替時刻を設定。
- 影響範囲: `study me` と `study rank` の「今日」判定、週次期間計算。
- 例: `/config set_reset_time time:04:00`

### `/config set_notify_role role:<role>`

- 目的: 通知時にメンションするロールを設定。
- 影響範囲: 定時通知と `run_now` の通知文。
- 例: `/config set_notify_role role:@study`

### `/config clear_notify_role`

- 目的: 通知ロールメンション設定を解除。
- 影響範囲: 通知はメンションなしで送信。
- 例: `/config clear_notify_role`

### `/config add_exclude_role role:<role>`

- 目的: 定時移動対象から除外するロールを追加。
- 影響範囲: 自動移動、`move_game_to_study`、`dry_run`、`run_now`。
- 例: `/config add_exclude_role role:@bot`

### `/config remove_exclude_role role:<role>`

- 目的: 移動除外ロールを削除。
- 影響範囲: 対象ロール所持者が再び移動対象になる。
- 例: `/config remove_exclude_role role:@bot`

### `/config clear_exclude_roles`

- 目的: 移動除外ロールを全解除。
- 例: `/config clear_exclude_roles`

### `/config set_maintenance enabled:<true|false>`

- 目的: 定時処理停止を手動で固定。
- 仕様: `true` で停止、`false` で再開。実行時に `maintenance_until_epoch=0` にリセット。
- 例: `/config set_maintenance enabled:true`

### `/config set_maintenance_for minutes:<1-10080>`

- 目的: 実行時刻から指定分だけメンテナンス有効化。
- 仕様: 期限到達後、次の定時ループで自動解除。
- 例: `/config set_maintenance_for minutes:60`

### `/config add_exclude_user user:<member>`

- 目的: 指定ユーザーをランキング集計から除外。
- 影響範囲: `study rank` と週次ランキング通知。
- 例: `/config add_exclude_user user:@Taro`

### `/config remove_exclude_user user:<member>`

- 目的: 集計除外ユーザーを解除。
- 例: `/config remove_exclude_user user:@Taro`

### `/config clear_exclude_users`

- 目的: 集計除外ユーザーを全解除。
- 例: `/config clear_exclude_users`

### `/config set_error_channel channel:<text_channel>`

- 目的: 例外通知先チャンネルを設定。
- 影響範囲: 定時処理・音声状態処理のエラー通知。
- 例: `/config set_error_channel channel:#bot-error`

### `/config clear_error_channel`

- 目的: エラー通知先を解除。
- 仕様: 未設定時はエラー通知を送らない。
- 例: `/config clear_error_channel`

### `/config set_weekly_period days:<1-31>`

- 目的: 週次ランキング通知の集計日数を設定。
- 仕様: 直近 `days` 日を対象に合算。
- 例: `/config set_weekly_period days:7`

### `/config set_weekly weekday:<0-6> time:<HH:MM>`

- 目的: 週次通知の曜日・時刻を設定。
- 仕様: `weekday` は `0=月 ... 6=日`。
- 例: `/config set_weekly weekday:6 time:21:00`

### `/config set_weekly_enabled enabled:<true|false>`

- 目的: 週次通知の有効/無効を切替。
- 仕様: 無効時は週次通知の判定自体をスキップ。
- 例: `/config set_weekly_enabled enabled:false`

### `/config move_study_to_game`

- 目的: `STUDY` 参加者を `GAME` へ即時移動。
- 使いどころ: 勉強会終了時の一括移動。
- 例: `/config move_study_to_game`

### `/config move_game_to_study`

- 目的: `GAME` と `ANYTHINGOK_VOICE` の参加者を `STUDY` へ即時移動。
- 仕様: 重複ユーザーは1回だけ移動、移動除外ロールは対象外。
- 例: `/config move_game_to_study`

### `/config dry_run`

- 目的: 実際に移動せず、移動対象人数と先頭20名を確認。
- 使いどころ: 本番実行前の対象確認。
- 例: `/config dry_run`

### `/config run_now`

- 目的: 手動で「移動 + 通知送信」を即時実行。
- 仕様: `move_game_to_study` と `send_message` を順番に実施。
- 例: `/config run_now`

## `/study` コマンド

### `/study me`

- 目的: 実行者の「今日」の勉強時間を表示。
- 仕様: 進行中セッション分もリアルタイム加算。
- 例: `/study me`

### `/study rank limit:<1-30>`

- 目的: 「今日」の勉強時間ランキングを表示。
- 仕様: 進行中セッション分を加算。`aggregation_excluded_user_ids` は表示対象外。
- 例: `/study rank`
- 例: `/study rank limit:20`

## 補足仕様

- `set_maintenance enabled:false` 実行時は、期間指定メンテナンスも即解除されます。
- 集計除外ユーザーはランキング集計だけ除外され、勉強時間の記録自体は継続されます。
