#!/usr/bin/env python3
"""Offline wake-word listener that plays a local acknowledgement on wake.

Portable design (Linux + ALSA):

  - Audio devices, model directory, response directory, detection
    parameters are all configurable via CLI / environment variables /
    an optional JSON config file.
  - Audio backend abstraction: only an ALSA backend (arecord/aplay) is
    implemented; the class boundary mirrors where a PortAudio backend
    would plug in for macOS/Windows later.
  - Default input/output devices are auto-detected with a fallback
    chain (``reachymini_*`` shared entries on Reachy Mini -> ``default``
    -> ``plughw:0,0`` on generic hardware).

Detection semantics (tuned on a real Reachy Mini):

  - openWakeWord scores each 80 ms frame with 0..1.
  - A wake is only accepted after ``hits`` consecutive frames above
    ``threshold`` (default 0.20 / 3), which filters single-frame noise
    spikes while real utterances (>=0.5 s) pass losslessly.
  - After the acknowledgement the capture stream is reopened so the
    robot's own playback echo cannot trigger a second wake.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import random
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import wave

import numpy as np
from openwakeword.model import Model

RATE = 16000          # frames/s used internally
FRAME = 1280          # 80 ms per inference frame (samples)
BLOCK = FRAME * 4     # 80 ms stereo S16_LE bytes
ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("hey-jarvis")

# Model files that are feature extractors, not wake-word classifiers.
FEATURE_MODELS = {"melspectrogram.onnx", "embedding_model.onnx"}

DEFAULTS = {
    "input_device": "auto",
    "output_device": "auto",
    "model_dir": "models",
    "responses_dir": "assets",
    "channel": 0,
    "threshold": 0.20,
    "hits": 3,
    "cooldown": 3.0,
}

ENV_MAP = {
    "input_device": "WAKE_INPUT_DEVICE",
    "output_device": "WAKE_OUTPUT_DEVICE",
    "model_dir": "WAKE_MODEL_DIR",
    "responses_dir": "WAKE_RESPONSES_DIR",
    "channel": "WAKE_CHANNEL",
    "threshold": "WAKE_THRESHOLD",
    "hits": "WAKE_HITS",
    "cooldown": "WAKE_COOLDOWN",
}


def _to_type(name: str, value: str):
    if name == "channel":
        return "mean" if value == "mean" else int(value)
    if name == "hits":
        return int(value)
    if name in ("threshold", "cooldown"):
        return float(value)
    return value


def resolve_config(args: argparse.Namespace) -> dict:
    """Merge built-in defaults < JSON config file < environment < CLI."""
    cfg = dict(DEFAULTS)
    if args.config:
        cfg.update(json.loads(args.config.read_text(encoding="utf-8")))
    for key, env in ENV_MAP.items():
        if os.getenv(env) is not None:
            cfg[key] = _to_type(key, os.getenv(env))
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = value
    return cfg


class AlsaBackend:
    """ALSA capture/playback via external arecord/aplay processes.

    This is the only device-touching layer; a future PortAudio backend
    would implement the same interface.
    """

    INPUT_FALLBACKS = ["reachymini_audio_src", "default", "plughw:0,0"]
    OUTPUT_FALLBACKS = ["reachymini_audio_sink", "default", "plughw:0,0"]

    def __init__(self, cfg: dict, rate: int = RATE):
        self.rate = rate
        self.cfg = cfg
        self.input_device = self._pick("input", cfg["input_device"])
        self.output_device = self._pick("output", cfg["output_device"])
        LOG.info("AUDIO input=%s output=%s", self.input_device, self.output_device)

    def _pick(self, kind: str, preferred: str) -> str:
        if preferred != "auto":
            return preferred
        listing = subprocess.run(
            ["arecord" if kind == "input" else "aplay", "-L"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        names = {line.strip() for line in listing.splitlines()}
        for cand in (self.INPUT_FALLBACKS if kind == "input"
                     else self.OUTPUT_FALLBACKS):
            if cand in names:
                return cand
        return "default"

    def open_capture(self) -> subprocess.Popen:
        """Start raw stereo S16_LE capture at self.rate."""
        return subprocess.Popen(
            ["arecord", "-q", "-D", self.input_device, "-t", "raw",
             "-f", "S16_LE", "-r", str(self.rate), "-c", "2",
             "--buffer-size", "4096", "--period-size", "1024"],
            stdout=subprocess.PIPE,
        )

    def play_file(self, path: Path) -> None:
        subprocess.run(
            ["aplay", "-q", "-D", self.output_device, str(path)],
            check=True, timeout=10,
        )


class Capture:
    """Read exact 80 ms stereo frames with a timeout on a stalled pipe."""

    def __init__(self, backend: AlsaBackend):
        self.proc = backend.open_capture()
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        os.set_blocking(self.proc.stdout.fileno(), False)

    def read(self) -> np.ndarray:
        data = bytearray()
        deadline = time.monotonic() + 5
        while len(data) < BLOCK:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError("No complete microphone frame within 5 seconds")
            chunk = os.read(self.proc.stdout.fileno(), BLOCK - len(data))
            if not chunk:
                raise RuntimeError(
                    f"Microphone pipe closed (exit={self.proc.poll()})"
                )
            data.extend(chunk)
        return np.frombuffer(data, dtype="<i2").reshape(-1, 2)

    def close(self) -> None:
        self.selector.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()

    def __enter__(self) -> "Capture":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def mono(stereo: np.ndarray, channel):
    """Downmix to one channel: 0/1 picks a channel, 'mean' averages both."""
    if channel == "mean":
        return stereo.astype(np.float32).mean(axis=1).astype(np.int16)
    return np.ascontiguousarray(stereo[:, int(channel)])


def load_model(model_dir: Path) -> Model:
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise SystemExit(
            f"Model directory not found: {model_dir}\n"
            "Run scripts/download_models.sh first (or point WAKE_MODEL_DIR / "
            "--model-dir at existing openWakeWord ONNX files)."
        )
    wake = sorted(p for p in model_dir.glob("*.onnx")
                  if p.name not in FEATURE_MODELS)
    if not wake:
        raise SystemExit(
            f"No wake-word classifier model (*.onnx, excluding "
            f"{sorted(FEATURE_MODELS)}) in {model_dir}"
        )
    melspec = model_dir / "melspectrogram.onnx"
    embedding = model_dir / "embedding_model.onnx"
    LOG.info("MODEL wake=%s melspec=%s embedding=%s",
             [p.name for p in wake],
             melspec.name if melspec.exists() else "builtin",
             embedding.name if embedding.exists() else "builtin")
    return Model(
        wakeword_models=[str(p) for p in wake],
        inference_framework="onnx",
        melspec_model_path=str(melspec) if melspec.exists() else None,
        embedding_model_path=str(embedding) if embedding.exists() else None,
        ncpu=1,
    )


def responses(responses_dir: Path) -> list[Path]:
    files = sorted(responses_dir.glob("*.wav"))
    if not files:
        raise SystemExit(f"No response .wav files in {responses_dir}")
    return files


def validate_wav(path: Path) -> None:
    with wave.open(str(path)) as wav:
        if (wav.getnframes() == 0 or wav.getframerate() != RATE
                or wav.getnchannels() != 2 or wav.getsampwidth() != 2):
            raise ValueError(
                f"Response must be nonempty 16 kHz stereo 16-bit WAV: {path}"
            )


def respond(backend: AlsaBackend, responses_dir: Path) -> Path:
    pool = responses(responses_dir)
    path = random.choice(pool)
    validate_wav(path)
    LOG.info("RESPONSE_START text=%s file=%s", path.stem, path.name)
    backend.play_file(path)
    LOG.info("RESPONSE_DONE")
    return path


def score_frame(model: Model, samples: np.ndarray) -> float:
    return max((float(v) for v in model.predict(samples).values()), default=0.0)


def test_wav(model: Model, path: Path, threshold: float,
             expect_no_wake: bool) -> int:
    with wave.open(str(path)) as wav:
        if wav.getframerate() != RATE or wav.getsampwidth() != 2:
            raise ValueError("Test WAV must be 16 kHz signed 16-bit PCM")
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        if wav.getnchannels() > 1:
            audio = audio.reshape(-1, wav.getnchannels()).mean(axis=1).astype(np.int16)
    scores, elapsed = [], []
    for i in range(0, len(audio), FRAME):
        frame = audio[i:i + FRAME]
        if len(frame) < FRAME:
            frame = np.pad(frame, (0, FRAME - len(frame)))
        start = time.monotonic()
        scores.append(score_frame(model, frame))
        elapsed.append((time.monotonic() - start) * 1000)
    peak = max(scores, default=0)
    hit = peak >= threshold
    LOG.info("TEST peak=%.4f hit=%s mean_inference_ms=%.2f max_inference_ms=%.2f",
             peak, hit, np.mean(elapsed), max(elapsed, default=0))
    return 0 if hit != expect_no_wake else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline wake-word listener (openWakeWord) with local "
                    "acknowledgement playback."
    )
    parser.add_argument("--config", type=Path, metavar="FILE",
                        help="JSON config file (see config.example.json)")
    parser.add_argument("--input-device", dest="input_device",
                        help="ALSA capture device or 'auto'")
    parser.add_argument("--output-device", dest="output_device",
                        help="ALSA playback device or 'auto'")
    parser.add_argument("--model-dir", dest="model_dir",
                        help="directory with openWakeWord *.onnx files")
    parser.add_argument("--responses-dir", dest="responses_dir",
                        help="directory with acknowledgement *.wav files")
    parser.add_argument("--threshold", type=float,
                        help="wake score threshold (0, 1]")
    parser.add_argument("--hits", type=int,
                        help="consecutive frames above threshold required")
    parser.add_argument("--channel", type=int,
                        help="capture channel to score (0/1); mean not here")
    parser.add_argument("--cooldown", type=float,
                        help="seconds to ignore after a wake")
    parser.add_argument("--test-wav", type=Path)
    parser.add_argument("--expect-no-wake", action="store_true")
    parser.add_argument("--probe-seconds", type=float)
    parser.add_argument("--play-response", type=Path, nargs="?", const="",
                        help="play one response file (or a random one) and exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = resolve_config(args)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not 0 < cfg["threshold"] <= 1 or cfg["cooldown"] < 0 or cfg["hits"] < 1:
        raise SystemExit("threshold must be (0, 1], cooldown nonnegative, hits >= 1")
    backend = AlsaBackend(cfg)
    responses_dir = Path(cfg["responses_dir"])

    if args.play_response is not None:
        path = args.play_response if str(args.play_response) else None
        if path:
            validate_wav(path)
            backend.play_file(path)
        else:
            respond(backend, responses_dir)
        return 0

    if args.probe_seconds:
        blocks = []
        with Capture(backend) as capture:
            end = time.monotonic() + args.probe_seconds
            while time.monotonic() < end:
                blocks.append(capture.read())
        data = np.concatenate(blocks).astype(np.float32) / 32768
        LOG.info("PROBE channel_rms=%s channel_peak=%s",
                 np.sqrt(np.mean(data ** 2, axis=0)), np.max(np.abs(data), axis=0))
        return 0

    model = load_model(Path(cfg["model_dir"]))
    if args.test_wav:
        return test_wav(model, args.test_wav, cfg["threshold"], args.expect_no_wake)

    def shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    LOG.info("READY model_dir=%s responses_dir=%s channel=%s "
             "threshold=%.2f hits=%d cooldown=%.1fs offline=true",
             cfg["model_dir"], cfg["responses_dir"], cfg["channel"],
             cfg["threshold"], cfg["hits"], cfg["cooldown"])
    last_wake = -float("inf")
    heartbeat = time.monotonic()
    peak = 0.0
    hits = 0
    try:
        while True:
            # Reopen after an acknowledgement so queued playback echo cannot
            # become a delayed wake. Systemd restarts on capture/model failure.
            with Capture(backend) as capture:
                warmup_end = time.monotonic() + 0.5
                while True:
                    samples = mono(capture.read(), cfg["channel"])
                    if time.monotonic() < warmup_end:
                        continue
                    score = score_frame(model, samples)
                    peak = max(peak, score)
                    now = time.monotonic()
                    if now - heartbeat >= 30:
                        LOG.info("LISTENING peak_score=%.4f rms=%.5f", peak,
                                 np.sqrt(np.mean((samples.astype(np.float32) / 32768) ** 2)))
                        heartbeat, peak = now, 0.0
                    if score >= cfg["threshold"]:
                        hits += 1
                    else:
                        hits = 0
                    if hits >= cfg["hits"] and now - last_wake >= cfg["cooldown"]:
                        last_wake = now
                        LOG.info("WAKE_DETECTED phrase=%s score=%.4f hits=%d",
                                 Path(cfg["model_dir"]).stem, score, hits)
                        break
            respond(backend, responses_dir)
            model.reset()
            LOG.info("LISTENING resumed")
    except KeyboardInterrupt:
        LOG.info("STOPPED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
