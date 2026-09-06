import asyncio
from collections import deque
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import discord
from discord import app_commands
from discord.ext import commands
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("diskzokej")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
COMMAND_ALIASES_FILE = BASE_DIR / "command_aliases.json"
DEFAULT_CONFIG = {
    "command_prefix": "!",
    "slash_command_guild_ids": [],
    "default_volume_percent": 100,
    "idle_disconnect_timeout": 300,
    "playback_start_timeout": 15,
    "bot_message_delete_after_seconds": 600,
    "direct_media_suffixes": [
        ".aac",
        ".flac",
        ".m3u",
        ".m3u8",
        ".m4a",
        ".mp3",
        ".mp4",
        ".ogg",
        ".oga",
        ".opus",
        ".wav",
        ".webm",
    ],
    "ytdl_options": {
        "format": "bestaudio/best",
        "noplaylist": True,
        "default_search": "ytsearch1",
        "quiet": True,
        "no_warnings": True,
    },
    "fallback_ytdl_options": {
        "format": "best",
    },
}
FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_at_eof 1 "
        "-reconnect_on_network_error 1 "
        "-reconnect_on_http_error 4xx,5xx "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}


def merge_nested_dicts(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_nested_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)

    try:
        raw_data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Soubor s konfiguraci neni citelny: %s", CONFIG_FILE)
        return dict(DEFAULT_CONFIG)

    if not isinstance(raw_data, dict):
        LOGGER.warning("Konfigurace musi byt JSON objekt: %s", CONFIG_FILE)
        return dict(DEFAULT_CONFIG)

    return merge_nested_dicts(DEFAULT_CONFIG, raw_data)


def normalize_command_prefix(value: Any) -> str:
    if not isinstance(value, str):
        LOGGER.warning("command_prefix musi byt text, pouzivam vychozi hodnotu.")
        return str(DEFAULT_CONFIG["command_prefix"])

    if value == "":
        LOGGER.warning("command_prefix nesmi byt prazdny, pouzivam vychozi hodnotu.")
        return str(DEFAULT_CONFIG["command_prefix"])

    return value


def parse_guild_id_list(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        LOGGER.warning("slash_command_guild_ids musi byt seznam cisel.")
        return []

    guild_ids: list[int] = []
    for raw_item in value:
        try:
            guild_id = int(raw_item)
        except (TypeError, ValueError):
            LOGGER.warning("Ignoruju neplatne guild ID pro slash commandy: %r", raw_item)
            continue
        if guild_id > 0:
            guild_ids.append(guild_id)

    return guild_ids


CONFIG = load_config()
COMMAND_PREFIX = normalize_command_prefix(
    CONFIG.get("command_prefix", DEFAULT_CONFIG["command_prefix"])
)
SLASH_COMMAND_GUILD_IDS = parse_guild_id_list(
    CONFIG.get("slash_command_guild_ids", DEFAULT_CONFIG["slash_command_guild_ids"])
)
IDLE_DISCONNECT_TIMEOUT = int(
    CONFIG.get("idle_disconnect_timeout", DEFAULT_CONFIG["idle_disconnect_timeout"])
)
PLAYBACK_START_TIMEOUT = int(
    CONFIG.get("playback_start_timeout", DEFAULT_CONFIG["playback_start_timeout"])
)
BOT_MESSAGE_DELETE_AFTER_SECONDS = max(
    0,
    int(
        CONFIG.get(
            "bot_message_delete_after_seconds",
            DEFAULT_CONFIG["bot_message_delete_after_seconds"],
        )
    ),
)
DEFAULT_VOLUME_PERCENT = max(
    0,
    min(200, int(CONFIG.get("default_volume_percent", DEFAULT_CONFIG["default_volume_percent"]))),
)
DIRECT_MEDIA_SUFFIXES = {
    str(suffix).lower()
    for suffix in CONFIG.get("direct_media_suffixes", DEFAULT_CONFIG["direct_media_suffixes"])
}
YTDL_OPTIONS = dict(CONFIG.get("ytdl_options", DEFAULT_CONFIG["ytdl_options"]))
FALLBACK_YTDL_OPTIONS = {
    **YTDL_OPTIONS,
    **dict(CONFIG.get("fallback_ytdl_options", DEFAULT_CONFIG["fallback_ytdl_options"])),
}
CANONICAL_COMMANDS = (
    "play",
    "radio",
    "radios",
    "pause",
    "resume",
    "skip",
    "stop",
    "queue",
    "leave",
    "np",
    "help",
)


def looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def clean_http_headers(headers: Any) -> Dict[str, str]:
    if not isinstance(headers, dict):
        return {}

    clean_headers: Dict[str, str] = {}
    for raw_key, raw_value in headers.items():
        if raw_value is None:
            continue
        key = str(raw_key).strip()
        value = str(raw_value).replace("\r", " ").replace("\n", " ").strip()
        if key and value:
            clean_headers[key] = value
    return clean_headers


def build_ffmpeg_before_options(track: "Track") -> str:
    before_options = FFMPEG_OPTIONS["before_options"]
    if not track.http_headers:
        return before_options

    header_text = "".join(
        f"{key}: {value}\r\n"
        for key, value in track.http_headers.items()
    )
    escaped_header_text = header_text.replace('"', r"\"")
    return f'{before_options} -headers "{escaped_header_text}"'


def should_refresh_track_before_playback(track: "Track") -> bool:
    return is_youtube_url(track.webpage_url)


def should_pipe_track_through_ytdlp(track: "Track") -> bool:
    return is_youtube_url(track.webpage_url)


def create_ytdlp_pipe_process(track: "Track") -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--quiet",
            "--no-warnings",
            "--no-playlist",
            "--format",
            "bestaudio/best",
            "--output",
            "-",
            "--",
            track.webpage_url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def stop_helper_process(process: Optional[subprocess.Popen]) -> None:
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def create_audio_source_handle(track: "Track") -> "AudioSourceHandle":
    ffmpeg_options = dict(FFMPEG_OPTIONS)
    if should_pipe_track_through_ytdlp(track):
        helper_process = create_ytdlp_pipe_process(track)
        if helper_process.stdout is None:
            stop_helper_process(helper_process)
            raise commands.CommandError("Nepodarilo se spustit yt-dlp audio pipe.")

        source = discord.FFmpegPCMAudio(
            helper_process.stdout,
            pipe=True,
            executable=FFMPEG_EXECUTABLE,
            **ffmpeg_options,
        )
        return AudioSourceHandle(source=source, helper_process=helper_process)

    ffmpeg_options["before_options"] = build_ffmpeg_before_options(track)
    source = discord.FFmpegPCMAudio(
        track.stream_url,
        executable=FFMPEG_EXECUTABLE,
        **ffmpeg_options,
    )
    return AudioSourceHandle(source=source)


def bot_message_kwargs() -> Dict[str, int]:
    if BOT_MESSAGE_DELETE_AFTER_SECONDS <= 0:
        return {}
    return {"delete_after": BOT_MESSAGE_DELETE_AFTER_SECONDS}


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    requested_by: str
    source_name: str
    http_headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class AudioSourceHandle:
    source: Any
    helper_process: Optional[subprocess.Popen] = None

def find_ffmpeg_executable() -> Optional[str]:
    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        return path_ffmpeg

    local_ffmpeg = Path(__file__).resolve().parent / "ffmpeg.exe"
    if local_ffmpeg.exists():
        return str(local_ffmpeg)

    winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_root.exists():
        matches = sorted(winget_root.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"), reverse=True)
        if matches:
            return str(matches[0])

    return None


FFMPEG_EXECUTABLE = find_ffmpeg_executable()


def load_command_aliases() -> Dict[str, list[str]]:
    aliases_by_command = {command_name: [] for command_name in CANONICAL_COMMANDS}

    if not COMMAND_ALIASES_FILE.exists():
        return aliases_by_command

    try:
        raw_data = json.loads(COMMAND_ALIASES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Soubor s aliasy prikazu neni citelny: %s", COMMAND_ALIASES_FILE)
        return aliases_by_command

    if not isinstance(raw_data, dict):
        LOGGER.warning("Aliasy prikazu musi byt JSON objekt: %s", COMMAND_ALIASES_FILE)
        return aliases_by_command

    used_names = set(CANONICAL_COMMANDS)
    for command_name, raw_aliases in raw_data.items():
        if command_name not in aliases_by_command:
            LOGGER.warning("Ignoruju aliasy pro neznamy prikaz `%s`.", command_name)
            continue
        if not isinstance(raw_aliases, list):
            LOGGER.warning("Aliasy prikazu `%s` musi byt seznam textu.", command_name)
            continue

        normalized_aliases: list[str] = []
        for raw_alias in raw_aliases:
            if not isinstance(raw_alias, str):
                LOGGER.warning("Ignoruju netextovy alias u prikazu `%s`.", command_name)
                continue

            alias = raw_alias.strip().lower()
            if not alias:
                continue
            if " " in alias:
                LOGGER.warning("Ignoruju alias s mezerou `%s` u prikazu `%s`.", raw_alias, command_name)
                continue
            if alias in used_names:
                LOGGER.warning(
                    "Ignoruju duplicitni alias `%s`, uz je pouzity jako prikaz nebo jiny alias.",
                    raw_alias,
                )
                continue

            used_names.add(alias)
            normalized_aliases.append(alias)

        aliases_by_command[command_name] = normalized_aliases

    return aliases_by_command


COMMAND_ALIASES = load_command_aliases()


def get_command_aliases(command_name: str) -> list[str]:
    return list(COMMAND_ALIASES.get(command_name, []))


def format_command_label(command_name: str) -> str:
    aliases = get_command_aliases(command_name)
    primary = f"{COMMAND_PREFIX}{command_name}"
    if not aliases:
        return primary
    alias_list = ", ".join(f"{COMMAND_PREFIX}{alias}" for alias in aliases)
    return f"{primary} (aliasy: {alias_list})"


def format_slash_label(command_name: str) -> str:
    return f"/{command_name}"


def build_help_text() -> str:
    return (
        "Napoveda k botovi:\n"
        f"`{format_command_label('play')} <odkaz nebo hledany text>` nebo `{format_slash_label('play')}` - prida skladbu, audio, nebo cely YouTube playlist do fronty. Text se hleda na YouTube.\n"
        f"`{format_command_label('radio')} <stream_url> [alias]` nebo `{format_slash_label('radio')}` - prida radio stream do fronty a volitelne ulozi alias.\n"
        f"`{COMMAND_PREFIX}radio <alias>` - spusti drive ulozene radio podle aliasu.\n"
        f"`{format_command_label('radios')}` nebo `{format_slash_label('radios')}` - vypise vsechna ulozena radia a jejich adresy.\n"
        f"`{format_command_label('pause')}` nebo `{format_slash_label('pause')}` - pozastavi prehravani.\n"
        f"`{format_command_label('resume')}` nebo `{format_slash_label('resume')}` - obnovi prehravani.\n"
        f"`{format_command_label('skip')}` nebo `{format_slash_label('skip')}` - preskoci aktualne prehravanou polozku.\n"
        f"`{format_command_label('stop')}` nebo `{format_slash_label('stop')}` - zastavi prehravani a vymaze cekajici frontu.\n"
        f"`{format_command_label('queue')}` nebo `{format_slash_label('queue')}` - ukaze, co prave hraje a co je jeste ve fronte.\n"
        f"`{format_command_label('np')}` nebo `{format_slash_label('np')}` - ukaze aktualne prehravanou skladbu nebo stream.\n"
        f"`{format_command_label('leave')}` nebo `{format_slash_label('leave')}` - odpoji bota z hlasoveho kanalu.\n"
        f"`{format_command_label('help')}` nebo `{format_slash_label('help')}` - ukaze tuhle napovedu.\n"
        f"Aktivni prefix: `{COMMAND_PREFIX}`\n"
        "Priklady:\n"
        f"`{COMMAND_PREFIX}play never gonna give you up`\n"
        f"`{COMMAND_PREFIX}play https://www.youtube.com/playlist?list=...`\n"
        f"`{COMMAND_PREFIX}radio https://stream.example.com/live.mp3 beat`\n"
        "`/play query:never gonna give you up`"
    )


class GuildPlayer:
    def __init__(self, bot: commands.Bot, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.voice_client: Optional[discord.VoiceClient] = None
        self.current: Optional[Track] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self.current_source = None
        self.volume = DEFAULT_VOLUME_PERCENT / 100.0
        self.history: deque[str] = deque(maxlen=3)
        self.player_task = bot.loop.create_task(self.player_loop())

    async def send_status(self, message: str) -> None:
        await send_status_update(self.guild, message, preferred_channel=self.text_channel)

    def _finish_event(self, finished: asyncio.Event) -> None:
        self.bot.loop.call_soon_threadsafe(finished.set)

    def has_human_listeners(self) -> bool:
        if not self.voice_client or not self.voice_client.channel:
            return False
        return any(not member.bot for member in self.voice_client.channel.members)

    async def disconnect_voice(self) -> None:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
        self.voice_client = None
        self.current_source = None

    def set_volume(self, volume: float) -> int:
        self.volume = max(0.0, min(2.0, volume))
        source = getattr(self.voice_client, "source", None)
        if source is not None and hasattr(source, "volume"):
            source.volume = self.volume
        return int(round(self.volume * 100))

    def change_volume(self, delta: float) -> int:
        return self.set_volume(self.volume + delta)

    async def wait_for_playback_start(
        self,
        track: Track,
        finished: asyncio.Event,
    ) -> bool:
        for _ in range(PLAYBACK_START_TIMEOUT * 2):
            if finished.is_set():
                return False
            if self.voice_client and self.voice_client.is_playing():
                return True
            await asyncio.sleep(0.5)

        LOGGER.warning("Stream se nerozbehl vcas na %s: %s", self.guild.name, track.webpage_url)
        await self.send_status(
            f"Nepodarilo se rozbehnout stream: **{track.title}**. Preskakuju na dalsi polozku."
        )
        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.stop()
        self._finish_event(finished)
        return False

    async def player_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            while not self.bot.is_closed():
                track: Optional[Track] = None
                source_handle: Optional[AudioSourceHandle] = None
                try:
                    track = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=IDLE_DISCONNECT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    if self.current is None and self.queue.empty() and not self.has_human_listeners():
                        LOGGER.info(
                            "Odpojuju guild %s po %s s neaktivity bez posluchacu",
                            self.guild.name,
                            IDLE_DISCONNECT_TIMEOUT,
                        )
                        await self.send_status("Ve voice kanalu nikdo neni, odpojuju se po neaktivite.")
                        await self.disconnect_voice()
                        return
                    continue
                try:
                    await self.send_status(f"Pripravuju prehravani: **{track.title}**\n{track.webpage_url}")
                    if should_refresh_track_before_playback(track) and not should_pipe_track_through_ytdlp(track):
                        await self.send_status(f"Obnovuju YouTube audio stream: **{track.title}**")
                        track = await refresh_track_before_playback(track)

                    self.current = track

                    finished = asyncio.Event()
                    playback_error: Optional[Exception] = None

                    def after_playback(error: Optional[Exception]) -> None:
                        nonlocal playback_error
                        if error:
                            playback_error = error
                            LOGGER.exception("Chyba pri prehravani na %s", self.guild.name, exc_info=error)
                        self._finish_event(finished)

                    if not FFMPEG_EXECUTABLE:
                        raise commands.CommandError(
                            "FFmpeg nebyl nalezen. Nainstaluj ho a over prikaz `ffmpeg -version`."
                        )
                    if not self.voice_client or not self.voice_client.is_connected():
                        raise commands.CommandError(
                            "Bot uz neni pripojeny do hlasoveho kanalu. Pripoj ho znovu prikazem `!play` nebo `!radio`."
                        )

                    if should_pipe_track_through_ytdlp(track):
                        await self.send_status(f"Spoustim YouTube audio pres yt-dlp a FFmpeg: **{track.title}**")
                    else:
                        await self.send_status(f"Spoustim audio stream pres FFmpeg: **{track.title}**")
                    source_handle = create_audio_source_handle(track)
                    self.current_source = discord.PCMVolumeTransformer(source_handle.source, volume=self.volume)
                    self.voice_client.play(self.current_source, after=after_playback)
                    playback_started = await self.wait_for_playback_start(track, finished)
                    if playback_started:
                        await self.send_status(f"Prehravani bezi: **{track.title}**\n{track.webpage_url}")
                    await finished.wait()
                    if playback_error:
                        await self.send_status(
                            f"Audio stream skoncil chybou: **{track.title}**. Preskakuju na dalsi polozku."
                        )
                except asyncio.CancelledError:
                    LOGGER.info("Ukoncuji prehravaci smycku pro guild %s", self.guild.name)
                    raise
                except FileNotFoundError as error:
                    LOGGER.exception("FFmpeg nebyl nalezen", exc_info=error)
                    await self.send_status(
                        "Nepodarilo se spustit FFmpeg. Over, ze je nainstalovany a dostupny."
                    )
                except discord.ClientException as error:
                    LOGGER.exception("Discord voice chyba", exc_info=error)
                    await self.send_status(f"Prehravani selhalo: {error}")
                except commands.CommandError as error:
                    await self.send_status(str(error))
                except Exception as error:
                    LOGGER.exception("Neocekavana chyba v player_loop na %s", self.guild.name, exc_info=error)
                    await self.send_status(
                        f"Prehravani spadlo u polozky **{track.title}**. Preskakuju na dalsi."
                    )
                finally:
                    if self.voice_client and self.voice_client.is_playing():
                        self.voice_client.stop()
                    if source_handle is not None:
                        stop_helper_process(source_handle.helper_process)
                    if track is not None:
                        self.history.appendleft(track.title)
                    self.current_source = None
                    self.current = None
                    if track is not None:
                        self.queue.task_done()
        finally:
            players.pop(self.guild.id, None)

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel != channel:
                await self.voice_client.move_to(channel)
            return self.voice_client

        self.voice_client = await channel.connect(self_deaf=True)
        return self.voice_client

    async def cleanup(self) -> None:
        self.player_task.cancel()
        await self.disconnect_voice()
        self.current = None
        self._drain_queue()
        try:
            await self.player_task
        except asyncio.CancelledError:
            pass

    def _drain_queue(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True,
)
players: Dict[int, GuildPlayer] = {}
tree_synced = False
ytdl = YoutubeDL(YTDL_OPTIONS)
fallback_ytdl = YoutubeDL(
    {
        **YTDL_OPTIONS,
        "format": "best",
    }
)
RADIO_ALIASES_FILE = BASE_DIR / "radio_aliases.json"


def get_player(guild: discord.Guild) -> GuildPlayer:
    player = players.get(guild.id)
    if player is None:
        player = GuildPlayer(bot, guild)
        players[guild.id] = player
    return player


def require_guild(ctx: commands.Context) -> discord.Guild:
    if ctx.guild is None:
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    return ctx.guild


def read_token_from_file(file_path: Path) -> Optional[str]:
    if not file_path.exists():
        return None
    token = file_path.read_text(encoding="utf-8").strip()
    return token or None


def read_token_from_env_file(file_path: Path) -> Optional[str]:
    if not file_path.exists():
        return None

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DISCORD_TOKEN":
            return value.strip().strip("\"'")
    return None


def load_discord_token() -> str:
    token = os.getenv("DISCORD_TOKEN")
    if token:
        return token

    env_token = read_token_from_env_file(BASE_DIR / ".env")
    if env_token:
        return env_token

    file_token = read_token_from_file(BASE_DIR / "token.txt")
    if file_token:
        return file_token

    raise RuntimeError(
        "Discord token nebyl nalezen. Pouzij promennou DISCORD_TOKEN, soubor .env, nebo token.txt."
    )


def load_radio_aliases() -> Dict[str, str]:
    if not RADIO_ALIASES_FILE.exists():
        return {}

    try:
        raw_data = json.loads(RADIO_ALIASES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Soubor s aliasy radii neni citelny: %s", RADIO_ALIASES_FILE)
        return {}

    if not isinstance(raw_data, dict):
        return {}

    aliases: Dict[str, str] = {}
    for alias, stream_url in raw_data.items():
        if isinstance(alias, str) and isinstance(stream_url, str):
            aliases[alias.lower()] = stream_url
    return aliases


def save_radio_aliases(aliases: Dict[str, str]) -> None:
    RADIO_ALIASES_FILE.write_text(
        json.dumps(dict(sorted(aliases.items())), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


radio_aliases = load_radio_aliases()


async def run_blocking(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args))


def is_direct_media_url(value: str) -> bool:
    if not looks_like_url(value):
        return False
    parsed = urlparse(value)
    return Path(parsed.path.lower()).suffix in DIRECT_MEDIA_SUFFIXES


def pick_first_entry(data: dict) -> Optional[dict]:
    entries = data.get("entries") or []
    for entry in entries:
        if not entry:
            continue
        if entry.get("url"):
            return entry
        nested_entry = pick_first_entry(entry)
        if nested_entry:
            return nested_entry
    return None


def is_youtube_url(value: str) -> bool:
    if not looks_like_url(value):
        return False

    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in {"youtube.com", "music.youtube.com", "youtu.be"}


def is_youtube_playlist_url(value: str) -> bool:
    if not is_youtube_url(value):
        return False

    parsed = urlparse(value)
    query_values = parse_qs(parsed.query)
    return "list" in query_values


def normalize_youtube_playlist_url(value: str) -> str:
    if not is_youtube_playlist_url(value):
        return value

    parsed = urlparse(value)
    query_values = parse_qs(parsed.query)
    playlist_ids = query_values.get("list") or []
    if not playlist_ids:
        return value

    host = parsed.netloc or "www.youtube.com"
    if host.lower().endswith("youtu.be"):
        host = "www.youtube.com"

    query = urlencode({"list": playlist_ids[0]})
    return urlunparse((parsed.scheme or "https", host, "/playlist", "", query, ""))


def detect_source_name(data: dict, query: str) -> str:
    extractor = data.get("extractor_key") or data.get("extractor")
    if extractor:
        return str(extractor)
    parsed = urlparse(data.get("webpage_url") or data.get("original_url") or query)
    return parsed.netloc or "Neznamy zdroj"


def create_direct_media_track(url: str, requested_by: str) -> Track:
    parsed = urlparse(url)
    title = Path(parsed.path).name or f"Media stream ({parsed.netloc})"
    return Track(
        title=title,
        webpage_url=url,
        stream_url=url,
        requested_by=requested_by,
        source_name=parsed.netloc or "Primy stream",
    )


def create_track_from_data(data: dict, query: str, requested_by: str) -> Track:
    stream_url = data.get("url")
    webpage_url = data.get("webpage_url") or data.get("original_url")
    title = data.get("title") or "Neznamy nazev"
    source_name = detect_source_name(data, query)
    http_headers = clean_http_headers(data.get("http_headers"))

    if not stream_url or not webpage_url:
        if looks_like_url(query) and is_direct_media_url(query):
            return create_direct_media_track(query, requested_by)
        raise commands.CommandError("Nepodarilo se ziskat prehravaci odkaz.")

    return Track(
        title=title,
        webpage_url=webpage_url,
        stream_url=stream_url,
        requested_by=requested_by,
        source_name=source_name,
        http_headers=http_headers,
    )


def get_playlist_entry_url(entry: dict) -> Optional[str]:
    for key in ("webpage_url", "original_url"):
        value = entry.get(key)
        if isinstance(value, str) and looks_like_url(value):
            return value

    url = entry.get("url")
    if isinstance(url, str):
        if looks_like_url(url):
            return url
        if entry.get("ie_key") == "Youtube" or len(url) == 11:
            return f"https://www.youtube.com/watch?v={url}"

    video_id = entry.get("id")
    if isinstance(video_id, str) and video_id:
        return f"https://www.youtube.com/watch?v={video_id}"

    return None


async def extract_track(query: str, requested_by: str, *, allow_playlist: bool = False) -> Track | list[Track]:
    def _extract() -> dict:
        search_term = normalize_youtube_playlist_url(query) if allow_playlist else query
        search_term = search_term if looks_like_url(search_term) else f"ytsearch1:{search_term}"
        last_error = None
        format_candidates = ["bestaudio/best", "best", "bestvideo+bestaudio/best", None]

        for format_name in format_candidates:
            options = dict(YTDL_OPTIONS)
            options["noplaylist"] = not allow_playlist
            if allow_playlist:
                options["extract_flat"] = False
            if format_name:
                options["format"] = format_name

            try:
                return YoutubeDL(options).extract_info(search_term, download=False)
            except DownloadError as error:
                last_error = error
                LOGGER.warning("yt-dlp selhalo pro format %s: %s", format_name or "default", error)

        if last_error:
            raise last_error
        raise commands.CommandError("Nepodarilo se nacist metadata videa.")

    try:
        data = await run_blocking(_extract)
    except DownloadError as error:
        LOGGER.exception("yt-dlp selhalo", exc_info=error)
        if is_direct_media_url(query):
            LOGGER.info("Zkousim primy media stream bez yt-dlp: %s", query)
            return create_direct_media_track(query, requested_by)
        raise commands.CommandError(
            "Nepodarilo se nacist zadany odkaz. YouTube vyhledavani je prioritni, ostatni weby funguji jen pokud je umi zpracovat yt-dlp nebo jde o primy stream."
        )
    if "entries" in data:
        if allow_playlist:
            tracks = []
            for entry in data.get("entries") or []:
                if not entry:
                    continue
                try:
                    tracks.append(create_track_from_data(entry, query, requested_by))
                except commands.CommandError:
                    entry_url = get_playlist_entry_url(entry)
                    if not entry_url:
                        LOGGER.debug("Preskakuju nepouzitelnou polozku playlistu.", exc_info=True)
                        continue
                    try:
                        resolved_entry = await extract_track(entry_url, requested_by, allow_playlist=False)
                    except commands.CommandError:
                        LOGGER.debug("Nepodarilo se nacist polozku playlistu: %s", entry_url, exc_info=True)
                        continue
                    if isinstance(resolved_entry, list):
                        tracks.extend(resolved_entry)
                    else:
                        tracks.append(resolved_entry)
            if tracks:
                return tracks
            raise commands.CommandError("Z playlistu se nepodarilo ziskat zadne prehravatelne skladby.")

        picked_entry = pick_first_entry(data)
        if not picked_entry:
            if looks_like_url(query):
                raise commands.CommandError(
                    "Z tohohle odkazu se nepodarilo ziskat prehravatelnou stopu."
                )
            raise commands.CommandError("Na YouTube jsem nic nenasel.")
        data = picked_entry

    return create_track_from_data(data, query, requested_by)


async def refresh_track_before_playback(track: Track) -> Track:
    if not should_refresh_track_before_playback(track):
        return track

    refreshed = await extract_track(
        track.webpage_url,
        track.requested_by,
        allow_playlist=False,
    )
    if isinstance(refreshed, list):
        if not refreshed:
            raise commands.CommandError(
                f"Nepodarilo se obnovit YouTube stream pro **{track.title}**."
            )
        refreshed_track = refreshed[0]
    else:
        refreshed_track = refreshed

    refreshed_track.requested_by = track.requested_by
    return refreshed_track


def create_radio_track(stream_url: str, requested_by: str) -> Track:
    if not looks_like_url(stream_url):
        raise commands.CommandError("Pro `!radio` musis zadat platnou URL streamu.")

    parsed = urlparse(stream_url)
    host = parsed.netloc or "neznamy zdroj"
    return Track(
        title=f"Radio stream ({host})",
        webpage_url=stream_url,
        stream_url=stream_url,
        requested_by=requested_by,
        source_name=host,
    )


def resolve_radio_input(value: str) -> str:
    alias = value.strip().lower()
    return radio_aliases.get(alias, value.strip())


def find_player(guild: discord.Guild) -> Optional[GuildPlayer]:
    return players.get(guild.id)


def get_queue_snapshot(player: Optional[GuildPlayer]) -> list[Track]:
    if player is None:
        return []
    return list(player.queue._queue)


def build_queue_text(player: Optional[GuildPlayer]) -> str:
    items = get_queue_snapshot(player)
    lines = []

    if player and player.current:
        lines.append(f"Prave hraje: **{player.current.title}**")

    if items:
        lines.extend(f"{index}. {track.title}" for index, track in enumerate(items, start=1))
    elif player is None or not player.current:
        lines.append("Fronta je prazdna.")

    return "\n".join(lines)


def require_member_voice(member: discord.Member) -> discord.VoiceState:
    voice_state = getattr(member, "voice", None)
    if voice_state is None or voice_state.channel is None:
        raise commands.CommandError("Musis byt pripojeny do hlasoveho kanalu.")
    return voice_state


def require_interaction_guild(interaction: discord.Interaction) -> discord.Guild:
    if interaction.guild is None:
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    return interaction.guild


def require_interaction_member(interaction: discord.Interaction) -> discord.Member:
    if not isinstance(interaction.user, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    return interaction.user


async def ensure_voice_for_member(
    guild: discord.Guild,
    member: discord.Member,
    text_channel: Optional[discord.TextChannel] = None,
) -> GuildPlayer:
    voice_state = require_member_voice(member)
    player = get_player(guild)
    if text_channel is not None:
        player.text_channel = text_channel
    await player.connect(voice_state.channel)
    return player


async def ensure_voice(ctx: commands.Context) -> GuildPlayer:
    if ctx.guild is None:
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")

    text_channel = ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None
    return await ensure_voice_for_member(ctx.guild, ctx.author, text_channel)


def get_player_for_control(guild: discord.Guild) -> GuildPlayer:
    player = find_player(guild)
    if player is None:
        raise commands.CommandError("Bot jeste nema aktivni prehravac na tomhle serveru.")
    return player


def ensure_same_voice_channel(player: GuildPlayer, member: discord.Member) -> None:
    voice_state = require_member_voice(member)
    if player.voice_client and player.voice_client.channel and player.voice_client.channel != voice_state.channel:
        raise commands.CommandError("Musis byt ve stejnem hlasovem kanalu jako bot.")


async def enqueue_play_request(
    guild: discord.Guild,
    member: discord.Member,
    query: str,
    text_channel: Optional[discord.TextChannel],
) -> list[Track]:
    if not FFMPEG_EXECUTABLE:
        raise commands.CommandError(
            "FFmpeg nebyl nalezen. Nainstaluj ho a restartuj terminal nebo PC."
    )
    cleaned_query = query.strip()
    if not cleaned_query:
        raise commands.CommandError("Pro `play` zadej YouTube odkaz nebo hledany text.")

    playlist_requested = is_youtube_playlist_url(cleaned_query)
    voice_state = require_member_voice(member)

    await send_status_update(
        guild,
        f"Prijato: `{cleaned_query}`",
        preferred_channel=text_channel,
    )
    await send_status_update(
        guild,
        f"Pripojuju se do hlasoveho kanalu `{voice_state.channel}`...",
        preferred_channel=text_channel,
    )

    player = get_player(guild)
    if text_channel is not None:
        player.text_channel = text_channel
    await player.connect(voice_state.channel)

    if playlist_requested:
        await send_status_update(
            guild,
            "Nacitam YouTube playlist, muze to chvili trvat...",
            preferred_channel=text_channel,
        )
    elif is_youtube_url(cleaned_query):
        await send_status_update(
            guild,
            "Nacitam YouTube odkaz...",
            preferred_channel=text_channel,
        )
    elif looks_like_url(cleaned_query):
        await send_status_update(
            guild,
            "Nacitam odkaz...",
            preferred_channel=text_channel,
        )
    else:
        await send_status_update(
            guild,
            f"Vyhledavam na YouTube: `{cleaned_query}`",
            preferred_channel=text_channel,
        )

    starts_now = player.current is None and player.queue.empty()
    result = await extract_track(
        cleaned_query,
        member.display_name,
        allow_playlist=playlist_requested,
    )
    tracks = result if isinstance(result, list) else [result]
    await send_status_update(
        guild,
        format_track_ready_status(tracks, starts_now=starts_now),
        preferred_channel=text_channel,
    )

    for track in tracks:
        await player.queue.put(track)
    return tracks


def format_play_enqueue_status(tracks: list[Track]) -> str:
    if len(tracks) == 1:
        track = tracks[0]
        return f"Pridano do fronty: **{track.title}** (`{track.source_name}`)"

    return f"Pridano do fronty {len(tracks)} skladeb z playlistu."


def format_track_ready_status(tracks: list[Track], *, starts_now: bool) -> str:
    action = "Poustim" if starts_now else "Davam do fronty"
    if len(tracks) == 1:
        track = tracks[0]
        return f"{action}: **{track.title}**\n{track.webpage_url}"

    return f"{action} YouTube playlist: {len(tracks)} skladeb."


async def enqueue_radio_request(
    guild: discord.Guild,
    member: discord.Member,
    value: str,
    text_channel: Optional[discord.TextChannel],
) -> tuple[Track, Optional[str]]:
    if not FFMPEG_EXECUTABLE:
        raise commands.CommandError(
            "FFmpeg nebyl nalezen. Nainstaluj ho a restartuj terminal nebo PC."
        )

    parts = value.split()
    alias_saved = None
    stream_value = value.strip()

    if len(parts) >= 2 and looks_like_url(parts[0]):
        stream_value = parts[0]
        alias_saved = parts[1].lower()
        radio_aliases[alias_saved] = stream_value
        save_radio_aliases(radio_aliases)
    else:
        stream_value = resolve_radio_input(stream_value)

    player = await ensure_voice_for_member(guild, member, text_channel)
    track = create_radio_track(stream_value, member.display_name)
    await player.queue.put(track)
    return track, alias_saved


async def pause_player(guild: discord.Guild, member: discord.Member) -> str:
    player = get_player_for_control(guild)
    ensure_same_voice_channel(player, member)
    if not player.voice_client or not player.voice_client.is_playing():
        raise commands.CommandError("Prave nic nehraje.")
    player.voice_client.pause()
    return "Pozastavuju prehravani."


async def resume_player(guild: discord.Guild, member: discord.Member) -> str:
    player = get_player_for_control(guild)
    ensure_same_voice_channel(player, member)
    if not player.voice_client or not player.voice_client.is_paused():
        raise commands.CommandError("Prehravani neni pozastavene.")
    player.voice_client.resume()
    return "Obnovuju prehravani."


async def skip_player(guild: discord.Guild, member: discord.Member) -> str:
    player = get_player_for_control(guild)
    ensure_same_voice_channel(player, member)
    if not player.voice_client or not player.voice_client.is_playing():
        raise commands.CommandError("Prave nic nehraje.")
    player.voice_client.stop()
    return "Preskakuju aktualni skladbu."


async def stop_player(guild: discord.Guild, member: discord.Member) -> str:
    player = get_player_for_control(guild)
    ensure_same_voice_channel(player, member)
    player._drain_queue()
    if player.voice_client and (player.voice_client.is_playing() or player.voice_client.is_paused()):
        player.voice_client.stop()
    return "Zastavuju prehravani a cistim frontu."


async def leave_player(guild: discord.Guild, member: discord.Member) -> str:
    player = get_player_for_control(guild)
    ensure_same_voice_channel(player, member)
    await player.cleanup()
    return "Odpojeno z hlasoveho kanalu."


async def change_player_volume(guild: discord.Guild, member: discord.Member, delta: float) -> str:
    player = get_player_for_control(guild)
    ensure_same_voice_channel(player, member)
    volume_percent = player.change_volume(delta)
    return f"Nastavuju hlasitost na {volume_percent}%."


def build_radios_text() -> str:
    if not radio_aliases:
        return "Zatim nejsou ulozene zadne radio aliasy."
    lines = [f"`{alias}` -> {stream_url}" for alias, stream_url in sorted(radio_aliases.items())]
    return "Ulozena radia:\n" + "\n".join(lines)


def get_status_channel(
    guild: discord.Guild,
    preferred_channel: Optional[discord.TextChannel] = None,
) -> Optional[discord.TextChannel]:
    if preferred_channel is not None:
        return preferred_channel

    player = find_player(guild)
    if player and player.text_channel is not None:
        return player.text_channel

    system_channel = getattr(guild, "system_channel", None)
    if system_channel is not None:
        return system_channel

    return None


async def send_status_update(
    guild: discord.Guild,
    status: str,
    *,
    preferred_channel: Optional[discord.TextChannel] = None,
) -> None:
    channel = get_status_channel(guild, preferred_channel)
    if channel is None:
        LOGGER.info("Stav bez dostupneho textoveho kanalu na guild %s: %s", guild, status)
        return

    await channel.send(status, **bot_message_kwargs())


async def send_interaction_text(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = False,
) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content, ephemeral=ephemeral)


async def sync_application_commands() -> None:
    synced_commands = await bot.tree.sync()
    LOGGER.info("Sesynchronizovano globalnich slash commandu: %s", len(synced_commands))

    for guild_id in SLASH_COMMAND_GUILD_IDS:
        guild_object = discord.Object(id=guild_id)
        bot.tree.clear_commands(guild=guild_object)
        bot.tree.copy_global_to(guild=guild_object)
        guild_commands = await bot.tree.sync(guild=guild_object)
        LOGGER.info(
            "Sesynchronizovano slash commandu pro guild %s: %s",
            guild_id,
            len(guild_commands),
        )


@bot.event
async def on_ready() -> None:
    global tree_synced
    LOGGER.info("Bot pripojen jako %s", bot.user)
    if not tree_synced:
        try:
            await sync_application_commands()
        except Exception as error:
            LOGGER.exception("Synchronizace slash commandu selhala", exc_info=error)
        tree_synced = True


@bot.command(name="play", aliases=get_command_aliases("play"))
async def play(ctx: commands.Context, *, query: str) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    guild = require_guild(ctx)
    text_channel = ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None
    tracks = await enqueue_play_request(
        guild,
        ctx.author,
        query,
        text_channel,
    )
    await send_status_update(
        guild,
        format_play_enqueue_status(tracks),
        preferred_channel=text_channel,
    )


@bot.command(name="radio", aliases=get_command_aliases("radio"))
async def radio(ctx: commands.Context, *, value: str) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    guild = require_guild(ctx)
    text_channel = ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None
    track, alias_saved = await enqueue_radio_request(
        guild,
        ctx.author,
        value,
        text_channel,
    )
    if alias_saved:
        await send_status_update(
            guild,
            f"Ulozen alias `{alias_saved}` a pridano radio do fronty: **{track.title}**",
            preferred_channel=text_channel,
        )
        return
    await send_status_update(
        guild,
        f"Pridano radio do fronty: **{track.title}**",
        preferred_channel=text_channel,
    )


@bot.command(name="radios", aliases=get_command_aliases("radios"))
async def radios(ctx: commands.Context) -> None:
    await send_status_update(
        require_guild(ctx),
        build_radios_text(),
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="pause", aliases=get_command_aliases("pause"))
async def pause_cmd(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    guild = require_guild(ctx)
    await send_status_update(
        guild,
        await pause_player(guild, ctx.author),
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="resume", aliases=get_command_aliases("resume"))
async def resume_cmd(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    guild = require_guild(ctx)
    await send_status_update(
        guild,
        await resume_player(guild, ctx.author),
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="skip", aliases=get_command_aliases("skip"))
async def skip(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    guild = require_guild(ctx)
    await send_status_update(
        guild,
        await skip_player(guild, ctx.author),
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="stop", aliases=get_command_aliases("stop"))
async def stop(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    guild = require_guild(ctx)
    await send_status_update(
        guild,
        await stop_player(guild, ctx.author),
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="queue", aliases=get_command_aliases("queue"))
async def queue_cmd(ctx: commands.Context) -> None:
    guild = require_guild(ctx)
    await send_status_update(
        guild,
        build_queue_text(find_player(guild)),
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="leave", aliases=get_command_aliases("leave"))
async def leave(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    guild = require_guild(ctx)
    await send_status_update(
        guild,
        await leave_player(guild, ctx.author),
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="np", aliases=get_command_aliases("np"))
async def now_playing(ctx: commands.Context) -> None:
    guild = require_guild(ctx)
    player = find_player(guild)
    if player is None or not player.current:
        await send_status_update(
            guild,
            "Prave nic nehraje.",
            preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
        )
        return
    await send_status_update(
        guild,
        f"Prave hraje: **{player.current.title}**\n{player.current.webpage_url}",
        preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )


@bot.command(name="help", aliases=get_command_aliases("help"))
async def help_cmd(ctx: commands.Context) -> None:
    await ctx.send(build_help_text(), **bot_message_kwargs())


@bot.tree.command(name="play", description="Prida skladbu, odkaz, nebo YouTube playlist do fronty")
@app_commands.describe(query="Hledany text, URL skladby, nebo URL YouTube playlistu")
async def play_slash(interaction: discord.Interaction, query: str) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    await interaction.response.defer(ephemeral=True)
    tracks = await enqueue_play_request(guild, member, query, text_channel)
    status = format_play_enqueue_status(tracks)
    await send_status_update(
        guild,
        status,
        preferred_channel=text_channel,
    )
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="radio", description="Prida radio stream nebo ulozi alias")
@app_commands.describe(value="URL streamu nebo alias")
async def radio_slash(interaction: discord.Interaction, value: str) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    track, alias_saved = await enqueue_radio_request(guild, member, value, text_channel)
    if alias_saved:
        status = f"Ulozen alias `{alias_saved}` a pridano radio do fronty: **{track.title}**"
        await send_status_update(guild, status, preferred_channel=text_channel)
        await send_interaction_text(interaction, status, ephemeral=True)
        return
    status = f"Pridano radio do fronty: **{track.title}**"
    await send_status_update(guild, status, preferred_channel=text_channel)
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="radios", description="Ukaze ulozena radia")
async def radios_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    status = build_radios_text()
    await send_status_update(
        guild,
        status,
        preferred_channel=interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None,
    )
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="pause", description="Pozastavi prehravani")
async def pause_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    status = await pause_player(guild, member)
    await send_status_update(guild, status, preferred_channel=text_channel)
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="resume", description="Obnovi prehravani")
async def resume_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    status = await resume_player(guild, member)
    await send_status_update(guild, status, preferred_channel=text_channel)
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="skip", description="Preskoci aktualni polozku")
async def skip_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    status = await skip_player(guild, member)
    await send_status_update(guild, status, preferred_channel=text_channel)
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="stop", description="Zastavi prehravani a vymaze frontu")
async def stop_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    status = await stop_player(guild, member)
    await send_status_update(guild, status, preferred_channel=text_channel)
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="queue", description="Ukaze frontu")
async def queue_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    status = build_queue_text(find_player(guild))
    await send_status_update(
        guild,
        status,
        preferred_channel=interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None,
    )
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="np", description="Ukaze prave prehravanou polozku")
async def np_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    player = find_player(guild)
    if player is None or not player.current:
        status = "Prave nic nehraje."
        await send_status_update(
            guild,
            status,
            preferred_channel=interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None,
        )
        await send_interaction_text(interaction, status, ephemeral=True)
        return
    status = f"Prave hraje: **{player.current.title}**\n{player.current.webpage_url}"
    await send_status_update(
        guild,
        status,
        preferred_channel=interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None,
    )
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="leave", description="Odpoji bota z hlasoveho kanalu")
async def leave_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    status = await leave_player(guild, member)
    await send_status_update(guild, status, preferred_channel=text_channel)
    await send_interaction_text(interaction, status, ephemeral=True)


@bot.tree.command(name="help", description="Ukaze napovedu")
async def help_slash(interaction: discord.Interaction) -> None:
    await send_interaction_text(interaction, build_help_text(), ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    guild = interaction.guild
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    if isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, commands.CommandError):
        if guild is not None:
            await send_status_update(guild, str(error.original), preferred_channel=text_channel)
        await send_interaction_text(interaction, str(error.original), ephemeral=True)
        return
    if isinstance(error, commands.CommandError):
        if guild is not None:
            await send_status_update(guild, str(error), preferred_channel=text_channel)
        await send_interaction_text(interaction, str(error), ephemeral=True)
        return
    LOGGER.exception("Neocekavana chyba ve slash commandu", exc_info=error)
    if guild is not None:
        await send_status_update(guild, "Doslo k neocekavane chybe.", preferred_channel=text_channel)
    await send_interaction_text(interaction, "Doslo k neocekavane chybe.", ephemeral=True)


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.guild is not None:
            await send_status_update(
                ctx.guild,
                f"Chybi parametr prikazu. Pouzij `{COMMAND_PREFIX}help`.",
                preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
            )
        else:
            await ctx.send(f"Chybi parametr prikazu. Pouzij `{COMMAND_PREFIX}help`.", **bot_message_kwargs())
        return
    if isinstance(error, commands.CommandError):
        if ctx.guild is not None:
            await send_status_update(
                ctx.guild,
                str(error),
                preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
            )
        else:
            await ctx.send(str(error), **bot_message_kwargs())
        return

    LOGGER.exception("Neocekavana chyba", exc_info=error)
    if ctx.guild is not None:
        await send_status_update(
            ctx.guild,
            "Doslo k neocekavane chybe.",
            preferred_channel=ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
        )
    else:
        await ctx.send("Doslo k neocekavane chybe.", **bot_message_kwargs())


def main() -> None:
    token = load_discord_token()
    if not FFMPEG_EXECUTABLE:
        LOGGER.warning("FFmpeg nebyl nalezen. Bot se prihlasi, ale prehravani nebude fungovat.")
    else:
        LOGGER.info("Pouzivam FFmpeg: %s", FFMPEG_EXECUTABLE)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
