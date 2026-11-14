"""ASR backend selection and transcription."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from formats import Segment, clean_text

UNIFIED_ASR_ENV = "DRAGON_UNIFIED_ASR_HOME"
UNIFIED_ASR_DEFAULT_HOME = Path.home() / ".agents" / "skills" / "dragon__unified-asr"


def unified_asr_home() -> Path:
    """统一 ASR helper 根目录：env DRAGON_UNIFIED_ASR_HOME 优先，否则默认技能路径。"""
    override = os.environ.get(UNIFIED_ASR_ENV, "").strip()
    return Path(override) if override else UNIFIED_ASR_DEFAULT_HOME


def unified_asr_available() -> bool:
    """True 当统一 ASR helper 提供 scripts/transcribe.py 入口。"""
    return (unified_asr_home() / "scripts" / "transcribe.py").is_file()


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def available_backends() -> tuple[str, ...]:
    found: list[str] = []
    if os.environ.get("GROQ_API_KEY") and shutil.which("curl"):
        found.append("groq")
    if unified_asr_available():
        found.append("unified-fw")
    if shutil.which("whisper"):
        found.append("whisper")
    if shutil.which("agent-reach"):
        found.append("agent-reach")
    return tuple(found)


def choose_backend(requested: str) -> str:
    available = available_backends()
    if requested != "auto":
        if requested not in available:
            raise RuntimeError(
                f"ASR 后端 {requested} 当前不可用；可用后端：{', '.join(available) or '无'}"
            )
        return requested
    if not available:
        raise RuntimeError(
            "没有可用 ASR。请配置 GROQ_API_KEY、安装统一 ASR（unified-asr 技能，"
            "helper 路径可用 DRAGON_UNIFIED_ASR_HOME 指定）、安装 openai-whisper，"
            "或配置 agent-reach"
        )
    return available[0]


def split_audio(audio: Path, work_dir: Path, seconds: int = 600) -> tuple[Path, ...]:
    pattern = str(work_dir / "chunk_%03d.mp3")
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(audio),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "32k",
        "-f",
        "segment",
        "-segment_time",
        str(seconds),
        pattern,
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=1800
    )
    chunks = tuple(sorted(work_dir.glob("chunk_*.mp3")))
    if result.returncode != 0 or not chunks:
        raise RuntimeError(f"音频切片失败：{result.stderr[-500:]}")
    return chunks


def _groq_chunk(path: Path) -> dict:
    command = [
        "curl",
        "-fsS",
        "--max-time",
        "600",
        "--retry",
        "4",
        "--retry-all-errors",
        "--retry-delay",
        "5",
        "https://api.groq.com/openai/v1/audio/transcriptions",
        "-H",
        "@-",
        "-F",
        f"file=@{path}",
        "-F",
        "model=whisper-large-v3",
        "-F",
        "language=zh",
        "-F",
        "response_format=verbose_json",
    ]
    result = subprocess.run(
        command,
        input=f"Authorization: Bearer {os.environ['GROQ_API_KEY']}\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=660,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Groq 转录失败：{result.stderr[-500:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Groq 返回了无法解析的结果") from exc


def transcribe_groq(audio: Path, work_dir: Path) -> tuple[Segment, ...]:
    output: list[Segment] = []
    offset = 0.0
    for chunk in split_audio(audio, work_dir):
        payload = _groq_chunk(chunk)
        raw_segments = payload.get("segments") or []
        if raw_segments:
            output.extend(
                Segment(
                    start=offset + float(item.get("start", 0)),
                    end=offset + float(item.get("end", item.get("start", 0) + 1)),
                    text=clean_text(str(item.get("text", ""))),
                )
                for item in raw_segments
                if clean_text(str(item.get("text", "")))
            )
        else:
            text = clean_text(str(payload.get("text", "")))
            if text:
                output.append(Segment(offset, offset + media_duration(chunk), text))
        offset += media_duration(chunk)
    return tuple(output)


def transcribe_whisper(audio: Path, work_dir: Path) -> tuple[Segment, ...]:
    command = [
        "whisper",
        str(audio),
        "--model",
        "small",
        "--language",
        "zh",
        "--output_format",
        "json",
        "--output_dir",
        str(work_dir),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=7200
    )
    json_files = tuple(work_dir.glob("*.json"))
    if result.returncode != 0 or not json_files:
        raise RuntimeError(f"本地 Whisper 转录失败：{result.stderr[-500:]}")
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    return tuple(
        Segment(float(item["start"]), float(item["end"]), clean_text(str(item["text"])))
        for item in payload.get("segments", [])
        if clean_text(str(item.get("text", "")))
    )


def transcribe_unified(audio: Path, work_dir: Path) -> tuple[Segment, ...]:
    """调用 unified-asr 技能的本地 helper（faster-whisper，CUDA 自动检测，带转写缓存）。"""
    helper = unified_asr_home() / "scripts" / "transcribe.py"
    if not helper.is_file():
        raise RuntimeError(f"统一 ASR helper 不存在：{helper}")
    result = subprocess.run(
        [sys.executable, str(helper), str(audio), "--output-dir", str(work_dir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=7200,
    )
    if result.returncode != 0:
        raise RuntimeError(f"统一 ASR 转录失败：{result.stderr[-500:]}")
    segments: list[Segment] = []
    payload_file = work_dir / "transcript.json"
    if payload_file.exists():
        try:
            payload = json.loads(payload_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
        raw = payload.get("segments") if isinstance(payload, dict) else payload
        if isinstance(raw, list):
            segments = [
                Segment(
                    float(item["start"]),
                    float(item["end"]),
                    clean_text(str(item.get("text", ""))),
                )
                for item in raw
                if isinstance(item, dict) and clean_text(str(item.get("text", "")))
            ]
    if not segments:
        text_file = work_dir / "text.txt"
        if not text_file.exists():
            raise RuntimeError("统一 ASR 转录失败：未产出 transcript.json 或 text.txt")
        text = clean_text(text_file.read_text(encoding="utf-8", errors="replace"))
        if text:
            segments = [Segment(0, max(1.0, media_duration(audio)), text)]
    return tuple(segments)


def transcribe_agent_reach(audio: Path, work_dir: Path) -> tuple[Segment, ...]:
    output = work_dir / "agent-reach-transcript.txt"
    result = subprocess.run(
        ["agent-reach", "transcribe", str(audio), "-o", str(output)],
        capture_output=True,
        text=True,
        check=False,
        timeout=7200,
    )
    if result.returncode != 0 or not output.exists():
        raise RuntimeError(f"agent-reach 转录失败：{result.stderr[-500:]}")
    text = clean_text(output.read_text(encoding="utf-8", errors="replace"))
    return (Segment(0, max(1.0, media_duration(audio)), text),) if text else ()


def transcribe(
    audio: Path, work_dir: Path, requested: str = "auto"
) -> tuple[str, tuple[Segment, ...]]:
    backend = choose_backend(requested)
    handlers = {
        "groq": transcribe_groq,
        "unified-fw": transcribe_unified,
        "whisper": transcribe_whisper,
        "agent-reach": transcribe_agent_reach,
    }
    segments = handlers[backend](audio, work_dir)
    if not segments:
        raise RuntimeError(f"{backend} 没有返回可用文字")
    return backend, segments
