import sys
import types
import unittest


def install_test_stubs() -> None:
    if "discord" not in sys.modules:
        discord_module = types.ModuleType("discord")

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

        discord_module.Intents = Intents
        discord_module.FFmpegPCMAudio = FFmpegPCMAudio
        discord_module.Guild = object
        discord_module.VoiceClient = object
        discord_module.TextChannel = object
        discord_module.VoiceChannel = object
        discord_module.Member = object
        discord_module.ClientException = Exception

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
    def test_looks_like_url_accepts_http_and_https(self) -> None:
        self.assertTrue(diskzokej.looks_like_url("https://example.com/stream"))
        self.assertTrue(diskzokej.looks_like_url("http://example.com"))

    def test_looks_like_url_rejects_non_urls(self) -> None:
        self.assertFalse(diskzokej.looks_like_url("just text"))
        self.assertFalse(diskzokej.looks_like_url("spotify:track:123"))

    def test_is_direct_media_url_checks_suffix_case_insensitively(self) -> None:
        self.assertTrue(diskzokej.is_direct_media_url("https://cdn.example.com/live/stream.MP3"))
        self.assertFalse(diskzokej.is_direct_media_url("https://example.com/watch?v=123"))

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
