import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

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
    "idle_disconnect_timeout": 300,
    "playback_start_timeout": 15,
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
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
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


CONFIG = load_config()
COMMAND_PREFIX = normalize_command_prefix(
    CONFIG.get("command_prefix", DEFAULT_CONFIG["command_prefix"])
)
IDLE_DISCONNECT_TIMEOUT = int(
    CONFIG.get("idle_disconnect_timeout", DEFAULT_CONFIG["idle_disconnect_timeout"])
)
PLAYBACK_START_TIMEOUT = int(
    CONFIG.get("playback_start_timeout", DEFAULT_CONFIG["playback_start_timeout"])
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
    "panel",
)


def looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    requested_by: str
    source_name: str


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
        f"`{format_command_label('play')} <odkaz nebo hledany text>` nebo `{format_slash_label('play')}` - prida skladbu nebo audio do fronty. Text se hleda na YouTube.\n"
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
        f"`{format_command_label('panel')}` nebo `{format_slash_label('panel')}` - otevre ovladaci panel s tlacitky a radii.\n"
        f"`{format_command_label('help')}` nebo `{format_slash_label('help')}` - ukaze tuhle napovedu.\n"
        f"Aktivni prefix: `{COMMAND_PREFIX}`\n"
        "Priklady:\n"
        f"`{COMMAND_PREFIX}play never gonna give you up`\n"
        f"`{COMMAND_PREFIX}radio https://stream.example.com/live.mp3 beat`\n"
        f"`{COMMAND_PREFIX}panel`\n"
        "`/play query:never gonna give you up`\n"
        "`/panel`"
    )


class GuildPlayer:
    def __init__(self, bot: commands.Bot, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.voice_client: Optional[discord.VoiceClient] = None
        self.current: Optional[Track] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self.player_task = bot.loop.create_task(self.player_loop())

    async def send_status(self, message: str) -> None:
        if self.text_channel:
            await self.text_channel.send(message)

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

    async def wait_for_playback_start(
        self,
        track: Track,
        finished: asyncio.Event,
    ) -> None:
        for _ in range(PLAYBACK_START_TIMEOUT * 2):
            if finished.is_set():
                return
            if self.voice_client and self.voice_client.is_playing():
                return
            await asyncio.sleep(0.5)

        LOGGER.warning("Stream se nerozbehl vcas na %s: %s", self.guild.name, track.webpage_url)
        await self.send_status(
            f"Nepodarilo se rozbehnout stream: **{track.title}**. Preskakuju na dalsi polozku."
        )
        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.stop()
        self._finish_event(finished)

    async def player_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            while not self.bot.is_closed():
                track: Optional[Track] = None
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
                    self.current = track
                    await self.send_status(f"Prave hraju: **{track.title}**\n{track.webpage_url}")

                    finished = asyncio.Event()

                    def after_playback(error: Optional[Exception]) -> None:
                        if error:
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

                    source = discord.FFmpegPCMAudio(
                        track.stream_url,
                        executable=FFMPEG_EXECUTABLE,
                        **FFMPEG_OPTIONS,
                    )
                    self.voice_client.play(source, after=after_playback)
                    await self.wait_for_playback_start(track, finished)
                    await finished.wait()
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


async def extract_track(query: str, requested_by: str) -> Track:
    def _extract() -> dict:
        search_term = query if looks_like_url(query) else f"ytsearch1:{query}"
        last_error = None
        format_candidates = ["bestaudio/best", "best", "bestvideo+bestaudio/best", None]

        for format_name in format_candidates:
            options = dict(YTDL_OPTIONS)
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
        picked_entry = pick_first_entry(data)
        if not picked_entry:
            if looks_like_url(query):
                raise commands.CommandError(
                    "Z tohohle odkazu se nepodarilo ziskat prehravatelnou stopu."
                )
            raise commands.CommandError("Na YouTube jsem nic nenasel.")
        data = picked_entry

    stream_url = data.get("url")
    webpage_url = data.get("webpage_url") or data.get("original_url")
    title = data.get("title") or "Neznamy nazev"
    source_name = detect_source_name(data, query)

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
    )


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
    elif not player.current:
        lines.append("Fronta je prazdna.")

    return "\n".join(lines)


def build_player_embed(player: Optional[GuildPlayer]) -> discord.Embed:
    embed = discord.Embed(title="Diskzokej Panel")
    if player and player.current:
        embed.add_field(
            name="Prave hraje",
            value=f"**{player.current.title}**\n{player.current.webpage_url}",
            inline=False,
        )
    else:
        embed.add_field(name="Prave hraje", value="Nic nehraje.", inline=False)

    queue_text = build_queue_text(player)
    if len(queue_text) > 1024:
        queue_text = queue_text[:1021] + "..."
    embed.add_field(name="Fronta", value=queue_text, inline=False)

    if radio_aliases:
        radios_text = "\n".join(f"`{alias}`" for alias in sorted(radio_aliases)[:10])
        embed.add_field(name="Ulozena radia", value=radios_text, inline=False)

    voice_state = "Nepripojen"
    if player and player.voice_client and player.voice_client.is_connected():
        voice_state = f"Pripojen do `{player.voice_client.channel}`"
        if player.voice_client.is_paused():
            voice_state += " (pauza)"
    embed.set_footer(text=f"Prefix: {COMMAND_PREFIX} | {voice_state}")
    return embed


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
) -> Track:
    if not FFMPEG_EXECUTABLE:
        raise commands.CommandError(
            "FFmpeg nebyl nalezen. Nainstaluj ho a restartuj terminal nebo PC."
        )
    player = await ensure_voice_for_member(guild, member, text_channel)
    track = await extract_track(query, member.display_name)
    await player.queue.put(track)
    return track


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


def build_radios_text() -> str:
    if not radio_aliases:
        return "Zatim nejsou ulozene zadne radio aliasy."
    lines = [f"`{alias}` -> {stream_url}" for alias, stream_url in sorted(radio_aliases.items())]
    return "Ulozena radia:\n" + "\n".join(lines)


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


class RadioSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label=alias, value=alias, description=radio_aliases[alias][:100])
            for alias in sorted(radio_aliases)[:25]
        ]
        super().__init__(
            placeholder="Vyber ulozene radio",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            guild = require_interaction_guild(interaction)
            member = require_interaction_member(interaction)
            text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
            selected_alias = self.values[0]
            track, _ = await enqueue_radio_request(guild, member, selected_alias, text_channel)
            player = get_player(guild)
            await interaction.response.edit_message(
                embed=build_player_embed(player),
                view=PlayerPanelView(guild),
            )
            await interaction.followup.send(
                f"Pridano radio do fronty: **{track.title}**",
                ephemeral=True,
            )
        except commands.CommandError as error:
            await send_interaction_text(interaction, str(error), ephemeral=True)


class PlayerPanelView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        self.guild = guild
        if radio_aliases:
            self.add_item(RadioSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            require_interaction_guild(interaction)
            require_interaction_member(interaction)
            return True
        except commands.CommandError as error:
            await send_interaction_text(interaction, str(error), ephemeral=True)
            return False

    async def refresh_panel(self, interaction: discord.Interaction) -> None:
        player = find_player(self.guild)
        await interaction.response.edit_message(
            embed=build_player_embed(player),
            view=PlayerPanelView(self.guild),
        )

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            guild = require_interaction_guild(interaction)
            member = require_interaction_member(interaction)
            await pause_player(guild, member)
            await self.refresh_panel(interaction)
        except commands.CommandError as error:
            await send_interaction_text(interaction, str(error), ephemeral=True)

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.success)
    async def resume_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            guild = require_interaction_guild(interaction)
            member = require_interaction_member(interaction)
            await resume_player(guild, member)
            await self.refresh_panel(interaction)
        except commands.CommandError as error:
            await send_interaction_text(interaction, str(error), ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary)
    async def skip_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            guild = require_interaction_guild(interaction)
            member = require_interaction_member(interaction)
            await skip_player(guild, member)
            await self.refresh_panel(interaction)
        except commands.CommandError as error:
            await send_interaction_text(interaction, str(error), ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            guild = require_interaction_guild(interaction)
            member = require_interaction_member(interaction)
            await stop_player(guild, member)
            await self.refresh_panel(interaction)
        except commands.CommandError as error:
            await send_interaction_text(interaction, str(error), ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary)
    async def queue_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = get_player(self.guild)
        await send_interaction_text(interaction, build_queue_text(player), ephemeral=True)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            guild = require_interaction_guild(interaction)
            member = require_interaction_member(interaction)
            await leave_player(guild, member)
            await self.refresh_panel(interaction)
        except commands.CommandError as error:
            await send_interaction_text(interaction, str(error), ephemeral=True)


async def send_player_panel(
    destination: commands.Context | discord.Interaction,
    guild: discord.Guild,
) -> None:
    player = find_player(guild)
    embed = build_player_embed(player)
    view = PlayerPanelView(guild)

    if isinstance(destination, commands.Context):
        await destination.send(embed=embed, view=view)
        return

    if destination.response.is_done():
        await destination.followup.send(embed=embed, view=view)
    else:
        await destination.response.send_message(embed=embed, view=view)


@bot.event
async def on_ready() -> None:
    global tree_synced
    LOGGER.info("Bot pripojen jako %s", bot.user)
    if not tree_synced:
        try:
            synced_commands = await bot.tree.sync()
            LOGGER.info("Sesynchronizovano slash commandu: %s", len(synced_commands))
        except Exception as error:
            LOGGER.exception("Synchronizace slash commandu selhala", exc_info=error)
        tree_synced = True


@bot.command(name="play", aliases=get_command_aliases("play"))
async def play(ctx: commands.Context, *, query: str) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    track = await enqueue_play_request(
        require_guild(ctx),
        ctx.author,
        query,
        ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )
    await ctx.send(f"Pridano do fronty: **{track.title}** (`{track.source_name}`)")


@bot.command(name="radio", aliases=get_command_aliases("radio"))
async def radio(ctx: commands.Context, *, value: str) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    track, alias_saved = await enqueue_radio_request(
        require_guild(ctx),
        ctx.author,
        value,
        ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None,
    )
    if alias_saved:
        await ctx.send(
            f"Ulozen alias `{alias_saved}` a pridano radio do fronty: **{track.title}**"
        )
        return
    await ctx.send(f"Pridano radio do fronty: **{track.title}**")


@bot.command(name="radios", aliases=get_command_aliases("radios"))
async def radios(ctx: commands.Context) -> None:
    await ctx.send(build_radios_text())


@bot.command(name="pause", aliases=get_command_aliases("pause"))
async def pause_cmd(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    await ctx.send(await pause_player(require_guild(ctx), ctx.author))


@bot.command(name="resume", aliases=get_command_aliases("resume"))
async def resume_cmd(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    await ctx.send(await resume_player(require_guild(ctx), ctx.author))


@bot.command(name="skip", aliases=get_command_aliases("skip"))
async def skip(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    await ctx.send(await skip_player(require_guild(ctx), ctx.author))


@bot.command(name="stop", aliases=get_command_aliases("stop"))
async def stop(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    await ctx.send(await stop_player(require_guild(ctx), ctx.author))


@bot.command(name="queue", aliases=get_command_aliases("queue"))
async def queue_cmd(ctx: commands.Context) -> None:
    await ctx.send(build_queue_text(find_player(require_guild(ctx))))


@bot.command(name="leave", aliases=get_command_aliases("leave"))
async def leave(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    await ctx.send(await leave_player(require_guild(ctx), ctx.author))


@bot.command(name="np", aliases=get_command_aliases("np"))
async def now_playing(ctx: commands.Context) -> None:
    player = get_player(require_guild(ctx))
    if not player.current:
        await ctx.send("Prave nic nehraje.")
        return
    await ctx.send(f"Prave hraje: **{player.current.title}**\n{player.current.webpage_url}")


@bot.command(name="help", aliases=get_command_aliases("help"))
async def help_cmd(ctx: commands.Context) -> None:
    await ctx.send(build_help_text())


@bot.command(name="panel", aliases=get_command_aliases("panel"))
async def panel_cmd(ctx: commands.Context) -> None:
    await send_player_panel(ctx, require_guild(ctx))


@bot.tree.command(name="play", description="Prida skladbu nebo odkaz do fronty")
@app_commands.describe(query="Hledany text nebo URL")
async def play_slash(interaction: discord.Interaction, query: str) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    track = await enqueue_play_request(guild, member, query, text_channel)
    await send_interaction_text(
        interaction,
        f"Pridano do fronty: **{track.title}** (`{track.source_name}`)",
    )


@bot.tree.command(name="radio", description="Prida radio stream nebo ulozi alias")
@app_commands.describe(value="URL streamu nebo alias")
async def radio_slash(interaction: discord.Interaction, value: str) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    text_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    track, alias_saved = await enqueue_radio_request(guild, member, value, text_channel)
    if alias_saved:
        await send_interaction_text(
            interaction,
            f"Ulozen alias `{alias_saved}` a pridano radio do fronty: **{track.title}**",
        )
        return
    await send_interaction_text(interaction, f"Pridano radio do fronty: **{track.title}**")


@bot.tree.command(name="radios", description="Ukaze ulozena radia")
async def radios_slash(interaction: discord.Interaction) -> None:
    await send_interaction_text(interaction, build_radios_text(), ephemeral=True)


@bot.tree.command(name="pause", description="Pozastavi prehravani")
async def pause_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    await send_interaction_text(interaction, await pause_player(guild, member))


@bot.tree.command(name="resume", description="Obnovi prehravani")
async def resume_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    await send_interaction_text(interaction, await resume_player(guild, member))


@bot.tree.command(name="skip", description="Preskoci aktualni polozku")
async def skip_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    await send_interaction_text(interaction, await skip_player(guild, member))


@bot.tree.command(name="stop", description="Zastavi prehravani a vymaze frontu")
async def stop_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    await send_interaction_text(interaction, await stop_player(guild, member))


@bot.tree.command(name="queue", description="Ukaze frontu")
async def queue_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    await send_interaction_text(interaction, build_queue_text(find_player(guild)), ephemeral=True)


@bot.tree.command(name="np", description="Ukaze prave prehravanou polozku")
async def np_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    player = find_player(guild)
    if player is None or not player.current:
        await send_interaction_text(interaction, "Prave nic nehraje.", ephemeral=True)
        return
    await send_interaction_text(
        interaction,
        f"Prave hraje: **{player.current.title}**\n{player.current.webpage_url}",
        ephemeral=True,
    )


@bot.tree.command(name="leave", description="Odpoji bota z hlasoveho kanalu")
async def leave_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    member = require_interaction_member(interaction)
    await send_interaction_text(interaction, await leave_player(guild, member))


@bot.tree.command(name="help", description="Ukaze napovedu")
async def help_slash(interaction: discord.Interaction) -> None:
    await send_interaction_text(interaction, build_help_text(), ephemeral=True)


@bot.tree.command(name="panel", description="Otevre ovladaci panel")
async def panel_slash(interaction: discord.Interaction) -> None:
    guild = require_interaction_guild(interaction)
    await send_player_panel(interaction, guild)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, commands.CommandError):
        await send_interaction_text(interaction, str(error.original), ephemeral=True)
        return
    if isinstance(error, commands.CommandError):
        await send_interaction_text(interaction, str(error), ephemeral=True)
        return
    LOGGER.exception("Neocekavana chyba ve slash commandu", exc_info=error)
    await send_interaction_text(interaction, "Doslo k neocekavane chybe.", ephemeral=True)


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Chybi parametr prikazu. Pouzij `{COMMAND_PREFIX}help`.")
        return
    if isinstance(error, commands.CommandError):
        await ctx.send(str(error))
        return

    LOGGER.exception("Neocekavana chyba", exc_info=error)
    await ctx.send("Doslo k neocekavane chybe.")


def main() -> None:
    token = load_discord_token()
    if not FFMPEG_EXECUTABLE:
        LOGGER.warning("FFmpeg nebyl nalezen. Bot se prihlasi, ale prehravani nebude fungovat.")
    else:
        LOGGER.info("Pouzivam FFmpeg: %s", FFMPEG_EXECUTABLE)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
