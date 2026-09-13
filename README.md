# Music Remover

Music Remover separates vocals from background audio and can remove or replace the soundtrack of a video. Demucs handles source separation; FFmpeg extracts, mixes and remuxes the audio while retaining the encoded video stream.

## Features

- Separate vocals and background audio with Demucs.
- Remove music or replace it with a selected local track.
- Mix and remux audio with FFmpeg.
- Process folders with progress updates in the interface.

## How it works

The application coordinates an isolated Demucs worker and FFmpeg utilities for extraction, mixing and remuxing. Background jobs report progress to the UI.

**Stack:** Python · FastAPI · Demucs · PyTorch · FFmpeg

## Getting started

Use Python 3.12 and a separate virtual environment. Run the following commands from this repository's root in Windows PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-local.txt
```

`requirements-local.txt` starts the interface and media utilities. For vocal separation, install the dependencies in `requirements.txt` with Torch and torchaudio builds appropriate for your hardware. Demucs downloads model weights on first use. FFmpeg must be available.

### Start the application

Open http://127.0.0.1:7860. Install compatible Torch and torchaudio versions for your hardware, then the remaining requirements. The first Demucs run downloads model weights.

```powershell
python main.py
```

## Example workflow

Choose a recording with voice and background music, select removal or replacement, and compare the resulting soundtrack.

## Testing and limitations

Audio extraction, looping, mixing, duration and preservation of the encoded video stream were checked. Demucs inference and separation quality still need a configured model run.

See [Verification](VERIFICATION.md) for the recorded checks and [Limitations](LIMITATIONS.md) for integration requirements.

## Configuration and security

Keep web services bound to `127.0.0.1`. Hosting this application for multiple users requires authentication and separate storage and resource limits. Configure your own provider credentials when a feature requires them; credentials and personal data are not included. See [Security](SECURITY.md) for local configuration and reporting guidance.
