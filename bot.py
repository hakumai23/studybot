import os
import json
import datetime
import asyncio
import sqlite3
import io
import re
import threading
from pathlib import Path
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import tasks
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


ENV_PATH = Path(".env")


def configure_matplotlib_font() -> None:
    preferred_fonts = [
        "Yu Gothic",
        "YuGothic",
        "Meiryo",
        "Noto Sans CJK JP",
        "Noto Sans JP",
        "IPAexGothic",
        "IPAGothic",
        "MS Gothic"
    ]
    installed_fonts = {font.name for font in font_manager.fontManager.ttflist}
    selected_font = None
    for font_name in preferred_fonts:
        if font_name in installed_fonts:
            selected_font = font_name
            break
    if selected_font:
        plt.rcParams["font.family"] = selected_font
    plt.rcParams["axes.unicode_minus"] = False


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
configure_matplotlib_font()


DATA_DIR = Path(os.getenv("DATA_DIR", ".")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
STUDY_DB_PATH = DATA_DIR / "study_time.db"
CONFIG_LOCK = threading.Lock()


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


def split_seconds_by_local_date(start_utc: datetime.datetime, end_utc: datetime.datetime, timezone_name: str, reset_time: str = "00:00") -> dict[str, int]:
    if end_utc <= start_utc:
        return {}
    timezone = get_timezone(timezone_name)
    start_local = start_utc.astimezone(timezone)
    end_local = end_utc.astimezone(timezone)
    reset_hour, reset_minute = [int(item) for item in normalize_time(reset_time).split(":")]
    reset_delta = datetime.timedelta(hours=reset_hour, minutes=reset_minute)
    current = start_local
    segments: dict[str, int] = {}
    while current < end_local:
        reset_boundary = current.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
        if reset_boundary <= current:
            reset_boundary += datetime.timedelta(days=1)
        segment_end = min(reset_boundary, end_local)
        seconds = int((segment_end - current).total_seconds())
        key = (current - reset_delta).strftime("%Y-%m-%d")
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


def end_study_session(guild_id: int, user_id: int, ended_at: datetime.datetime, timezone_name: str, reset_time: str = "00:00") -> None:
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
    seconds_by_date = split_seconds_by_local_date(started_at, ended_at, timezone_name, reset_time)
    add_study_seconds(guild_id, user_id, seconds_by_date)


def get_today_key(timezone_name: str, reset_time: str = "00:00") -> str:
    timezone = get_timezone(timezone_name)
    now_local = datetime.datetime.now(timezone)
    reset_hour, reset_minute = [int(item) for item in normalize_time(reset_time).split(":")]
    reset_delta = datetime.timedelta(hours=reset_hour, minutes=reset_minute)
    return (now_local - reset_delta).strftime("%Y-%m-%d")


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


def create_ranking_chart_image(title: str, names: list[str], seconds_list: list[int]) -> io.BytesIO:
    labels = [f"{index + 1}. {name}" for index, name in enumerate(names)]
    values = [seconds / 3600 for seconds in seconds_list]
    figure_width = max(8.0, 1.2 * len(labels) + 3.0)
    fig, ax = plt.subplots(figsize=(figure_width, 6.0))
    bars = ax.bar(labels, values, color="#2D7FF9")
    ax.set_title(title)
    ax.set_xlabel("Hours")
    ax.set_ylabel("Users")
    ax.tick_params(axis="x", labelrotation=25)
    max_value = max(values) if values else 0
    for bar, seconds in zip(bars, seconds_list):
        height = float(bar.get_height())
        text_y = height + max(max_value * 0.01, 0.02)
        ax.text(bar.get_x() + bar.get_width() / 2, text_y, format_seconds(int(seconds)), ha="center", va="bottom")
    if max_value > 0:
        ax.set_ylim(0, max_value * 1.2)
    fig.tight_layout()
    image_stream = io.BytesIO()
    fig.savefig(image_stream, format="png", dpi=150)
    plt.close(fig)
    image_stream.seek(0)
    return image_stream


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
    if "anythingok_voice_channel_id" not in entry and "general_voice_channel_id" in entry:
        entry["anythingok_voice_channel_id"] = entry.get("general_voice_channel_id")
        entry.pop("general_voice_channel_id", None)
    entry.pop("target_user_ids", None)
    entry.setdefault("notify_time", "21:00")
    entry.setdefault("timezone", "Asia/Tokyo")
    entry.setdefault("notify_message", "勉強の時間です")
    entry.setdefault("general_channel_id", 1477655666528096440)
    entry.setdefault("game_channel_id", 1370726579021283334)
    entry.setdefault("anythingok_voice_channel_id", 1370726579021283333)
    entry.setdefault("study_channel_id", 1473956243486933145)
    entry.setdefault("notify_role_id", None)
    entry.setdefault("excluded_role_ids", [])
    entry.setdefault("aggregation_excluded_user_ids", [])
    entry.setdefault("maintenance_enabled", False)
    entry.setdefault("maintenance_until_epoch", 0)
    entry.setdefault("error_channel_id", 1370726579021283331)
    entry.setdefault("reset_time", "00:00")
    entry.setdefault("weekly_enabled", True)
    entry.setdefault("weekly_weekday", 6)
    entry.setdefault("weekly_time", "21:00")
    entry.setdefault("weekly_period_days", 7)
    entry.setdefault("weekly_last_sent_week", "")
    return entry


def update_guild_config(guild_id: int, updates: dict) -> dict:
    with CONFIG_LOCK:
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


def resolve_maintenance_enabled(guild_id: int, config: dict) -> bool:
    if not config.get("maintenance_enabled", False):
        return False
    until_epoch = int(config.get("maintenance_until_epoch", 0) or 0)
    if until_epoch <= 0:
        return True
    now_epoch = int(get_now_utc().timestamp())
    if now_epoch < until_epoch:
        return True
    update_guild_config(guild_id, {"maintenance_enabled": False, "maintenance_until_epoch": 0})
    config["maintenance_enabled"] = False
    config["maintenance_until_epoch"] = 0
    return False


def get_excluded_user_id_set(config: dict) -> set[int]:
    return {int(item) for item in config.get("aggregation_excluded_user_ids", [])}


def get_week_key(now_local: datetime.datetime) -> str:
    iso_year, iso_week, _ = now_local.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def get_week_date_keys(now_local: datetime.datetime) -> list[str]:
    start_date = now_local.date() - datetime.timedelta(days=now_local.weekday())
    return [(start_date + datetime.timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(7)]


def get_period_date_keys(now_local: datetime.datetime, reset_time: str, period_days: int) -> list[str]:
    normalized_reset_time = normalize_time(reset_time)
    reset_hour, reset_minute = [int(item) for item in normalized_reset_time.split(":")]
    reset_delta = datetime.timedelta(hours=reset_hour, minutes=reset_minute)
    business_date = (now_local - reset_delta).date()
    start_date = business_date - datetime.timedelta(days=max(period_days - 1, 0))
    return [(start_date + datetime.timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(period_days)]


def get_period_range_text(now_local: datetime.datetime, reset_time: str, period_days: int) -> str:
    normalized_reset_time = normalize_time(reset_time)
    reset_hour, reset_minute = [int(item) for item in normalized_reset_time.split(":")]
    reset_delta = datetime.timedelta(hours=reset_hour, minutes=reset_minute)
    business_date = (now_local - reset_delta).date()
    start_date = business_date - datetime.timedelta(days=max(period_days - 1, 0))
    start_local = datetime.datetime.combine(start_date, datetime.time(hour=reset_hour, minute=reset_minute), tzinfo=now_local.tzinfo)
    return f"対象期間: {start_local.strftime('%Y-%m-%d %H:%M')} 〜 {now_local.strftime('%Y-%m-%d %H:%M')} ({period_days}日)"


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


def get_weekly_totals(guild_id: int, timezone_name: str, now_utc: datetime.datetime, reset_time: str, period_days: int, excluded_user_ids: set[int]) -> dict[int, int]:
    timezone = get_timezone(timezone_name)
    now_local = now_utc.astimezone(timezone)
    date_keys = get_period_date_keys(now_local, reset_time, period_days)
    totals: dict[int, int] = {}
    with get_db_connection() as connection:
        placeholders = ",".join(["?"] * len(date_keys))
        rows = connection.execute(
            f"SELECT user_id, SUM(seconds) AS total FROM study_daily WHERE guild_id = ? AND study_date IN ({placeholders}) GROUP BY user_id",
            [guild_id, *date_keys]
        ).fetchall()
    for row in rows:
        user_id = int(row["user_id"])
        if user_id in excluded_user_ids:
            continue
        totals[user_id] = int(row["total"])
    week_start_key = date_keys[0]
    week_end_key = date_keys[-1]
    for user_id, started_at in get_active_sessions(guild_id):
        if user_id in excluded_user_ids:
            continue
        extra_by_date = split_seconds_by_local_date(started_at, now_utc, timezone_name, reset_time)
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
    reset_time = config.get("reset_time", "00:00")
    period_days = int(config.get("weekly_period_days", 7))
    now_utc = get_now_utc()
    now_local = now_utc.astimezone(get_timezone(timezone_name))
    excluded_user_ids = get_excluded_user_id_set(config)
    totals = get_weekly_totals(guild.id, timezone_name, now_utc, reset_time, period_days, excluded_user_ids)
    if not totals:
        return
    ranking = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:10]
    lines = ["週次勉強時間ランキング", get_period_range_text(now_local, reset_time, period_days)]
    names: list[str] = []
    seconds_values: list[int] = []
    for index, (user_id, seconds) in enumerate(ranking, start=1):
        name = await resolve_user_display_name(guild, user_id)
        lines.append(f"{index}. {name} {format_seconds(seconds)}")
        names.append(name)
        seconds_values.append(seconds)
    channel = await resolve_guild_channel(guild, int(channel_id))
    if channel is None or not isinstance(channel, discord.abc.Messageable):
        return
    chart_stream = create_ranking_chart_image("Weekly Ranking", names, seconds_values)
    chart_file = discord.File(fp=chart_stream, filename="weekly_summary.png")
    await channel.send("\n".join(lines), file=chart_file)


intents = discord.Intents.default()
intents.guilds = True
intents.members = False
intents.voice_states = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

last_run_by_guild: dict[int, str] = {}
timer_tasks: dict[tuple[int, int], asyncio.Task] = {}


async def resolve_user_display_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    if member is not None:
        return member.display_name
    try:
        member = await guild.fetch_member(user_id)
        return member.display_name
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
    user = client.get_user(user_id)
    if user is not None:
        return user.name
    try:
        user = await client.fetch_user(user_id)
        return user.name
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return str(user_id)


def is_excluded_member(member: discord.Member, excluded_role_ids: set[int]) -> bool:
    if not excluded_role_ids:
        return False
    member_role_ids = {role.id for role in member.roles}
    return bool(member_role_ids & excluded_role_ids)


async def collect_move_members(guild: discord.Guild, config: dict) -> tuple[discord.VoiceChannel, list[discord.Member]]:
    game_channel_id = config.get("game_channel_id")
    anythingok_voice_channel_id = config.get("anythingok_voice_channel_id")
    study_channel_id = config.get("study_channel_id")
    if not game_channel_id or not study_channel_id:
        raise RuntimeError("移動元または移動先チャンネルが未設定です")
    game_channel = await resolve_guild_channel(guild, int(game_channel_id))
    if not isinstance(game_channel, discord.VoiceChannel):
        raise RuntimeError(f"game_channel_id が不正です: {game_channel_id}")
    anythingok_voice_channel = None
    if anythingok_voice_channel_id:
        resolved_anythingok = await resolve_guild_channel(guild, int(anythingok_voice_channel_id))
        if resolved_anythingok is not None and not isinstance(resolved_anythingok, discord.VoiceChannel):
            raise RuntimeError(f"anythingok_voice_channel_id が不正です: {anythingok_voice_channel_id}")
        anythingok_voice_channel = resolved_anythingok
    study_channel = await resolve_guild_channel(guild, int(study_channel_id))
    if not isinstance(study_channel, discord.VoiceChannel):
        raise RuntimeError(f"study_channel_id が不正です: {study_channel_id}")
    members: list[discord.Member] = []
    members.extend(list(game_channel.members))
    if anythingok_voice_channel is not None:
        members.extend(list(anythingok_voice_channel.members))
    unique_members: dict[int, discord.Member] = {member.id: member for member in members}
    excluded_role_ids = set(config.get("excluded_role_ids", []))
    filtered_members = [member for member in unique_members.values() if not is_excluded_member(member, excluded_role_ids)]
    return study_channel, filtered_members


async def move_game_to_study(guild: discord.Guild, config: dict) -> int:
    study_channel, members = await collect_move_members(guild, config)
    for member in members:
        await member.move_to(study_channel)
    return len(members)


async def move_all_study_to_game(guild: discord.Guild, config: dict) -> int:
    game_channel_id = config.get("game_channel_id")
    study_channel_id = config.get("study_channel_id")
    if not game_channel_id or not study_channel_id:
        return 0
    study_channel = await resolve_guild_channel(guild, int(study_channel_id))
    game_channel = await resolve_guild_channel(guild, int(game_channel_id))
    if not isinstance(study_channel, discord.VoiceChannel) or not isinstance(game_channel, discord.VoiceChannel):
        raise RuntimeError("game_channel_id または study_channel_id が不正です")
    members = list(study_channel.members)
    for member in members:
        await member.move_to(game_channel)
    return len(members)


async def send_message(guild: discord.Guild, config: dict) -> None:
    channel_id = config.get("general_channel_id")
    if not channel_id:
        return
    channel = await resolve_guild_channel(guild, int(channel_id))
    if not isinstance(channel, discord.abc.Messageable):
        raise RuntimeError(f"general_channel_id が不正です: {channel_id}")
    message = config.get("notify_message", "20:30の通知です")
    notify_role_id = config.get("notify_role_id")
    if notify_role_id:
        message = f"<@&{notify_role_id}> {message}"
    await channel.send(message, allowed_mentions=discord.AllowedMentions(roles=True))


async def notify_error(guild: discord.Guild, config: dict, error_text: str) -> None:
    error_channel_id = config.get("error_channel_id")
    if not error_channel_id:
        return
    try:
        channel = await resolve_guild_channel(guild, int(error_channel_id))
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return
        await channel.send(f"⚠️ {error_text}")
    except Exception as error:
        print(f"[notify_error] guild={guild.id} error={error}")
        return


async def resolve_guild_channel(guild: discord.Guild, channel_id: int) -> discord.abc.GuildChannel | discord.Thread | None:
    channel = guild.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        fetched = await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, discord.InvalidData):
        return None
    return fetched


@tasks.loop(minutes=1)
async def notify_loop() -> None:
    for guild in client.guilds:
        config = get_guild_config(guild.id)
        try:
            if resolve_maintenance_enabled(guild.id, config):
                continue
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
        except Exception as error:
            print(f"[notify_loop] guild={guild.id} error={error}")
            await notify_error(guild, config, f"定時処理でエラーが発生しました: {error}")


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    guild = member.guild
    config = get_guild_config(guild.id)
    try:
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
            end_study_session(guild.id, member.id, get_now_utc(), config.get("timezone", "Asia/Tokyo"), config.get("reset_time", "00:00"))
    except Exception as error:
        await notify_error(guild, config, f"音声状態更新処理でエラーが発生しました: {error}")


@client.event
async def on_ready() -> None:
    if not notify_loop.is_running():
        notify_loop.start()
    await tree.sync()


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        user_message = "管理者権限が必要です。"
    elif isinstance(error, app_commands.CheckFailure):
        user_message = "このコマンドを実行する権限がありません。"
    else:
        user_message = "コマンドの実行中にエラーが発生しました。"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(user_message, ephemeral=True)
        else:
            await interaction.response.send_message(user_message, ephemeral=True)
    except discord.HTTPException:
        pass
    guild = interaction.guild
    if guild is None:
        return
    config = get_guild_config(guild.id)
    original_error = getattr(error, "original", error)
    await notify_error(guild, config, f"コマンドエラー: {original_error}")


config_group = app_commands.Group(name="config", description="ボット設定")
tree.add_command(config_group)
study_group = app_commands.Group(name="study", description="勉強時間")
tree.add_command(study_group)
config_maintenance_group = app_commands.Group(name="maintenance", description="メンテナンス設定")
config_group.add_command(config_maintenance_group)
config_aggregate_group = app_commands.Group(name="aggregate", description="集計設定")
config_group.add_command(config_aggregate_group)


def require_guild(interaction: discord.Interaction) -> int | None:
    if not interaction.guild_id:
        return None
    return interaction.guild_id


@tree.command(name="send", description="ボイスチャンネル間でメンバーを移動します")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(from_channel="元のチャンネル", to_channel="送り先チャンネル", except_users="送らないユーザー(メンション/IDを空白かカンマ区切りで複数指定)")
async def send_voice_members(
    interaction: discord.Interaction,
    from_channel: discord.VoiceChannel,
    to_channel: discord.VoiceChannel,
    except_users: str | None = None
) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    if from_channel.id == to_channel.id:
        await interaction.response.send_message("From と To は別のボイスチャンネルを指定してください。", ephemeral=True)
        return
    members = list(from_channel.members)
    excluded_ids: set[int] = set()
    if except_users:
        tokens = [token for token in re.split(r"[\s,]+", except_users.strip()) if token]
        invalid_tokens: list[str] = []
        for token in tokens:
            mention_match = re.fullmatch(r"<@!?(\d{15,20})>", token)
            if mention_match:
                excluded_ids.add(int(mention_match.group(1)))
                continue
            id_match = re.fullmatch(r"\d{15,20}", token)
            if id_match:
                excluded_ids.add(int(token))
                continue
            invalid_tokens.append(token)
        if invalid_tokens:
            await interaction.response.send_message(
                "Except の形式が不正です。メンションまたはユーザーIDを空白かカンマ区切りで指定してください。",
                ephemeral=True
            )
            return
    if excluded_ids:
        members = [member for member in members if member.id not in excluded_ids]
    if not members:
        await interaction.response.send_message("移動対象メンバーがいません。", ephemeral=True)
        return
    moved = 0
    failed_count = 0
    for member in members:
        try:
            await member.move_to(to_channel)
            moved += 1
        except (discord.Forbidden, discord.HTTPException):
            failed_count += 1
    result_message = f"{moved}人を {from_channel.mention} から {to_channel.mention} へ移動しました。"
    if failed_count > 0:
        result_message += f" 失敗: {failed_count}人"
    if excluded_ids:
        result_message += f" 除外指定: {len(excluded_ids)}人"
    await interaction.response.send_message(result_message, ephemeral=True)


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
                f"NOTIFY_ROLE_ID: {config['notify_role_id']}",
                f"GENERAL: {config['general_channel_id']}",
                f"GAME: {config['game_channel_id']}",
                f"ANYTHINGOK_VOICE: {config['anythingok_voice_channel_id']}",
                f"STUDY: {config['study_channel_id']}",
                f"EXCLUDED_ROLE_IDS: {','.join(str(item) for item in config['excluded_role_ids'])}",
                f"AGGREGATION_EXCLUDED_USER_IDS: {','.join(str(item) for item in config['aggregation_excluded_user_ids'])}",
                f"MAINTENANCE_ENABLED: {config['maintenance_enabled']}",
                f"MAINTENANCE_UNTIL_EPOCH: {config['maintenance_until_epoch']}",
                f"ERROR_CHANNEL_ID: {config['error_channel_id']}",
                f"RESET_TIME: {config['reset_time']}",
                f"WEEKLY_ENABLED: {config['weekly_enabled']}",
                f"WEEKLY_WEEKDAY: {config['weekly_weekday']}",
                f"WEEKLY_TIME: {config['weekly_time']}",
                f"WEEKLY_PERIOD_DAYS: {config['weekly_period_days']}"
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


@config_group.command(name="set_anythingok_voice")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_anythingok_voice(interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"anythingok_voice_channel_id": channel.id})
    await interaction.response.send_message("ANYTHINGOK_VOICEを更新しました。", ephemeral=True)


@config_group.command(name="set_study")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_study(interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"study_channel_id": channel.id})
    await interaction.response.send_message("STUDYを更新しました。", ephemeral=True)


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


@config_group.command(name="set_reset_time")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_reset_time(interaction: discord.Interaction, time: str) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    try:
        normalized_time = normalize_time(time)
    except ValueError:
        await interaction.response.send_message("時刻はHH:MM形式で指定してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"reset_time": normalized_time})
    await interaction.response.send_message("日次リセット時刻を更新しました。", ephemeral=True)


@config_group.command(name="set_notify_role")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_notify_role(interaction: discord.Interaction, role: discord.Role) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"notify_role_id": role.id})
    await interaction.response.send_message("通知メンションロールを更新しました。", ephemeral=True)


@config_group.command(name="clear_notify_role")
@app_commands.checks.has_permissions(administrator=True)
async def config_clear_notify_role(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"notify_role_id": None})
    await interaction.response.send_message("通知メンションロールを解除しました。", ephemeral=True)


@config_group.command(name="add_exclude_role")
@app_commands.checks.has_permissions(administrator=True)
async def config_add_exclude_role(interaction: discord.Interaction, role: discord.Role) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    excluded_role_ids = list(config.get("excluded_role_ids", []))
    if role.id not in excluded_role_ids:
        excluded_role_ids.append(role.id)
    update_guild_config(guild_id, {"excluded_role_ids": excluded_role_ids})
    await interaction.response.send_message("移動除外ロールを追加しました。", ephemeral=True)


@config_group.command(name="remove_exclude_role")
@app_commands.checks.has_permissions(administrator=True)
async def config_remove_exclude_role(interaction: discord.Interaction, role: discord.Role) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    excluded_role_ids = [item for item in config.get("excluded_role_ids", []) if int(item) != role.id]
    update_guild_config(guild_id, {"excluded_role_ids": excluded_role_ids})
    await interaction.response.send_message("移動除外ロールを削除しました。", ephemeral=True)


@config_group.command(name="clear_exclude_roles")
@app_commands.checks.has_permissions(administrator=True)
async def config_clear_exclude_roles(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"excluded_role_ids": []})
    await interaction.response.send_message("移動除外ロールをクリアしました。", ephemeral=True)


@config_maintenance_group.command(name="set")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_maintenance(interaction: discord.Interaction, enabled: bool) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"maintenance_enabled": enabled, "maintenance_until_epoch": 0})
    await interaction.response.send_message("メンテ停止スイッチを更新しました。", ephemeral=True)


@config_maintenance_group.command(name="set_for")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_maintenance_for(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 10080]) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    timezone_name = config.get("timezone", "Asia/Tokyo")
    now_utc = get_now_utc()
    until_utc = now_utc + datetime.timedelta(minutes=int(minutes))
    until_epoch = int(until_utc.timestamp())
    until_local = until_utc.astimezone(get_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")
    update_guild_config(guild_id, {"maintenance_enabled": True, "maintenance_until_epoch": until_epoch})
    await interaction.response.send_message(
        f"メンテナンスモードを有効化しました。終了予定: {until_local} ({timezone_name})",
        ephemeral=True
    )


@config_aggregate_group.command(name="add_exclude_user")
@app_commands.checks.has_permissions(administrator=True)
async def config_add_exclude_user(interaction: discord.Interaction, user: discord.Member) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    excluded_user_ids = [int(item) for item in config.get("aggregation_excluded_user_ids", [])]
    if user.id not in excluded_user_ids:
        excluded_user_ids.append(user.id)
    update_guild_config(guild_id, {"aggregation_excluded_user_ids": excluded_user_ids})
    await interaction.response.send_message("集計除外ユーザーを追加しました。", ephemeral=True)


@config_aggregate_group.command(name="remove_exclude_user")
@app_commands.checks.has_permissions(administrator=True)
async def config_remove_exclude_user(interaction: discord.Interaction, user: discord.Member) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    excluded_user_ids = [int(item) for item in config.get("aggregation_excluded_user_ids", []) if int(item) != user.id]
    update_guild_config(guild_id, {"aggregation_excluded_user_ids": excluded_user_ids})
    await interaction.response.send_message("集計除外ユーザーを削除しました。", ephemeral=True)


@config_aggregate_group.command(name="clear_exclude_users")
@app_commands.checks.has_permissions(administrator=True)
async def config_clear_exclude_users(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"aggregation_excluded_user_ids": []})
    await interaction.response.send_message("集計除外ユーザーをクリアしました。", ephemeral=True)


@config_group.command(name="set_error_channel")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_error_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"error_channel_id": channel.id})
    await interaction.response.send_message("エラー通知先チャンネルを更新しました。", ephemeral=True)


@config_group.command(name="clear_error_channel")
@app_commands.checks.has_permissions(administrator=True)
async def config_clear_error_channel(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"error_channel_id": None})
    await interaction.response.send_message("エラー通知先チャンネルを解除しました。", ephemeral=True)


@config_group.command(name="set_weekly_period")
@app_commands.checks.has_permissions(administrator=True)
async def config_set_weekly_period(interaction: discord.Interaction, days: app_commands.Range[int, 1, 31]) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    update_guild_config(guild_id, {"weekly_period_days": int(days)})
    await interaction.response.send_message("週次ランキング対象日数を更新しました。", ephemeral=True)


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


@config_group.command(name="move_game_to_study")
@app_commands.checks.has_permissions(administrator=True)
async def config_move_game_to_study(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("サーバー情報を取得できませんでした。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    try:
        moved = await move_game_to_study(guild, config)
    except Exception as error:
        await interaction.response.send_message(f"実行に失敗しました: {error}", ephemeral=True)
        return
    await interaction.response.send_message(f"{moved}人をGAME/ANYTHINGOK_VOICEからSTUDYへ移動しました。", ephemeral=True)


@config_group.command(name="dry_run")
@app_commands.checks.has_permissions(administrator=True)
async def config_dry_run(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("サーバー情報を取得できませんでした。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    try:
        study_channel, members = await collect_move_members(guild, config)
    except Exception as error:
        await interaction.response.send_message(f"実行に失敗しました: {error}", ephemeral=True)
        return
    member_names = [member.display_name for member in members[:20]]
    lines = [
        f"移動先: {study_channel.name}",
        f"移動対象人数: {len(members)}人",
        f"除外ロール数: {len(config.get('excluded_role_ids', []))}"
    ]
    if member_names:
        lines.append("対象メンバー(先頭20人): " + ", ".join(member_names))
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@config_group.command(name="run_now")
@app_commands.checks.has_permissions(administrator=True)
async def config_run_now(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("サーバー情報を取得できませんでした。", ephemeral=True)
        return
    config = get_guild_config(guild_id)
    try:
        moved = await move_game_to_study(guild, config)
        await send_message(guild, config)
    except Exception as error:
        await interaction.response.send_message(f"実行に失敗しました: {error}", ephemeral=True)
        return
    await interaction.response.send_message(f"手動実行しました。移動: {moved}人、通知送信: 1件", ephemeral=True)


@study_group.command(name="me")
async def study_me(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    user_id = interaction.user.id
    config = get_guild_config(guild_id)
    timezone_name = config.get("timezone", "Asia/Tokyo")
    reset_time = config.get("reset_time", "00:00")
    today_key = get_today_key(timezone_name, reset_time)
    total_seconds = get_daily_seconds(guild_id, user_id, today_key)
    active_start = get_active_session_start(guild_id, user_id)
    if active_start is not None:
        extra_by_date = split_seconds_by_local_date(active_start, get_now_utc(), timezone_name, reset_time)
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
    reset_time = config.get("reset_time", "00:00")
    today_key = get_today_key(timezone_name, reset_time)
    totals = get_rank_daily_seconds(guild_id, today_key)
    excluded_user_ids = get_excluded_user_id_set(config)
    for user_id in list(totals.keys()):
        if user_id in excluded_user_ids:
            totals.pop(user_id, None)
    now_utc = get_now_utc()
    for user_id, started_at in get_active_sessions(guild_id):
        if user_id in excluded_user_ids:
            continue
        extra_by_date = split_seconds_by_local_date(started_at, now_utc, timezone_name, reset_time)
        extra_today = int(extra_by_date.get(today_key, 0))
        totals[user_id] = totals.get(user_id, 0) + extra_today
    ranking = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    if not ranking:
        await interaction.response.send_message("今日の記録はまだありません。", ephemeral=True)
        return
    lines: list[str] = []
    names: list[str] = []
    seconds_values: list[int] = []
    for index, (user_id, seconds) in enumerate(ranking, start=1):
        name = await resolve_user_display_name(guild, user_id)
        lines.append(f"{index}. {name} {format_seconds(seconds)}")
        names.append(name)
        seconds_values.append(seconds)
    chart_stream = create_ranking_chart_image("Daily Ranking", names, seconds_values)
    chart_file = discord.File(fp=chart_stream, filename="daily_rank.png")
    await interaction.response.send_message("\n".join(lines), ephemeral=True, file=chart_file)


@study_group.command(name="weekly_rank")
async def study_weekly_rank(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 30] = 10) -> None:
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
    reset_time = config.get("reset_time", "00:00")
    period_days = int(config.get("weekly_period_days", 7))
    now_utc = get_now_utc()
    now_local = now_utc.astimezone(get_timezone(timezone_name))
    excluded_user_ids = get_excluded_user_id_set(config)
    totals = get_weekly_totals(guild_id, timezone_name, now_utc, reset_time, period_days, excluded_user_ids)
    ranking = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    if not ranking:
        await interaction.response.send_message(f"直近{period_days}日の記録はまだありません。", ephemeral=True)
        return
    lines = ["週次勉強時間ランキング", get_period_range_text(now_local, reset_time, period_days)]
    names: list[str] = []
    seconds_values: list[int] = []
    for index, (user_id, seconds) in enumerate(ranking, start=1):
        name = await resolve_user_display_name(guild, user_id)
        lines.append(f"{index}. {name} {format_seconds(seconds)}")
        names.append(name)
        seconds_values.append(seconds)
    chart_stream = create_ranking_chart_image("Weekly Ranking", names, seconds_values)
    chart_file = discord.File(fp=chart_stream, filename="weekly_rank.png")
    await interaction.response.send_message("\n".join(lines), ephemeral=True, file=chart_file)


@study_group.command(name="timer")
async def study_timer(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 720], title: str = "タイマー終了") -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    invoke_channel_id = interaction.channel_id
    if not invoke_channel_id:
        await interaction.response.send_message("通知先チャンネルを取得できませんでした。", ephemeral=True)
        return
    timer_key = (guild_id, interaction.user.id)
    existing_task = timer_tasks.get(timer_key)
    if existing_task is not None and not existing_task.done():
        await interaction.response.send_message("すでにタイマーが動作中です。先に `/study timer_cancel` を実行してください。", ephemeral=True)
        return
    seconds = int(minutes) * 60

    async def run_timer() -> None:
        try:
            await asyncio.sleep(seconds)
            guild = interaction.guild
            if guild is None:
                return
            target_channel = await resolve_guild_channel(guild, int(invoke_channel_id))
            if target_channel is None or not isinstance(target_channel, discord.abc.Messageable):
                return
            try:
                await target_channel.send(f"{interaction.user.mention} {title}（{int(minutes)}分）")
            except (discord.Forbidden, discord.HTTPException) as error:
                config = get_guild_config(guild.id)
                await notify_error(guild, config, f"タイマー通知に失敗しました: {error}")
        finally:
            timer_tasks.pop(timer_key, None)

    timer_tasks[timer_key] = asyncio.create_task(run_timer())
    await interaction.response.send_message(
        f"タイマーを開始しました。{int(minutes)}分後に <#{int(invoke_channel_id)}> へ通知します。"
    )


@study_group.command(name="timer_cancel")
async def study_timer_cancel(interaction: discord.Interaction) -> None:
    guild_id = require_guild(interaction)
    if not guild_id:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    timer_key = (guild_id, interaction.user.id)
    task = timer_tasks.get(timer_key)
    if task is None or task.done():
        timer_tasks.pop(timer_key, None)
        await interaction.response.send_message("停止できるタイマーはありません。", ephemeral=True)
        return
    task.cancel()
    timer_tasks.pop(timer_key, None)
    await interaction.response.send_message("タイマーを停止しました。", ephemeral=True)


def main() -> None:
    token = get_required_env("DISCORD_TOKEN")
    if token.startswith("Bot "):
        token = token[4:]
    token = token.strip()
    client.run(token)


if __name__ == "__main__":
    main()
