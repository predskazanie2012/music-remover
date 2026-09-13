"""
Demucs worker with torchaudio patched to use soundfile instead of torchcodec.

torchaudio 2.5+ requires torchcodec which needs special FFmpeg DLLs on Windows.
This wrapper monkey-patches torchaudio.load / torchaudio.save before demucs imports
them, so demucs uses soundfile (libsndfile) for audio I/O instead.

Usage: python demucs_worker.py [demucs args...]
"""

import os
import sys
import numpy as np
import soundfile as sf
import torch
import torchaudio


def _configure_torch_threads():
    raw_threads = os.environ.get("MUSIC_REMOVER_TORCH_THREADS")
    if not raw_threads:
        return
    try:
        threads = max(1, int(raw_threads))
    except ValueError:
        return

    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(max(1, min(threads, 4)))
    except RuntimeError:
        pass


_configure_torch_threads()


def _load(filepath, frame_offset=0, num_frames=-1, normalize=True,
          channels_first=True, format=None, backend=None, buffer_size=65536):
    data, sr = sf.read(str(filepath), always_2d=True, dtype="float32")
    if frame_offset > 0:
        data = data[frame_offset:]
    if num_frames > 0:
        data = data[:num_frames]
    tensor = torch.from_numpy(data.T if channels_first else data)
    return tensor, sr


def _save(filepath, src, sample_rate, channels_first=True, compression=None,
          encoding=None, bits_per_sample=None, buffer_size=65536,
          backend=None, format=None):
    data = src.detach().cpu().numpy()
    if channels_first:
        data = data.T
    sf.write(str(filepath), data, int(sample_rate))


torchaudio.load = _load
torchaudio.save = _save

from demucs.__main__ import main  # noqa: E402
sys.exit(main())
