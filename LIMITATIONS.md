# Limitations and integration requirements

Audio extraction, looping, mixing, duration and preservation of the encoded video stream were checked. Demucs inference and separation quality still need a configured model run.

`requirements-local.txt` starts the interface and media utilities. For vocal separation, install the dependencies in `requirements.txt` with Torch and torchaudio builds appropriate for your hardware. Demucs downloads model weights on first use. FFmpeg must be available.

Keep web services bound to `127.0.0.1`. Hosting this application for multiple users requires authentication and separate storage and resource limits.
