"""
Main processing pipeline for music-remover.

Modes:
  remove  — keep only vocals (speech), strip all background music
  replace — keep vocals, replace background music with a custom track
"""

import random
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from pipeline.ffmpeg_utils import (
    find_ffmpeg,
    extract_audio_for_demucs,
    merge_audio_video,
    mix_audio_tracks,
    loop_audio_to_duration,
    get_media_duration,
    get_video_info,
    has_audio_stream,
)
from pipeline.separator import separate_audio


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv", ".wmv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}


def find_audio_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


@dataclass
class JobConfig:
    mode: str = "remove"             # "remove" | "replace"
    demucs_model: str = "htdemucs"   # "htdemucs" | "htdemucs_ft"
    cpu_threads: int = 0             # 0 = auto
    music_volume: float = 0.10       # 0.0–1.0, only for replace mode
    music_file: Optional[str] = None # path to replacement music (replace mode)
    music_files: list[str] = field(default_factory=list) # random pool for replace mode
    output_folder: str = ""          # where to save results


def _choose_music_file(config: JobConfig) -> Path:
    if config.music_files:
        existing = [Path(p) for p in config.music_files if Path(p).exists()]
        if not existing:
            raise RuntimeError("Папка с музыкой не содержит доступных аудиофайлов.")
        return random.choice(existing)

    if not config.music_file:
        raise RuntimeError("Режим 'replace': не указан файл или папка новой музыки.")

    music_src = Path(config.music_file)
    if not music_src.exists():
        raise RuntimeError(f"Файл музыки не найден: {music_src}")
    return music_src


def process_single_video(
    video_path: Path,
    config: JobConfig,
    progress_callback: Callable[[str, int], None],
    work_dir: Path,
) -> Path:
    """
    Process one video file: separate vocals, optionally mix with new music,
    merge back into video. Returns path to output file.
    """
    ffmpeg = find_ffmpeg()

    if not has_audio_stream(video_path, ffmpeg):
        raise RuntimeError(f"Видео не содержит аудиодорожки: {video_path.name}")

    info = get_video_info(video_path, ffmpeg)
    duration = info.get("duration") or get_media_duration(video_path, ffmpeg)

    stem = video_path.stem
    suffix = video_path.suffix

    progress_callback("Извлечение аудио...", 10)
    raw_audio = work_dir / f"{stem}_raw.wav"
    extract_audio_for_demucs(video_path, raw_audio, ffmpeg)

    progress_callback("Разделение вокала и музыки (Demucs)...", 20)
    demucs_dir = work_dir / "demucs_out"

    def _demucs_progress(msg: str):
        progress_callback(msg, None)

    vocals_path, _bg_path = separate_audio(
        audio_path=raw_audio,
        output_dir=demucs_dir,
        model=config.demucs_model,
        progress_callback=_demucs_progress,
        cpu_threads=config.cpu_threads,
    )

    if config.mode == "remove":
        progress_callback("Сборка итогового видео (вокал без музыки)...", 85)
        final_audio = vocals_path

    elif config.mode == "replace":
        music_src = _choose_music_file(config)

        progress_callback(f"Подготовка новой музыки: {music_src.name}", 75)
        music_looped = work_dir / f"{stem}_music_looped.wav"
        loop_audio_to_duration(music_src, duration, music_looped, ffmpeg)

        progress_callback("Микширование вокала с новой музыкой...", 82)
        mixed = work_dir / f"{stem}_mixed.wav"
        mix_audio_tracks(
            vocals_path=vocals_path,
            music_path=music_looped,
            output_path=mixed,
            ffmpeg=ffmpeg,
            music_volume=config.music_volume,
        )
        final_audio = mixed

    else:
        raise ValueError(f"Неизвестный режим: {config.mode}")

    progress_callback("Финальная сборка видео...", 88)
    out_dir = Path(config.output_folder) if config.output_folder else video_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    mode_label = "novocals" if config.mode == "remove" else "newmusic"
    output_path = out_dir / f"{stem}_{mode_label}{suffix}"

    # avoid collision if file exists
    counter = 1
    base_output = output_path
    while output_path.exists():
        output_path = out_dir / f"{stem}_{mode_label}_{counter}{suffix}"
        counter += 1

    merge_audio_video(video_path, final_audio, output_path, ffmpeg)
    progress_callback(f"Готово: {output_path.name}", 100)
    return output_path


def download_youtube(
    url: str,
    dest_dir: Path,
    progress_callback: Callable[[str], None],
) -> list:
    """
    Download video(s) from YouTube URL using yt-dlp.
    Returns list of downloaded video paths.
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp не установлен. Выполните: pip install yt-dlp"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    def _hook(d):
        status = d.get("status", "")
        if status == "downloading":
            pct = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            progress_callback(f"Скачивание: {pct} ({speed})")
        elif status == "finished":
            progress_callback(f"Загружено: {d['filename']}")
            downloaded.append(Path(d["filename"]))

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(dest_dir / "%(title)s.%(ext)s"),
        "progress_hooks": [_hook],
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    if not downloaded:
        downloaded = [p for p in dest_dir.iterdir()
                      if p.suffix.lower() in VIDEO_EXTENSIONS]

    return downloaded
