import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import asr
from formats import Segment

UNIFIED_ENV = "DRAGON_UNIFIED_ASR_HOME"


def env_without_unified():
    return {key: value for key, value in os.environ.items() if key != UNIFIED_ENV}


class AsrTests(unittest.TestCase):
    def test_available_and_choose_backends(self):
        def located(name):
            return (
                f"/bin/{name}" if name in {"curl", "whisper", "agent-reach"} else None
            )

        with (
            patch.dict(os.environ, {"GROQ_API_KEY": "secret"}),
            patch("asr.unified_asr_available", return_value=False),
            patch("asr.shutil.which", side_effect=located),
        ):
            self.assertEqual(
                asr.available_backends(), ("groq", "whisper", "agent-reach")
            )
            self.assertEqual(asr.choose_backend("auto"), "groq")
            self.assertEqual(asr.choose_backend("whisper"), "whisper")
        with patch("asr.available_backends", return_value=()):
            with self.assertRaisesRegex(RuntimeError, "没有可用 ASR"):
                asr.choose_backend("auto")
            with self.assertRaisesRegex(RuntimeError, "当前不可用"):
                asr.choose_backend("groq")

    def test_unified_asr_home_env_priority(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            default = root / "default-unified-asr"
            env_home = root / "env-unified-asr"
            (env_home / "scripts").mkdir(parents=True)
            (env_home / "scripts" / "transcribe.py").write_text(
                "# helper", encoding="utf-8"
            )

            with (
                patch.dict(os.environ, env_without_unified(), clear=True),
                patch("asr.UNIFIED_ASR_DEFAULT_HOME", default),
            ):
                self.assertEqual(asr.unified_asr_home(), default)
                self.assertFalse(asr.unified_asr_available())

            with (
                patch.dict(
                    os.environ,
                    {**env_without_unified(), UNIFIED_ENV: str(env_home)},
                    clear=True,
                ),
                patch("asr.UNIFIED_ASR_DEFAULT_HOME", default),
            ):
                self.assertEqual(asr.unified_asr_home(), env_home)
                self.assertTrue(asr.unified_asr_available())

            with (
                patch.dict(os.environ, {**env_without_unified(), UNIFIED_ENV: "  "}, clear=True),
                patch("asr.UNIFIED_ASR_DEFAULT_HOME", default),
            ):
                self.assertEqual(asr.unified_asr_home(), default)

    def test_available_backends_unified_fw_ordering(self):
        def located(name):
            return (
                f"/bin/{name}" if name in {"curl", "whisper", "agent-reach"} else None
            )

        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            (root / "scripts" / "transcribe.py").write_text("# helper", encoding="utf-8")
            env = env_without_unified()

            with (
                patch.dict(os.environ, env, clear=True),
                patch("asr.UNIFIED_ASR_DEFAULT_HOME", root),
                patch("asr.shutil.which", side_effect=located),
            ):
                self.assertEqual(
                    asr.available_backends(),
                    ("unified-fw", "whisper", "agent-reach"),
                )
                self.assertEqual(asr.choose_backend("auto"), "unified-fw")

            with (
                patch.dict(
                    os.environ,
                    {**env, "GROQ_API_KEY": "secret", UNIFIED_ENV: str(root)},
                    clear=True,
                ),
                patch("asr.shutil.which", side_effect=located),
            ):
                self.assertEqual(
                    asr.available_backends(),
                    ("groq", "unified-fw", "whisper", "agent-reach"),
                )
                self.assertEqual(asr.choose_backend("auto"), "groq")
                self.assertEqual(asr.choose_backend("unified-fw"), "unified-fw")

            with (
                patch.dict(os.environ, {**env, "GROQ_API_KEY": "secret"}, clear=True),
                patch("asr.UNIFIED_ASR_DEFAULT_HOME", root / "missing"),
                patch("asr.shutil.which", side_effect=located),
            ):
                self.assertEqual(
                    asr.available_backends(),
                    ("groq", "whisper", "agent-reach"),
                )
                self.assertEqual(asr.choose_backend("auto"), "groq")

    @patch("asr.subprocess.run")
    def test_media_duration(self, run):
        run.return_value = Mock(stdout="12.5\n")
        self.assertEqual(asr.media_duration(Path("a.mp3")), 12.5)
        run.return_value = Mock(stdout="bad")
        self.assertEqual(asr.media_duration(Path("a.mp3")), 0.0)

    def test_split_audio(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)

            def fake_run(*_args, **_kwargs):
                (root / "chunk_000.mp3").write_bytes(b"audio")
                return Mock(returncode=0, stderr="")

            with patch("asr.subprocess.run", side_effect=fake_run):
                self.assertEqual(len(asr.split_audio(Path("source.mp3"), root)), 1)

    def test_groq_transcription_with_segments_and_text_fallback(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            chunks = (root / "a.mp3", root / "b.mp3")
            for chunk in chunks:
                chunk.write_bytes(b"a")
            payloads = [
                {"segments": [{"start": 1, "end": 2, "text": "第一句"}]},
                {"text": "第二句"},
            ]
            with (
                patch("asr.split_audio", return_value=chunks),
                patch("asr._groq_chunk", side_effect=payloads),
                patch("asr.media_duration", return_value=10),
            ):
                self.assertEqual(
                    asr.transcribe_groq(Path("source.mp3"), root),
                    (Segment(1, 2, "第一句"), Segment(10, 20, "第二句")),
                )

    def test_groq_chunk_success_and_invalid_json(self):
        good = Mock(returncode=0, stdout='{"text":"ok"}', stderr="")
        with (
            patch.dict(os.environ, {"GROQ_API_KEY": "secret"}),
            patch("asr.subprocess.run", return_value=good),
        ):
            self.assertEqual(asr._groq_chunk(Path("a.mp3"))["text"], "ok")
        bad = Mock(returncode=0, stdout="not-json", stderr="")
        with (
            patch.dict(os.environ, {"GROQ_API_KEY": "secret"}),
            patch("asr.subprocess.run", return_value=bad),
            self.assertRaisesRegex(RuntimeError, "无法解析"),
        ):
            asr._groq_chunk(Path("a.mp3"))

    def test_whisper_and_agent_reach(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)

            def whisper_run(*_args, **_kwargs):
                (root / "audio.json").write_text(
                    json.dumps({"segments": [{"start": 0, "end": 2, "text": "你好"}]}),
                    encoding="utf-8",
                )
                return Mock(returncode=0, stderr="")

            with patch("asr.subprocess.run", side_effect=whisper_run):
                self.assertEqual(
                    asr.transcribe_whisper(Path("audio.mp3"), root),
                    (Segment(0, 2, "你好"),),
                )

            def reach_run(*_args, **_kwargs):
                (root / "agent-reach-transcript.txt").write_text(
                    "逐字稿", encoding="utf-8"
                )
                return Mock(returncode=0, stderr="")

            with (
                patch("asr.subprocess.run", side_effect=reach_run),
                patch("asr.media_duration", return_value=5),
            ):
                self.assertEqual(
                    asr.transcribe_agent_reach(Path("audio.mp3"), root),
                    (Segment(0, 5, "逐字稿"),),
                )

    def test_unified_transcription_json_text_fallback_and_failure(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            helper = root / "scripts" / "transcribe.py"
            helper.write_text("# helper", encoding="utf-8")
            commands: list[list[str]] = []

            def json_run(command, **_kwargs):
                commands.append(command)
                (root / "transcript.json").write_text(
                    json.dumps(
                        {"segments": [{"start": 0, "end": 2.5, "text": "你好"}]}
                    ),
                    encoding="utf-8",
                )
                return Mock(returncode=0, stderr="")

            with patch.dict(
                os.environ,
                {**env_without_unified(), UNIFIED_ENV: str(root)},
                clear=True,
            ):
                with patch("asr.subprocess.run", side_effect=json_run):
                    self.assertEqual(
                        asr.transcribe_unified(Path("audio.mp3"), root),
                        (Segment(0, 2.5, "你好"),),
                    )
                command = commands[0]
                self.assertEqual(
                    command,
                    [sys.executable, str(helper), "audio.mp3", "--output-dir", str(root)],
                )

                (root / "transcript.json").unlink()
                (root / "text.txt").write_text("逐字稿\n", encoding="utf-8")
                with patch("asr.subprocess.run", return_value=Mock(returncode=0, stderr="")):
                    with patch("asr.media_duration", return_value=5):
                        self.assertEqual(
                            asr.transcribe_unified(Path("audio.mp3"), root),
                            (Segment(0, 5, "逐字稿"),),
                        )

                with patch(
                    "asr.subprocess.run",
                    return_value=Mock(returncode=3, stderr="依赖缺失"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "统一 ASR 转录失败"):
                        asr.transcribe_unified(Path("audio.mp3"), root)

    def test_transcribe_dispatch(self):
        expected = (Segment(0, 1, "ok"),)
        with (
            patch("asr.choose_backend", return_value="groq"),
            patch("asr.transcribe_groq", return_value=expected),
        ):
            self.assertEqual(asr.transcribe(Path("a"), Path("b")), ("groq", expected))
        with (
            patch("asr.choose_backend", return_value="unified-fw"),
            patch("asr.transcribe_unified", return_value=expected),
        ):
            self.assertEqual(
                asr.transcribe(Path("a"), Path("b")), ("unified-fw", expected)
            )


if __name__ == "__main__":
    unittest.main()
