"""ffmpeg utility functions for music-remover."""

import re
import subprocess
import sys
from pathlib import Path
from typing import List


def find_ffmpeg() -> str:
    """Return path to ffmpeg binary (system PATH or imageio-ffmpeg)."""
    try:
        _run_silent(["ffmpeg", "-version"])
        return "ffmpeg"
    except (FileNotFoundError, RuntimeError):
        pass
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    raise RuntimeError(
        "ffmpeg не найден. Установите ffmpeg в PATH или выполните: pip install imageio-ffmpeg"
    )


def _creationflags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _run_silent(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, check=True, **_creationflags())


def run_cmd(cmd: List[str], description: str = "") -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        **_creationflags(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{description or cmd[0]} failed (code {result.returncode}):\n{result.stderr}"
        )
    return result


def _parse_duration(stderr: str) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", stderr or "")
    if match:
        h, m, s, cs = match.groups()
        return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
    raise RuntimeError("Не удалось определить длительность из вывода ffmpeg")


def get_media_duration(path: Path, ffmpeg: str) -> float:
    result = subprocess.run(
        [ffmpeg, "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        **_creationflags(),
    )
    try:
        return _parse_duration(result.stderr)
    except RuntimeError:
        raise RuntimeError(f"Не удалось определить длительность {path}")


def extract_audio_for_demucs(video_path: Path, output_path: Path, ffmpeg: str) -> None:
    """Extract full audio track preserving quality for Demucs (44.1 kHz stereo WAV)."""
    run_cmd([
        ffmpeg, "-y", "-i", str(video_path),
        "-vn", "-ac", "2", "-ar", "44100",
        "-acodec", "pcm_s16le",
        str(output_path),
    ], f"Извлечение аудио из {video_path.name}")


def merge_audio_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    ffmpeg: str,
) -> None:
    """Replace audio track in video with new audio, keeping video stream without re-encoding."""
    run_cmd([
        ffmpeg, "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        str(output_path),
    ], f"Объединение видео и аудио → {output_path.name}")


def mix_audio_tracks(
    vocals_path: Path,
    music_path: Path,
    output_path: Path,
    ffmpeg: str,
    music_volume: float = 0.10,
) -> None:
    """Mix vocals with background music. music_volume: 0.0–1.0."""
    run_cmd([
        ffmpeg, "-y",
        "-i", str(vocals_path),
        "-i", str(music_path),
        "-filter_complex",
        f"[1:a]volume={music_volume}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=3[out]",
        "-map", "[out]",
        "-c:a", "pcm_s16le",
        str(output_path),
    ], "Микширование вокала и музыки")


def loop_audio_to_duration(
    audio_path: Path,
    target_duration: float,
    output_path: Path,
    ffmpeg: str,
) -> None:
    """Loop audio until it reaches target_duration seconds."""
    run_cmd([
        ffmpeg, "-y",
        "-stream_loop", "-1",
        "-i", str(audio_path),
        "-t", str(target_duration),
        "-c:a", "pcm_s16le",
        str(output_path),
    ], "Зацикливание музыки до нужной длины")


def get_video_info(video_path: Path, ffmpeg: str) -> dict:
    """Return basic video metadata: duration, width, height, fps."""
    result = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-f", "null", "-"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        **_creationflags(),
    )
    info = {"duration": None, "width": None, "height": None, "fps": None}
    stderr = result.stderr or ""
    try:
        info["duration"] = _parse_duration(stderr)
    except RuntimeError:
        pass
    res_match = re.search(r"(\d{3,5})x(\d{3,5})", stderr)
    if res_match:
        info["width"] = int(res_match.group(1))
        info["height"] = int(res_match.group(2))
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
    if fps_match:
        info["fps"] = float(fps_match.group(1))
    return info


def has_audio_stream(video_path: Path, ffmpeg: str) -> bool:
    """Check if the video file contains at least one audio stream."""
    result = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-f", "null", "-"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        **_creationflags(),
    )
    return "Audio:" in (result.stderr or "")
