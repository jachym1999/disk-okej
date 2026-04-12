import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import discord
from discord.ext import commands
from yt_dlp import YoutubeDL


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("diskzokej")

COMMAND_PREFIX = "!"
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch1",
    "quiet": True,
    "no_warnings": True,
}
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
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

    async def player_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            track = await self.queue.get()
            self.current = track
            if self.text_channel:
                await self.text_channel.send(
                    f"Prave hraju: **{track.title}**\n{track.webpage_url}"
                )

            finished = asyncio.Event()

            def after_playback(error: Optional[Exception]) -> None:
                if error:
                    LOGGER.exception("Chyba pri prehravani na %s", self.guild.name, exc_info=error)
                self.bot.loop.call_soon_threadsafe(finished.set)

            try:
                if not FFMPEG_EXECUTABLE:
                    raise commands.CommandError(
                        "FFmpeg nebyl nalezen. Nainstaluj ho a over prikaz `ffmpeg -version`."
                    )

                source = discord.FFmpegPCMAudio(
                    track.stream_url,
                    executable=FFMPEG_EXECUTABLE,
                    **FFMPEG_OPTIONS,
                )
                self.voice_client.play(source, after=after_playback)
            except FileNotFoundError as error:
                LOGGER.exception("FFmpeg nebyl nalezen", exc_info=error)
                if self.text_channel:
                    await self.text_channel.send(
                        "Nepodarilo se spustit FFmpeg. Over, ze je nainstalovany a dostupny."
                    )
                finished.set()
            except discord.ClientException as error:
                LOGGER.exception("Discord voice chyba", exc_info=error)
                if self.text_channel:
                    await self.text_channel.send(f"Prehravani selhalo: {error}")
                finished.set()
            except commands.CommandError as error:
                if self.text_channel:
                    await self.text_channel.send(str(error))
                finished.set()

            await finished.wait()
            self.current = None

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel != channel:
                await self.voice_client.move_to(channel)
            return self.voice_client

        self.voice_client = await channel.connect(self_deaf=True)
        return self.voice_client

    async def cleanup(self) -> None:
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
        self.voice_client = None
        self.current = None
        self._drain_queue()

    def _drain_queue(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
players: dict[int, GuildPlayer] = {}
ytdl = YoutubeDL(YTDL_OPTIONS)
BASE_DIR = Path(__file__).resolve().parent
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


def load_radio_aliases() -> dict[str, str]:
    if not RADIO_ALIASES_FILE.exists():
        return {}

    try:
        raw_data = json.loads(RADIO_ALIASES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Soubor s aliasy radii neni citelny: %s", RADIO_ALIASES_FILE)
        return {}

    if not isinstance(raw_data, dict):
        return {}

    aliases: dict[str, str] = {}
    for alias, stream_url in raw_data.items():
        if isinstance(alias, str) and isinstance(stream_url, str):
            aliases[alias.lower()] = stream_url
    return aliases


def save_radio_aliases(aliases: dict[str, str]) -> None:
    RADIO_ALIASES_FILE.write_text(
        json.dumps(dict(sorted(aliases.items())), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


radio_aliases = load_radio_aliases()


async def extract_track(query: str, requested_by: str) -> Track:
    def _extract() -> dict:
        search_term = query if looks_like_url(query) else f"ytsearch1:{query}"
        return ytdl.extract_info(search_term, download=False)

    data = await asyncio.to_thread(_extract)
    if "entries" in data:
        entries = data.get("entries") or []
        if not entries:
            raise commands.CommandError("Na YouTube jsem nic nenasel.")
        data = entries[0]

    stream_url = data.get("url")
    webpage_url = data.get("webpage_url") or data.get("original_url")
    title = data.get("title") or "Neznamy nazev"

    if not stream_url or not webpage_url:
        raise commands.CommandError("Nepodarilo se ziskat prehravaci odkaz.")

    return Track(
        title=title,
        webpage_url=webpage_url,
        stream_url=stream_url,
        requested_by=requested_by,
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
    await ctx.send(f"Pridano do fronty: **{track.title}**")


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
        await ctx.send(f"Ulozen alias `{alias_saved}` a pridano radio do fronty: **{track.title}**")
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
        "Prikazy:\n"
        "`!play <youtube odkaz nebo hledany text>`\n"
        "`!radio <stream_url> [alias]` nebo `!radio <alias>`\n"
        "`!radios`\n"
        "`!skip` `!stop` `!queue` `!np` `!leave`"
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
