import os
import json
import datetime
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import tasks


ENV_PATH = Path(".env")


def load_env_file() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


load_env_file()


DATA_DIR = Path(os.getenv("DATA_DIR", ".")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
STUDY_DB_PATH = DATA_DIR / "study_time.db"


def get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(STUDY_DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_study_db() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS study_sessions (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                started_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS study_daily (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                study_date TEXT NOT NULL,
                seconds INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, study_date)
            )
            """
        )
        connection.commit()


def get_now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def split_seconds_by_local_date(start_utc: datetime.datetime, end_utc: datetime.datetime, timezone_name: str) -> dict[str, int]:
    if end_utc <= start_utc:
        return {}
    timezone = get_timezone(timezone_name)
    start_local = start_utc.astimezone(timezone)
    end_local = end_utc.astimezone(timezone)
    current = start_local
    segments: dict[str, int] = {}
    while current < end_local:
        next_midnight = (current + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        segment_end = min(next_midnight, end_local)
        seconds = int((segment_end - current).total_seconds())
        key = current.strftime("%Y-%m-%d")
        segments[key] = segments.get(key, 0) + max(seconds, 0)
        current = segment_end
    return segments


def add_study_seconds(guild_id: int, user_id: int, seconds_by_date: dict[str, int]) -> None:
    if not seconds_by_date:
        return
    with get_db_connection() as connection:
        for study_date, seconds in seconds_by_date.items():
            connection.execute(
                """
                INSERT INTO study_daily (guild_id, user_id, study_date, seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, study_date)
                DO UPDATE SET seconds = seconds + excluded.seconds
                """,
                (guild_id, user_id, study_date, seconds)
            )
        connection.commit()


def start_study_session(guild_id: int, user_id: int, started_at: datetime.datetime) -> None:
    started_epoch = int(started_at.timestamp())
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO study_sessions (guild_id, user_id, started_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO NOTHING
            """,
            (guild_id, user_id, started_epoch)
        )
        connection.commit()


def end_study_session(guild_id: int, user_id: int, ended_at: datetime.datetime, timezone_name: str) -> None:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT started_at FROM study_sessions WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ).fetchone()
        if row is None:
            return
        connection.execute(
            "DELETE FROM study_sessions WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        connection.commit()
    started_at = datetime.datetime.fromtimestamp(int(row["started_at"]), tz=datetime.timezone.utc)
    seconds_by_date = split_seconds_by_local_date(started_at, ended_at, timezone_name)
    add_study_seconds(guild_id, user_id, seconds_by_date)


def get_today_key(timezone_name: str) -> str:
    timezone = get_timezone(timezone_name)
    return datetime.datetime.now(timezone).strftime("%Y-%m-%d")


def get_daily_seconds(guild_id: int, user_id: int, study_date: str) -> int:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT seconds FROM study_daily WHERE guild_id = ? AND user_id = ? AND study_date = ?",
            (guild_id, user_id, study_date)
        ).fetchone()
    return int(row["seconds"]) if row else 0


def get_active_session_start(guild_id: int, user_id: int) -> datetime.datetime | None:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT started_at FROM study_sessions WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ).fetchone()
    if row is None:
        return None
    return datetime.datetime.fromtimestamp(int(row["started_at"]), tz=datetime.timezone.utc)


def get_rank_daily_seconds(guild_id: int, study_date: str) -> dict[int, int]:
    result: dict[int, int] = {}
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT user_id, seconds FROM study_daily WHERE guild_id = ? AND study_date = ?",
            (guild_id, study_date)
        ).fetchall()
    for row in rows:
        result[int(row["user_id"])] = int(row["seconds"])
    return result


def get_active_sessions(guild_id: int) -> list[tuple[int, datetime.datetime]]:
    sessions: list[tuple[int, datetime.datetime]] = []
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT user_id, started_at FROM study_sessions WHERE guild_id = ?",
            (guild_id,)
        ).fetchall()
    for row in rows:
        sessions.append(
            (
                int(row["user_id"]),
                datetime.datetime.fromtimestamp(int(row["started_at"]), tz=datetime.timezone.utc)
            )
        )
    return sessions


def format_seconds(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    sec = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def parse_ids(value: str) -> list[int]:
    parts = [item.strip() for item in value.split(",")]
    return [int(item) for item in parts if item.isdigit()]


def normalize_time(value: str) -> str:
    hour, minute = value.split(":")
    return f"{int(hour):02d}:{int(minute):02d}"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def get_guild_config(guild_id: int) -> dict:
    config = load_config()
    entry = config.get(str(guild_id), {})
    entry.setdefault("notify_time", "20:30")
    entry.setdefault("timezone", "Asia/Tokyo")
    entry.setdefault("notify_message", "20:30の通知です")
    entry.setdefault("general_channel_id", 1477655666528096440)
    entry.setdefault("game_channel_id", 1370726579021283334)
    entry.setdefault("study_channel_id", 1473956243486933145)
    entry.setdefault("target_user_ids", [])
    entry.setdefault("weekly_enabled", True)
    entry.setdefault("weekly_weekday", 6)
    entry.setdefault("weekly_time", "21:00")
    entry.setdefault("weekly_last_sent_week", "")
    return entry


def update_guild_config(guild_id: int, updates: dict) -> dict:
    config = load_config()
    entry = config.get(str(guild_id), {})
    entry.update(updates)
    config[str(guild_id)] = entry
    save_config(config)
    return entry


def get_notify_time(config: dict) -> datetime.time:
    time_value = normalize_time(config["notify_time"])
    hour, minute = time_value.split(":")
    tz = get_timezone(config["timezone"])
    return datetime.time(hour=int(hour), minute=int(minute), tzinfo=tz)


def get_timezone(name: str) -> datetime.tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Tokyo":
            return datetime.timezone(datetime.timedelta(hours=9))
        return datetime.timezone.utc


initialize_study_db()


def should_notify_now(config: dict, last_run: str | None) -> tuple[bool, str]:
    time_value = normalize_time(config["notify_time"])
    tz = get_timezone(config["timezone"])
    now = datetime.datetime.now(tz)
    key = now.strftime("%Y-%m-%d %H:%M")
    if key == last_run:
        return False, key
    if now.strftime("%H:%M") != time_value:
        return False, key
    return True, key


def get_week_key(now_local: datetime.datetime) -> str:
    iso_year, iso_week, _ = now_local.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def get_week_date_keys(now_local: datetime.datetime) -> list[str]:
    start_date = now_local.date() - datetime.timedelta(days=now_local.weekday())
    return [(start_date + datetime.timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(7)]


def should_send_weekly_now(config: dict) -> tuple[bool, str]:
    if not config.get("weekly_enabled", True):
        return False, ""
    timezone = get_timezone(config.get("timezone", "Asia/Tokyo"))
    now_local = datetime.datetime.now(timezone)
    weekly_time = normalize_time(config.get("weekly_time", "21:00"))
    weekly_weekday = int(config.get("weekly_weekday", 6))
    if now_local.weekday() != weekly_weekday:
        return False, ""
    if now_local.strftime("%H:%M") != weekly_time:
        return False, ""
    week_key = get_week_key(now_local)
    if config.get("weekly_last_sent_week", "") == week_key:
        return False, week_key
    return True, week_key


def get_weekly_totals(guild_id: int, timezone_name: str, now_utc: datetime.datetime) -> dict[int, int]:
    timezone = get_timezone(timezone_name)
    now_local = now_utc.astimezone(timezone)
    date_keys = get_week_date_keys(now_local)
    totals: dict[int, int] = {}
    with get_db_connection() as connection:
        placeholders = ",".join(["?"] * len(date_keys))
        rows = connection.execute(
            f"SELECT user_id, SUM(seconds) AS total FROM study_daily WHERE guild_id = ? AND study_date IN ({placeholders}) GROUP BY user_id",
            [guild_id, *date_keys]
        ).fetchall()
    for row in rows:
        totals[int(row["user_id"])] = int(row["total"])
    week_start_key = date_keys[0]
    week_end_key = date_keys[-1]
    for user_id, started_at in get_active_sessions(guild_id):
        extra_by_date = split_seconds_by_local_date(started_at, now_utc, timezone_name)
        extra = 0
        for date_key, seconds in extra_by_date.items():
            if week_start_key <= date_key <= week_end_key:
                extra += int(seconds)
        if extra > 0:
            totals[user_id] = totals.get(user_id, 0) + extra
    return totals


async def send_weekly_summary(guild: discord.Guild, config: dict) -> None:
    channel_id = config.get("general_channel_id")
    if not channel_id:
        return
    timezone_name = config.get("timezone", "Asia/Tokyo")
    totals = get_weekly_totals(guild.id, timezone_name, get_now_utc())
    if not totals:
        return
    ranking = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:10]
    lines = ["今週の勉強時間ランキング"]
    for index, (user_id, seconds) in enumerate(ranking, start=1):
        member = guild.get_member(user_id)
        name = member.display_name if member else str(user_id)
        lines.append(f"{index}. {name} {format_seconds(seconds)}")
    channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    await channel.send("\n".join(lines))


intents = discord.Intents.default()
intents.guilds = True
intents.members = False
intents.voice_states = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

last_run_by_guild: dict[int, str] = {}


async def move_game_to_study(guild: discord.Guild, config: dict) -> int:
    game_channel_id = config.get("game_channel_id")
    study_channel_id = config.get("study_channel_id")
    if not game_channel_id or not study_channel_id:
        return 0
    game_channel = guild.get_channel(game_channel_id) or await guild.fetch_channel(game_channel_id)
    study_channel = guild.get_channel(study_channel_id) or await guild.fetch_channel(study_channel_id)
    members = list(game_channel.members)
    target_user_ids = set(config.get("target_user_ids", []))
    if target_user_ids:
        members = [member for member in members if member.id in target_user_ids]
    for member in members:
        await member.move_to(study_channel)
    return len(members)


async def move_all_study_to_game(guild: discord.Guild, config: dict) -> int:
    game_channel_id = config.get("game_channel_id")
    study_channel_id = config.get("study_channel_id")
    if not game_channel_id or not study_channel_id:
        return 0
    study_channel = guild.get_channel(study_channel_id) or await guild.fetch_channel(study_channel_id)
    game_channel = guild.get_channel(game_channel_id) or await guild.fetch_channel(game_channel_id)
    members = list(study_channel.members)
    for member in members:
        await member.move_to(game_channel)
    return len(members)


async def send_message(guild: discord.Guild, config: dict) -> None:
    channel_id = config.get("general_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    await channel.send(config.get("notify_message", "20:30の通知です"))


@tasks.loop(minutes=1)
async def notify_loop() -> None:
    for guild in client.guilds:
        config = get_guild_config(guild.id)
        should_run, last_key = should_notify_now(config, last_run_by_guild.get(guild.id))
        last_run_by_guild[guild.id] = last_key
        if not should_run:
            send_weekly, week_key = should_send_weekly_now(config)
            if send_weekly:
                await send_weekly_summary(guild, config)
                update_guild_config(guild.id, {"weekly_last_sent_week": week_key})
            continue
        await move_game_to_study(guild, config)
        await send_message(guild, config)
        send_weekly, week_key = should_send_weekly_now(config)
        if send_weekly:
            await send_weekly_summary(guild, config)
            update_guild_config(guild.id, {"weekly_last_sent_week": week_key})


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    guild = member.guild
    config = get_guild_config(guild.id)
    study_channel_id = config.get("study_channel_id")
    if not study_channel_id:
        return
    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None
    entered_study = before_id != study_channel_id and after_id == study_channel_id
    left_study = before_id == study_channel_id and after_id != study_channel_id
    if entered_study:
        start_study_session(guild.id, member.id, get_now_utc())
    if left_study:
        end_study_session(guild.id, member.id, get_now_utc(), config.get("timezone", "Asia/Tokyo"))


@client.event
async def on_ready() -> None:
    if not notify_loop.is_running():
        notify_loop.start()
    await tree.sync()


config_group = app_commands.Group(name="config", description="ボット設定")
tree.add_command(config_group)
study_group = app_commands.Group(name="study", description="勉強時間")
tree.add_command(study_group)


def require_guild(interaction: discord.Interaction) -> int | None:
    if not interaction.guild_id:
        return None
    return interaction.guild_id


@config_group.command(name="show")
async def config_show(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    await interaction.response.send_message(
        "\n".join(
            [
                f"NOTIFY_TIME: {config['notify_time']}",
                f"TIMEZONE: {config['timezone']}",
                f"NOTIFY_MESSAGE: {config['notify_message']}",
                f"GENERAL: {config['general_channel_id']}",
                f"GAME: {config['game_channel_id']}",
                f"STUDY: {config['study_channel_id']}",
                f"TARGET_USER_IDS: {','.join(str(item) for item in config['target_user_ids'])}",
                f"WEEKLY_ENABLED: {config['weekly_enabled']}",
                f"WEEKLY_WEEKDAY: {config['weekly_weekday']}",
                f"WEEKLY_TIME: {config['weekly_time']}"
            ]
        ),
        ephemeral=True
    )


@config_group.command(name="set_general")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_general(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"general_channel_id": channel.id})
    await interaction.response.send_message("GENERALを更新しました。", ephemeral=True)


@config_group.command(name="set_game")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_game(interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"game_channel_id": channel.id})
    await interaction.response.send_message("GAMEを更新しました。", ephemeral=True)


@config_group.command(name="set_study")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_study(interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"study_channel_id": channel.id})
    await interaction.response.send_message("STUDYを更新しました。", ephemeral=True)


@config_group.command(name="set_users")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_users(interaction: discord.Interaction, users: str) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    ids = parse_ids(users)
    update_guild_config(guild_id, {"target_user_ids": ids})
    await interaction.response.send_message("対象ユーザーを更新しました。", ephemeral=True)


@config_group.command(name="set_time")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_time(interaction: discord.Interaction, time: str) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    try:
        normalize_time(time)
    except ValueError:
        await interaction.response.send_message("時刻はHH:MM形式で指定してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"notify_time": time})
    await interaction.response.send_message("通知時刻を更新しました。", ephemeral=True)


@config_group.command(name="set_timezone")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_timezone(interaction: discord.Interaction, timezone: str) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    try:
        get_timezone(timezone)
    except Exception:
        await interaction.response.send_message("タイムゾーンが不正です。", ephemeral=True)
        return
    update_guild_config(guild_id, {"timezone": timezone})
    await interaction.response.send_message("タイムゾーンを更新しました。", ephemeral=True)


@config_group.command(name="set_message")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_message(interaction: discord.Interaction, message: str) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"notify_message": message})
    await interaction.response.send_message("通知文を更新しました。", ephemeral=True)


@config_group.command(name="set_weekly")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_weekly(interaction: discord.Interaction, weekday: app_commands.Range[int, 0, 6], time: str) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    try:
        normalized_time = normalize_time(time)
    except ValueError:
        await interaction.response.send_message("時刻はHH:MM形式で指定してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"weekly_weekday": int(weekday), "weekly_time": normalized_time})
    await interaction.response.send_message("週次通知スケジュールを更新しました。", ephemeral=True)


@config_group.command(name="set_weekly_enabled")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_weekly_enabled(interaction: discord.Interaction, enabled: bool) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"weekly_enabled": enabled})
    await interaction.response.send_message("週次通知の有効/無効を更新しました。", ephemeral=True)


@config_group.command(name="move_study_to_game")
@app_commands.checks.has_permissions(administrator=True)
async def config_move_study_to_game(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("サーバー情報を取得できませんでした。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    moved = await move_all_study_to_game(guild, config)
    await interaction.response.send_message(f"{moved}人をSTUDYからGAMEへ移動しました。", ephemeral=True)


@study_group.command(name="me")
async def study_me(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    user_id = interaction.user.id
    config = get_guild_config(guild_id)
    timezone_name = config.get("timezone", "Asia/Tokyo")
    today_key = get_today_key(timezone_name)
    total_seconds = get_daily_seconds(guild_id, user_id, today_key)
    active_start = get_active_session_start(guild_id, user_id)
    if active_start is not None:
        extra_by_date = split_seconds_by_local_date(active_start, get_now_utc(), timezone_name)
        total_seconds += int(extra_by_date.get(today_key, 0))
    await interaction.response.send_message(
        f"今日の勉強時間: {format_seconds(total_seconds)}",
        ephemeral=True
    )


@study_group.command(name="rank")
async def study_rank(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 30] = 10) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("サーバー情報を取得できませんでした。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    timezone_name = config.get("timezone", "Asia/Tokyo")
    today_key = get_today_key(timezone_name)
    totals = get_rank_daily_seconds(guild_id, today_key)
    now_utc = get_now_utc()
    for user_id, started_at in get_active_sessions(guild_id):
        extra_by_date = split_seconds_by_local_date(started_at, now_utc, timezone_name)
        extra_today = int(extra_by_date.get(today_key, 0))
        totals[user_id] = totals.get(user_id, 0) + extra_today
    ranking = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    if not ranking:
        await interaction.response.send_message("今日の記録はまだありません。", ephemeral=True)
        return
    lines: list[str] = []
    for index, (user_id, seconds) in enumerate(ranking, start=1):
        member = guild.get_member(user_id)
        name = member.display_name if member else str(user_id)
        lines.append(f"{index}. {name} {format_seconds(seconds)}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


def main() -> None:
    token = get_required_env("DISCORD_TOKEN")
    if token.startswith("Bot "):
        token = token[4:]
    token = token.strip()
    client.run(token)


if __name__ == "__main__":
    main()
