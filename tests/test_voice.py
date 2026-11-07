import asyncio
import time
import types
import unittest

from tests import _setup
from app import config, db, handlers, tasks_db, voice
from app.render import build_source_text, render_tasks


def run(coro):
    return asyncio.run(coro)


def seg(logprob, silence=0.0):
    return types.SimpleNamespace(avg_logprob=logprob, no_speech_prob=silence)


def attachment(kind, url="https://example.com/voice.ogg"):
    return types.SimpleNamespace(type=kind, payload=types.SimpleNamespace(url=url))


def message_with(*attachments):
    return types.SimpleNamespace(body=types.SimpleNamespace(attachments=list(attachments)))


class Confidence(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(voice.confidence_from(types.SimpleNamespace(segments=[seg(-0.1), seg(-0.2)])), "high")
        self.assertEqual(voice.confidence_from(types.SimpleNamespace(segments=[seg(-0.6)])), "medium")
        self.assertEqual(voice.confidence_from(types.SimpleNamespace(segments=[seg(-1.4)])), "low")
        self.assertEqual(voice.confidence_from(types.SimpleNamespace(segments=[seg(-0.1, silence=0.9)])), "low")

    def test_unknown_when_service_gives_no_data(self):
        self.assertEqual(voice.confidence_from(types.SimpleNamespace(text="привет")), "unknown")
        self.assertEqual(voice.confidence_from({"text": "привет"}), "unknown")
        self.assertEqual(voice.confidence_from("просто строка"), "unknown")

    def test_dict_response(self):
        self.assertEqual(voice.confidence_from({"segments": [{"avg_logprob": -0.1, "no_speech_prob": 0.0}]}), "high")


class FindAudio(unittest.TestCase):
    def test_finds_audio(self):
        self.assertEqual(voice.audio_url(message_with(attachment("audio"))), "https://example.com/voice.ogg")

    def test_enum_like_type(self):
        enum_like = types.SimpleNamespace(value="AUDIO")
        self.assertIsNotNone(voice.audio_url(message_with(attachment(enum_like))))

    def test_ignores_other_attachments(self):
        self.assertIsNone(voice.audio_url(message_with(attachment("image"), attachment("file"))))
        self.assertIsNone(voice.audio_url(message_with()))
        self.assertIsNone(voice.audio_url(types.SimpleNamespace(body=types.SimpleNamespace(attachments=None))))

    def test_only_https_download(self):
        with self.assertRaises(ValueError):
            run(voice.download("http://example.com/a.ogg"))


class Transcribe(unittest.TestCase):
    def test_uses_verbose_and_falls_back(self):
        calls = []

        class Transcriptions:
            async def create(self, **kwargs):
                calls.append(kwargs.get("response_format"))
                if kwargs.get("response_format") == "verbose_json":
                    raise RuntimeError("не поддерживается")
                return types.SimpleNamespace(text="  алгебра номер пять  ")

        old = (voice.client, config.STT_MODE)
        voice.client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=Transcriptions()))
        config.STT_MODE = "whisper"
        try:
            text, conf = run(voice.transcribe(b"audio", ".ogg"))
        finally:
            voice.client, config.STT_MODE = old
        self.assertEqual((text, conf), ("алгебра номер пять", "unknown"))
        self.assertEqual(calls, ["verbose_json", None])


class VoicePipeline(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.ts = int(time.time() * 1000)
        self.saved = (voice.download, voice.transcribe, handlers.extract_tasks, config.STT_ENABLED)
        config.STT_ENABLED = True

        async def fake_download(url):
            return b"bytes", ".ogg"

        async def fake_extract(text, day, ask):
            return [{"kind": "homework", "subject": "Алгебра", "text": "№215", "due": None}]

        voice.download, handlers.extract_tasks = fake_download, fake_extract

    def tearDown(self):
        voice.download, voice.transcribe, handlers.extract_tasks, config.STT_ENABLED = self.saved

    def fake_transcribe(self, text, conf):
        async def f(data, ext):
            return text, conf
        voice.transcribe = f

    def test_voice_becomes_record_with_confidence(self):
        self.fake_transcribe("Алгебра, номер двести пятнадцать до пятницы, задание", "low")
        run(handlers.process_voice(1, "v1", "https://x/y.ogg", self.ts, "Анна"))
        row = tasks_db.get_tasks(1)[0]
        self.assertEqual((row[7], row[8]), ("voice", "low"))
        text, _ = render_tasks(tasks_db.get_tasks(1))
        self.assertIn("🎙 ⚠ проверьте", text)
        source = build_source_text(tasks_db.get_task(row[0]), db.get_message(1, "v1"))
        self.assertIn("Расшифровка ИИ", source)
        self.assertIn("Аудио не сохраняется", source)

    def test_confident_voice_has_no_warning(self):
        self.fake_transcribe("Алгебра, задание номер двести пятнадцать", "high")
        run(handlers.process_voice(1, "v1", "https://x/y.ogg", self.ts, "Анна"))
        text, _ = render_tasks(tasks_db.get_tasks(1))
        self.assertIn("🎙", text)
        self.assertNotIn("⚠", text)

    def test_chatter_in_voice_is_not_stored(self):
        self.fake_transcribe("всем привет как дела", "high")
        run(handlers.process_voice(1, "v1", "https://x/y.ogg", self.ts, "Анна"))
        self.assertEqual(tasks_db.get_tasks(1), [])
        self.assertIsNone(db.get_message(1, "v1"))

    def test_stt_error_is_harmless(self):
        async def broken(data, ext):
            raise RuntimeError("сервис недоступен")
        voice.transcribe = broken
        run(handlers.process_voice(1, "v1", "https://x/y.ogg", self.ts, "Анна"))
        self.assertEqual(tasks_db.get_tasks(1), [])

    def test_disabled_does_nothing(self):
        config.STT_ENABLED = False
        self.fake_transcribe("Алгебра, задание номер двести пятнадцать", "high")
        run(handlers.process_voice(1, "v1", "https://x/y.ogg", self.ts, "Анна"))
        self.assertEqual(tasks_db.get_tasks(1), [])


if __name__ == "__main__":
    unittest.main()


class ChatMode(unittest.TestCase):
    """Расшифровка моделью с аудио-входом через обычный чат-запрос."""

    def setUp(self):
        self.old = (voice.client, config.STT_MODE, config.STT_MODEL)
        config.STT_MODE, config.STT_MODEL = "chat", "glm-5.3-flash"

    def tearDown(self):
        voice.client, config.STT_MODE, config.STT_MODEL = self.old

    def fake_client(self, fail_first=0, answer="Алгебра номер пять\nCONFIDENCE: high"):
        calls = []

        class Completions:
            async def create(inner, **kwargs):
                calls.append(kwargs)
                if len(calls) <= fail_first:
                    raise RuntimeError("не принимаю такой формат")
                msg = types.SimpleNamespace(content=answer)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

        voice.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
        return calls

    def test_split_confidence(self):
        self.assertEqual(voice.split_confidence("Привет\nCONFIDENCE: high"), ("Привет", "high"))
        self.assertEqual(voice.split_confidence("«Привет»\n\nconfidence: LOW."), ("Привет", "low"))
        self.assertEqual(voice.split_confidence("Привет"), ("Привет", "unknown"))
        self.assertEqual(voice.split_confidence(""), ("", "unknown"))
        self.assertEqual(voice.split_confidence(None), ("", "unknown"))

    def test_chat_mode_sends_audio_and_parses(self):
        calls = self.fake_client()
        text, conf = run(voice.transcribe(b"audio", ".ogg"))
        self.assertEqual((text, conf), ("Алгебра номер пять", "high"))
        self.assertEqual(calls[0]["model"], "glm-5.3-flash")
        part = calls[0]["messages"][0]["content"][1]
        self.assertEqual(part["type"], "input_audio")
        self.assertEqual(part["input_audio"]["format"], "ogg")

    def test_falls_back_to_other_audio_format(self):
        calls = self.fake_client(fail_first=1)
        run(voice.transcribe(b"audio", ".m4a"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["messages"][0]["content"][1]["type"], "file")

    def test_all_ways_fail_raises(self):
        self.fake_client(fail_first=99)
        with self.assertRaises(RuntimeError):
            run(voice.transcribe(b"audio", ".ogg"))

    def test_whisper_mode_still_available(self):
        config.STT_MODE = "whisper"

        class Transcriptions:
            async def create(inner, **kwargs):
                return types.SimpleNamespace(text="привет", segments=[seg(-0.1)])

        voice.client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=Transcriptions()))
        self.assertEqual(run(voice.transcribe(b"a", ".ogg")), ("привет", "high"))


class MaxTranscription(unittest.TestCase):
    """Если MAX сам прислал расшифровку (поле transcription), свой ИИ не нужен."""

    def setUp(self):
        _setup.reset_db()
        self.ts = int(time.time() * 1000)
        self.saved = (voice.download, voice.transcribe, handlers.extract_tasks)

        async def forbidden(*a, **k):
            raise AssertionError("скачивать и расшифровывать не нужно")

        async def fake_extract(text, day, ask):
            return [{"kind": "homework", "subject": "Биология", "text": "№135", "due": None}]

        voice.download = voice.transcribe = forbidden
        handlers.extract_tasks = fake_extract

    def tearDown(self):
        voice.download, voice.transcribe, handlers.extract_tasks = self.saved

    def test_audio_info_reads_transcription(self):
        att = types.SimpleNamespace(
            type="audio", payload=types.SimpleNamespace(url="https://x/a.m4a", transcription=" Биология ДЗ ")
        )
        message = types.SimpleNamespace(body=types.SimpleNamespace(attachments=[att]))
        self.assertEqual(voice.audio_info(message), ("https://x/a.m4a", "Биология ДЗ"))

    def test_audio_info_without_transcription(self):
        att = types.SimpleNamespace(type="audio", payload=types.SimpleNamespace(url="https://x/a.m4a"))
        message = types.SimpleNamespace(body=types.SimpleNamespace(attachments=[att]))
        self.assertEqual(voice.audio_info(message), ("https://x/a.m4a", None))

    def test_transcription_becomes_record_without_own_stt(self):
        run(handlers.process_voice(1, "v1", "https://x/a.m4a", self.ts, "Анна",
                                   transcription="Биология ДЗ номер 135 страница 163"))
        row = tasks_db.get_tasks(1)[0]
        self.assertEqual(row[7], "voice")
        self.assertEqual(row[2], "Биология")