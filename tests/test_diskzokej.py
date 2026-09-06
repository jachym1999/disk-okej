import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def install_test_stubs() -> None:
    if "discord" not in sys.modules:
        discord_module = types.ModuleType("discord")
        app_commands_module = types.ModuleType("discord.app_commands")
        ui_module = types.ModuleType("discord.ui")

        class Intents:
            @staticmethod
            def default():
                intents = types.SimpleNamespace()
                intents.message_content = False
                intents.guilds = False
                intents.voice_states = False
                return intents

        class FFmpegPCMAudio:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

        class PCMVolumeTransformer:
            def __init__(self, source, volume=1.0) -> None:
                self.source = source
                self.volume = volume

        class Embed:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs
                self.fields = []
                self.footer = None

            def add_field(self, *args, **kwargs) -> None:
                self.fields.append((args, kwargs))

            def set_footer(self, *args, **kwargs) -> None:
                self.footer = (args, kwargs)

        class SelectOption:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

        class ButtonStyle:
            secondary = 1
            success = 2
            primary = 3
            danger = 4

        class InteractionResponse:
            def __init__(self) -> None:
                self._done = False

            def is_done(self) -> bool:
                return self._done

            async def send_message(self, *args, **kwargs) -> None:
                self._done = True

            async def edit_message(self, *args, **kwargs) -> None:
                self._done = True

            async def defer(self, *args, **kwargs) -> None:
                self._done = True

            async def send_modal(self, *args, **kwargs) -> None:
                self._done = True

        class InteractionFollowup:
            async def send(self, *args, **kwargs) -> None:
                return None

        class Interaction:
            def __init__(self) -> None:
                self.guild = None
                self.user = None
                self.channel = None
                self.response = InteractionResponse()
                self.followup = InteractionFollowup()

        class Object:
            def __init__(self, id) -> None:
                self.id = id

        class View:
            def __init__(self, *args, **kwargs) -> None:
                self.items = []

            def add_item(self, item) -> None:
                self.items.append(item)

        class Modal:
            def __init_subclass__(cls, **kwargs) -> None:
                return super().__init_subclass__()

            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

        class TextInput:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs
                self.value = ""

            def __str__(self) -> str:
                return self.value

        class Select:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs
                self.values = []

        class Button:
            pass

        def button(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        class AppCommandError(Exception):
            pass

        class CommandInvokeError(AppCommandError):
            def __init__(self, original):
                super().__init__(str(original))
                self.original = original

        def describe(**kwargs):
            def decorator(func):
                return func

            return decorator

        class CommandTree:
            def __init__(self) -> None:
                self.copied_guilds = []
                self.cleared_guilds = []
                self.synced_guilds = []

            async def sync(self, *args, **kwargs):
                self.synced_guilds.append(kwargs.get("guild"))
                return []

            def copy_global_to(self, guild):
                self.copied_guilds.append(guild)

            def clear_commands(self, *args, **kwargs):
                self.cleared_guilds.append(kwargs.get("guild"))

            def command(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            def error(self, func):
                return func

        discord_module.Intents = Intents
        discord_module.FFmpegPCMAudio = FFmpegPCMAudio
        discord_module.PCMVolumeTransformer = PCMVolumeTransformer
        discord_module.Embed = Embed
        discord_module.SelectOption = SelectOption
        discord_module.ButtonStyle = ButtonStyle
        discord_module.Interaction = Interaction
        discord_module.Object = Object
        discord_module.Guild = object
        discord_module.VoiceClient = object
        discord_module.VoiceState = object
        discord_module.TextChannel = object
        discord_module.VoiceChannel = object
        discord_module.Member = object
        discord_module.ClientException = Exception
        discord_module.ui = ui_module
        discord_module.app_commands = app_commands_module

        ui_module.View = View
        ui_module.Modal = Modal
        ui_module.TextInput = TextInput
        ui_module.Select = Select
        ui_module.Button = Button
        ui_module.button = button

        app_commands_module.AppCommandError = AppCommandError
        app_commands_module.CommandInvokeError = CommandInvokeError
        app_commands_module.describe = describe

        ext_module = types.ModuleType("discord.ext")
        commands_module = types.ModuleType("discord.ext.commands")

        class CommandError(Exception):
            pass

        class MissingRequiredArgument(CommandError):
            pass

        class CommandNotFound(CommandError):
            pass

        class Bot:
            def __init__(self, *args, **kwargs) -> None:
                self.loop = types.SimpleNamespace(create_task=lambda coro: coro)
                self.tree = CommandTree()

            async def wait_until_ready(self) -> None:
                return None

            def is_closed(self) -> bool:
                return False

            def run(self, *args, **kwargs) -> None:
                return None

            def event(self, func):
                return func

            def command(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

        commands_module.Bot = Bot
        commands_module.Context = object
        commands_module.CommandError = CommandError
        commands_module.MissingRequiredArgument = MissingRequiredArgument
        commands_module.CommandNotFound = CommandNotFound
        ext_module.commands = commands_module

        sys.modules["discord"] = discord_module
        sys.modules["discord.app_commands"] = app_commands_module
        sys.modules["discord.ui"] = ui_module
        sys.modules["discord.ext"] = ext_module
        sys.modules["discord.ext.commands"] = commands_module

    if "yt_dlp" not in sys.modules:
        yt_dlp_module = types.ModuleType("yt_dlp")
        yt_dlp_utils_module = types.ModuleType("yt_dlp.utils")

        class YoutubeDL:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

            def extract_info(self, *args, **kwargs):
                raise NotImplementedError

        class DownloadError(Exception):
            pass

        yt_dlp_module.YoutubeDL = YoutubeDL
        yt_dlp_utils_module.DownloadError = DownloadError

        sys.modules["yt_dlp"] = yt_dlp_module
        sys.modules["yt_dlp.utils"] = yt_dlp_utils_module


install_test_stubs()

import diskzokej


class DiskzokejHelpersTest(unittest.TestCase):
    def _write_temp_alias_file(self, content: str) -> Path:
        fd, file_name = tempfile.mkstemp(dir=".")
        os.close(fd)
        alias_file = Path(file_name)
        alias_file.write_text(content, encoding="utf-8")
        self.addCleanup(lambda: alias_file.exists() and alias_file.unlink())
        return alias_file

    def test_normalize_command_prefix_accepts_any_nonempty_string(self) -> None:
        self.assertEqual(diskzokej.normalize_command_prefix("*"), "*")
        self.assertEqual(diskzokej.normalize_command_prefix("Prosim "), "Prosim ")

    def test_load_command_aliases_reads_valid_aliases(self) -> None:
        original_file = diskzokej.COMMAND_ALIASES_FILE
        alias_file = self._write_temp_alias_file(
            '{"play": ["hraj", "prehraj"], "skip": ["dalsi"]}'
        )
        diskzokej.COMMAND_ALIASES_FILE = alias_file
        try:
            aliases = diskzokej.load_command_aliases()
        finally:
            diskzokej.COMMAND_ALIASES_FILE = original_file

        self.assertEqual(aliases["play"], ["hraj", "prehraj"])
        self.assertEqual(aliases["skip"], ["dalsi"])
        self.assertEqual(aliases["help"], [])

    def test_load_command_aliases_ignores_unknown_and_duplicate_aliases(self) -> None:
        original_file = diskzokej.COMMAND_ALIASES_FILE
        alias_file = self._write_temp_alias_file(
            '{"play": ["radio", "hraj"], "neznamy": ["cokoliv"], "help": ["s mezerou"]}'
        )
        diskzokej.COMMAND_ALIASES_FILE = alias_file
        try:
            aliases = diskzokej.load_command_aliases()
        finally:
            diskzokej.COMMAND_ALIASES_FILE = original_file

        self.assertEqual(aliases["play"], ["hraj"])
        self.assertEqual(aliases["help"], [])

    def test_looks_like_url_accepts_http_and_https(self) -> None:
        self.assertTrue(diskzokej.looks_like_url("https://example.com/stream"))
        self.assertTrue(diskzokej.looks_like_url("http://example.com"))

    def test_looks_like_url_rejects_non_urls(self) -> None:
        self.assertFalse(diskzokej.looks_like_url("just text"))
        self.assertFalse(diskzokej.looks_like_url("spotify:track:123"))

    def test_is_direct_media_url_checks_suffix_case_insensitively(self) -> None:
        self.assertTrue(diskzokej.is_direct_media_url("https://cdn.example.com/live/stream.MP3"))
        self.assertFalse(diskzokej.is_direct_media_url("https://example.com/watch?v=123"))

    def test_is_youtube_playlist_url_accepts_playlist_links(self) -> None:
        self.assertTrue(
            diskzokej.is_youtube_playlist_url("https://www.youtube.com/playlist?list=abc")
        )
        self.assertTrue(
            diskzokej.is_youtube_playlist_url("https://music.youtube.com/playlist?list=abc")
        )
        self.assertTrue(
            diskzokej.is_youtube_playlist_url(
                "https://www.youtube.com/watch?v=qU0_tfLe_f8&list=RDEMJRkAOj1KA-D40XdeTzhbbw&start_radio=1"
            )
        )

    def test_is_youtube_playlist_url_rejects_video_and_search_queries(self) -> None:
        self.assertFalse(
            diskzokej.is_youtube_playlist_url("https://www.youtube.com/watch?v=abc")
        )
        self.assertFalse(diskzokej.is_youtube_playlist_url("drink jako panak"))

    def test_is_youtube_url_accepts_video_and_short_links(self) -> None:
        self.assertTrue(diskzokej.is_youtube_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(diskzokej.is_youtube_url("https://youtu.be/abc"))
        self.assertFalse(diskzokej.is_youtube_url("https://example.com/watch?v=abc"))

    def test_normalize_youtube_playlist_url_converts_watch_playlist_links(self) -> None:
        self.assertEqual(
            diskzokej.normalize_youtube_playlist_url(
                "https://www.youtube.com/watch?v=qU0_tfLe_f8&list=RDEMJRkAOj1KA-D40XdeTzhbbw&start_radio=1"
            ),
            "https://www.youtube.com/playlist?list=RDEMJRkAOj1KA-D40XdeTzhbbw",
        )

    def test_get_playlist_entry_url_builds_youtube_watch_url_from_id(self) -> None:
        self.assertEqual(
            diskzokej.get_playlist_entry_url({"id": "qU0_tfLe_f8"}),
            "https://www.youtube.com/watch?v=qU0_tfLe_f8",
        )

    def test_format_play_enqueue_status_counts_playlist_tracks(self) -> None:
        tracks = [
            diskzokej.Track("prvni", "https://example.com/1", "https://media.example.com/1", "Tester", "Youtube"),
            diskzokej.Track("druha", "https://example.com/2", "https://media.example.com/2", "Tester", "Youtube"),
        ]

        self.assertEqual(
            diskzokej.format_play_enqueue_status(tracks),
            "Pridano do fronty 2 skladeb z playlistu.",
        )

    def test_format_track_ready_status_says_when_track_starts_now(self) -> None:
        tracks = [
            diskzokej.Track(
                "pisen",
                "https://www.youtube.com/watch?v=abc",
                "https://media.example.com/abc",
                "Tester",
                "Youtube",
            )
        ]

        self.assertEqual(
            diskzokej.format_track_ready_status(tracks, starts_now=True),
            "Poustim: **pisen**\nhttps://www.youtube.com/watch?v=abc",
        )

    def test_create_track_from_data_keeps_youtube_http_headers(self) -> None:
        track = diskzokej.create_track_from_data(
            {
                "title": "pisen",
                "url": "https://rr.example.com/audio",
                "webpage_url": "https://www.youtube.com/watch?v=abc",
                "http_headers": {
                    "User-Agent": "yt-dlp",
                    "X-Bad": "radek\nnavic",
                },
            },
            "pisen",
            "Tester",
        )

        self.assertEqual(track.http_headers["User-Agent"], "yt-dlp")
        self.assertEqual(track.http_headers["X-Bad"], "radek navic")

    def test_build_ffmpeg_before_options_adds_headers(self) -> None:
        track = diskzokej.Track(
            "pisen",
            "https://www.youtube.com/watch?v=abc",
            "https://rr.example.com/audio",
            "Tester",
            "Youtube",
            {"User-Agent": "yt-dlp"},
        )

        before_options = diskzokej.build_ffmpeg_before_options(track)

        self.assertIn("-headers", before_options)
        self.assertIn("User-Agent: yt-dlp", before_options)

    def test_build_help_text_omits_panel_command(self) -> None:
        help_text = diskzokej.build_help_text().lower()

        self.assertNotIn("panel", help_text)
        self.assertNotIn("gui", help_text)

    def test_send_status_update_sends_plain_channel_message(self) -> None:
        class FakeChannel:
            def __init__(self) -> None:
                self.messages = []
                self.kwargs = []

            async def send(self, content: str, **kwargs) -> None:
                self.messages.append(content)
                self.kwargs.append(kwargs)

        channel = FakeChannel()

        asyncio.run(
            diskzokej.send_status_update(
                types.SimpleNamespace(name="Test server"),
                "Pridano do fronty.",
                preferred_channel=channel,
            )
        )

        self.assertEqual(channel.messages, ["Pridano do fronty."])
        self.assertEqual(channel.kwargs, [{"delete_after": 600}])

    def test_sync_application_commands_uses_global_sync_without_guild_ids(self) -> None:
        original_ids = diskzokej.SLASH_COMMAND_GUILD_IDS
        original_tree = diskzokej.bot.tree
        diskzokej.SLASH_COMMAND_GUILD_IDS = []
        diskzokej.bot.tree = diskzokej.commands.Bot().tree
        try:
            asyncio.run(diskzokej.sync_application_commands())

            self.assertEqual(diskzokej.bot.tree.synced_guilds, [None])
            self.assertEqual(diskzokej.bot.tree.cleared_guilds, [])
            self.assertEqual(diskzokej.bot.tree.copied_guilds, [])
        finally:
            diskzokej.SLASH_COMMAND_GUILD_IDS = original_ids
            diskzokej.bot.tree = original_tree

    def test_sync_application_commands_keeps_global_when_guild_ids_are_used(self) -> None:
        original_ids = diskzokej.SLASH_COMMAND_GUILD_IDS
        original_tree = diskzokej.bot.tree
        diskzokej.SLASH_COMMAND_GUILD_IDS = [111, 222]
        diskzokej.bot.tree = diskzokej.commands.Bot().tree
        try:
            asyncio.run(diskzokej.sync_application_commands())

            copied_ids = [guild.id for guild in diskzokej.bot.tree.copied_guilds]
            synced_ids = [
                getattr(guild, "id", None)
                for guild in diskzokej.bot.tree.synced_guilds
            ]
            cleared_ids = [
                getattr(guild, "id", None)
                for guild in diskzokej.bot.tree.cleared_guilds
            ]

            self.assertEqual(copied_ids, [111, 222])
            self.assertEqual(synced_ids, [None, 111, 222])
            self.assertEqual(cleared_ids, [111, 222])
        finally:
            diskzokej.SLASH_COMMAND_GUILD_IDS = original_ids
            diskzokej.bot.tree = original_tree

    def test_pick_first_entry_returns_first_playable_nested_entry(self) -> None:
        data = {
            "entries": [
                None,
                {"entries": [{"title": "nested"}, {"url": "https://media.example.com/audio"}]},
                {"url": "https://media.example.com/second"},
            ]
        }

        picked = diskzokej.pick_first_entry(data)

        self.assertEqual(
            picked,
            {"url": "https://media.example.com/audio"},
        )

    def test_pick_first_entry_returns_none_when_missing_urls(self) -> None:
        self.assertIsNone(diskzokej.pick_first_entry({"entries": [{"title": "missing"}]}))

    def test_resolve_radio_input_uses_aliases_case_insensitively(self) -> None:
        original_aliases = diskzokej.radio_aliases
        diskzokej.radio_aliases = {"beat": "https://example.com/beat"}
        try:
            self.assertEqual(
                diskzokej.resolve_radio_input("BeAt"),
                "https://example.com/beat",
            )
            self.assertEqual(
                diskzokej.resolve_radio_input("https://example.com/direct"),
                "https://example.com/direct",
            )
        finally:
            diskzokej.radio_aliases = original_aliases


if __name__ == "__main__":
    unittest.main()
