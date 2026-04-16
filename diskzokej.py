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
DEFAULT_CONFIG: Dict[str, Any] = {
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


CONFIG = load_config()
COMMAND_PREFIX = str(CONFIG.get("command_prefix", DEFAULT_CONFIG["command_prefix"]))
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

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
players: Dict[int, GuildPlayer] = {}
ytdl = YoutubeDL(YTDL_OPTIONS)
fallback_ytdl = YoutubeDL(FALLBACK_YTDL_OPTIONS)
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
        try:
            return ytdl.extract_info(search_term, download=False)
        except DownloadError as error:
            message = str(error)
            if "Requested format is not available" not in message:
                raise
            LOGGER.warning("Primarni audio format neni dostupny, zkousim fallback `best`.")
            return fallback_ytdl.extract_info(search_term, download=False)

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


async def ensure_voice(ctx: commands.Context) -> GuildPlayer:
    if ctx.guild is None:
        raise commands.CommandError("Tenhle bot funguje jen na serveru.")
    if not isinstance(ctx.author, discord.Member) or ctx.author.voice is None:
        raise commands.CommandError("Musis byt pripojeny do hlasoveho kanalu.")

    player = get_player(ctx.guild)
    player.text_channel = ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None
    await player.connect(ctx.author.voice.channel)
    return player


@bot.event
async def on_ready() -> None:
    LOGGER.info("Bot pripojen jako %s", bot.user)


@bot.command(name="play")
async def play(ctx: commands.Context, *, query: str) -> None:
    if not FFMPEG_EXECUTABLE:
        raise commands.CommandError(
            "FFmpeg nebyl nalezen. Nainstaluj ho a restartuj terminal nebo PC."
        )
    player = await ensure_voice(ctx)
    track = await extract_track(query, ctx.author.display_name)
    await player.queue.put(track)
    await ctx.send(f"Pridano do fronty: **{track.title}** (`{track.source_name}`)")


@bot.command(name="radio")
async def radio(ctx: commands.Context, *, value: str) -> None:
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

    player = await ensure_voice(ctx)
    track = create_radio_track(stream_value, ctx.author.display_name)
    await player.queue.put(track)
    if alias_saved:
        await ctx.send(
            f"Ulozen alias `{alias_saved}` a pridano radio do fronty: **{track.title}**"
        )
        return
    await ctx.send(f"Pridano radio do fronty: **{track.title}**")


@bot.command(name="radios")
async def radios(ctx: commands.Context) -> None:
    if not radio_aliases:
        await ctx.send("Zatim nejsou ulozene zadne radio aliasy.")
        return

    lines = [f"`{alias}` -> {stream_url}" for alias, stream_url in sorted(radio_aliases.items())]
    await ctx.send("Ulozena radia:\n" + "\n".join(lines))


@bot.command(name="skip")
async def skip(ctx: commands.Context) -> None:
    player = get_player(require_guild(ctx))
    if not player.voice_client or not player.voice_client.is_playing():
        raise commands.CommandError("Prave nic nehraje.")
    player.voice_client.stop()
    await ctx.send("Preskakuju aktualni skladbu.")


@bot.command(name="stop")
async def stop(ctx: commands.Context) -> None:
    player = get_player(require_guild(ctx))
    player._drain_queue()
    if player.voice_client and player.voice_client.is_playing():
        player.voice_client.stop()
    await ctx.send("Zastavuju prehravani a cistim frontu.")


@bot.command(name="queue")
async def queue_cmd(ctx: commands.Context) -> None:
    player = get_player(require_guild(ctx))
    items = list(player.queue._queue)
    lines = []

    if player.current:
        lines.append(f"Prave hraje: **{player.current.title}**")

    if items:
        lines.extend(f"{index}. {track.title}" for index, track in enumerate(items, start=1))
    elif not player.current:
        lines.append("Fronta je prazdna.")

    await ctx.send("\n".join(lines))


@bot.command(name="leave")
async def leave(ctx: commands.Context) -> None:
    player = get_player(require_guild(ctx))
    await player.cleanup()
    await ctx.send("Odpojeno z hlasoveho kanalu.")


@bot.command(name="np")
async def now_playing(ctx: commands.Context) -> None:
    player = get_player(require_guild(ctx))
    if not player.current:
        await ctx.send("Prave nic nehraje.")
        return
    await ctx.send(f"Prave hraje: **{player.current.title}**\n{player.current.webpage_url}")


@bot.command(name="help")
async def help_cmd(ctx: commands.Context) -> None:
    await ctx.send(
        "Napoveda k botovi:\n"
        f"`{COMMAND_PREFIX}play <odkaz nebo hledany text>` - prida skladbu nebo audio do fronty. Text se hleda na YouTube.\n"
        f"`{COMMAND_PREFIX}radio <stream_url> [alias]` - prida radio stream do fronty a volitelne ulozi alias.\n"
        f"`{COMMAND_PREFIX}radio <alias>` - spusti drive ulozene radio podle aliasu.\n"
        f"`{COMMAND_PREFIX}radios` - vypise vsechna ulozena radia a jejich adresy.\n"
        f"`{COMMAND_PREFIX}skip` - preskoci aktualne prehravanou polozku.\n"
        f"`{COMMAND_PREFIX}stop` - zastavi prehravani a vymaze cekajici frontu.\n"
        f"`{COMMAND_PREFIX}queue` - ukaze, co prave hraje a co je jeste ve fronte.\n"
        f"`{COMMAND_PREFIX}np` - ukaze aktualne prehravanou skladbu nebo stream.\n"
        f"`{COMMAND_PREFIX}leave` - odpoji bota z hlasoveho kanalu.\n"
        f"`{COMMAND_PREFIX}help` - ukaze tuhle napovedu.\n"
        "Priklady:\n"
        f"`{COMMAND_PREFIX}play never gonna give you up`\n"
        f"`{COMMAND_PREFIX}play https://www.youtube.com/watch?v=dQw4w9WgXcQ`\n"
        f"`{COMMAND_PREFIX}radio https://stream.example.com/live.mp3 beat`\n"
        f"`{COMMAND_PREFIX}radio beat`"
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Chybi parametr prikazu. Pouzij `!help`.")
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
