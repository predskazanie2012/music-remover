"""
Audio separation using Demucs: extracts vocals and background music as separate tracks.
First run downloads the model automatically (~80-320MB depending on model).
"""

import os
import subprocess
import sys
from pathlib import Path

from pipeline.ffmpeg_utils import _creationflags


def _select_device() -> str:
    try:
        import torch
    except Exception:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def separate_audio(
    audio_path: Path,
    output_dir: Path,
    model: str = "htdemucs",
    progress_callback=None,
    cpu_threads: int = 0,
) -> tuple:
    """
    Separate audio into vocals and background (no_vocals).

    Args:
        model: Demucs model name — 'htdemucs' (fast) or 'htdemucs_ft' (quality)
        cpu_threads: number of CPU threads (0 = auto: half of available cores)
    Returns:
        (vocals_path, background_path) — both are WAV files at 44.1kHz stereo
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if cpu_threads == 0:
        cpu_threads = max(1, (os.cpu_count() or 4) // 2)

    device = _select_device()

    if progress_callback:
        progress_callback(
            f"Разделение аудио (Demucs {model}, {device.upper()}, {cpu_threads} ядер CPU)..."
        )

    worker = Path(__file__).parent / "demucs_worker.py"
    cmd = [
        sys.executable, str(worker),
        "-n", model,
        "--two-stems=vocals",
        "-d", device,
        "-j", str(cpu_threads),
        "--out", str(output_dir),
        str(audio_path),
    ]

    env = os.environ.copy()
    env["MUSIC_REMOVER_TORCH_THREADS"] = str(cpu_threads)
    env["OMP_NUM_THREADS"] = str(cpu_threads)
    env["MKL_NUM_THREADS"] = str(cpu_threads)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        **_creationflags(),
    )

    last_pct = 0
    output_lines: list = []
    for line in process.stdout:
        line = line.strip()
        if line:
            output_lines.append(line)
            if progress_callback:
                if "%" in line:
                    try:
                        pct = int(line.split("%")[0].split()[-1])
                        if pct != last_pct:
                            last_pct = pct
                            progress_callback(f"Demucs: {pct}%")
                    except (ValueError, IndexError):
                        pass

    process.wait()
    if process.returncode != 0:
        tail = "\n".join(output_lines[-30:]) if output_lines else "(нет вывода)"
        raise RuntimeError(
            f"Demucs завершился с кодом {process.returncode}.\n{tail}"
        )

    stem = audio_path.stem
    model_dir = output_dir / model / stem

    vocals_path = model_dir / "vocals.wav"
    background_path = model_dir / "no_vocals.wav"

    if not vocals_path.exists():
        raise RuntimeError(f"Demucs не создал файл вокала. Ожидался: {vocals_path}")
    if not background_path.exists():
        raise RuntimeError(f"Demucs не создал файл музыки. Ожидался: {background_path}")

    return vocals_path, background_path
