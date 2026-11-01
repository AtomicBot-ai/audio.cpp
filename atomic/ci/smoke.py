#!/usr/bin/env python3
"""Smoke-test one unpacked audio.cpp archive the way Atomic Chat drives it.

Stdlib only, so it runs on the bare arm64 runners (ubuntu-24.04-arm, windows-11-arm), in a bare
python:3.12-slim container and on a user's machine. Steps, each fatal on failure:

  1. `audiocpp_server --version` exits 0, says `audio.cpp <version>` and lists its backends
     (with --expect-backend, that one too).
  2. `audiocpp_server --list-devices` shows a CPU device: on the GGML_BACKEND_DL builds that is
     the proof the CPU backend module loads from the archive.
  3. eSpeak-ng files: without --static-espeak (the default, as Release (Atomic) builds) the archive
     must not carry `espeak-ng-data.bin` or `licenses/espeak-ng/`; with it, both must sit next to
     the server.
  4. MOSS-TTS-Nano 100M, which needs neither eSpeak-ng nor a reference voice:
     `audiocpp_server --config <generated> --no-ui --backend <backend>` loads it and answers
     `GET /health` with status ok and `GET /v1/models` with the model loaded, and
     `POST /v1/audio/speech` with seeds 1..--repeat returns 200 and a RIFF/WAVE body whose samples
     are not all zero, each --min-seconds..--max-seconds long: speech that is cut off or runs on is
     still a valid WAV, and only the length shows it. Any other status fails, with the server's error
     message.
  5. With --static-espeak only: the same with Kokoro 82M, which phonemizes with eSpeak-ng (a build
     without it answers Kokoro's speech request with a 500 `Could not load eSpeak-ng`).

The models are downloaded once into --models-dir from URLs pinned to one revision of
audio-cpp/audio.cpp-gguf, and a file is used only if its size and SHA-256 match the pin.

This mirrors atomic-chat-core's src/audio runtime: one model per server, config written by the
caller, lazy_load off, the archive directory as the working directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

ESPEAK_DATA = "espeak-ng-data.bin"
ESPEAK_LICENSES = Path("licenses") / "espeak-ng"
# ggml's SVE paths make wrong speech (atomic/README.md); these must not reach an archive.
SVE_VARIANTS = ("libggml-cpu-armv8.2_3.so", "libggml-cpu-armv8.6_*.so", "libggml-cpu-armv9.*.so")

GGUF_REPO = "https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/874fcb41ececda20bcbc0782d6b4ea7f7d069ea8"


@dataclass(frozen=True)
class SmokeModel:
    """A pinned GGUF and the server config entry that loads it (`path` is added at run time)."""

    file: str
    url: str
    sha256: str
    size: int
    entry: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.entry["id"]


# Apache-2.0; speaks plain text without a reference voice; 48 kHz stereo.
MOSS = SmokeModel(
    file="moss-tts-nano-100m-q8_0.gguf",
    url=f"{GGUF_REPO}/MOSS-TTS-Nano-100M-GGUF/moss-tts-nano-100m-q8_0.gguf",
    sha256="823b0de64f01f1ee9a168356c1199be500d71ec2f769f0183b416c9e6dbc0abc",
    size=193343328,
    entry={"id": "moss", "family": "moss_tts_nano", "task": "tts", "mode": "offline"},
)
# Needs eSpeak-ng: only part of the smoke when the archive links it statically.
KOKORO = SmokeModel(
    file="kokoro-82m-q8_0.gguf",
    url=f"{GGUF_REPO}/Kokoro-82M-GGUF/kokoro-82m-q8_0.gguf",
    sha256="9a4ca6176460cdc8c7845b804c0bbe85a4791ee3ffff568a3fdadbb708c63f47",
    size=189550976,
    entry={
        "id": "kokoro",
        "family": "kokoro_tts",
        "task": "tts",
        "mode": "offline",
        "default_voice_preset": {"voice_id": "af_heart"},
    },
)


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def fail(msg: str) -> NoReturn:
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"[smoke] FAIL: {msg}", flush=True)
    sys.exit(1)


def exe(directory: Path, name: str) -> Path:
    path = directory / (name + ".exe" if os.name == "nt" else name)
    if not path.is_file():
        fail(f"{path} is missing from the archive")
    return path


def child_env(directory: Path, extra_lib_dirs: list[str], scratch: Path) -> dict[str, str]:
    """The environment the core gives audiocpp_server: the archive on the loader path, and the
    package (TMPDIR) and eSpeak data (XDG_CACHE_HOME) caches somewhere writable. A container run
    as an arbitrary uid has HOME=/, where the eSpeak cache could not be created."""
    env = dict(os.environ)
    dirs = [str(directory), *extra_lib_dirs]
    if os.name == "nt":
        env["PATH"] = os.pathsep.join([*dirs, env.get("PATH", "")])
    else:
        env["LD_LIBRARY_PATH"] = os.pathsep.join([*dirs, env.get("LD_LIBRARY_PATH", "")]).rstrip(os.pathsep)
        for name in ("TMPDIR", "XDG_CACHE_HOME"):
            path = scratch / name.lower()
            path.mkdir(parents=True, exist_ok=True)
            env[name] = str(path)
    return env


def run(cmd: list[str], env: dict[str, str], cwd: Path, timeout: int = 300) -> str:
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout)
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out[-4000:], flush=True)
    if proc.returncode != 0:
        fail(f"{' '.join([Path(cmd[0]).name, *cmd[1:]])} exited with {proc.returncode}")
    return out


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matches_pin(path: Path, model: SmokeModel) -> bool:
    return path.is_file() and path.stat().st_size == model.size and file_sha256(path) == model.sha256


def ensure_model(models_dir: Path, model: SmokeModel) -> Path:
    """The pinned GGUF in models_dir, downloaded unless a file with its size and SHA-256 is there."""
    path = models_dir / model.file
    if matches_pin(path, model):
        log(f"model {path} present")
        return path
    models_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    for attempt in range(1, 4):
        log(f"downloading {model.url} (attempt {attempt})")
        try:
            with urllib.request.urlopen(model.url, timeout=60) as response, open(partial, "wb") as out:
                shutil.copyfileobj(response, out, length=8 << 20)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            log(f"download failed: {error}")
            time.sleep(5 * attempt)
            continue
        if matches_pin(partial, model):
            partial.replace(path)
            return path
        log(f"{model.file}: size or SHA-256 does not match the pin")
    partial.unlink(missing_ok=True)
    fail(f"could not download {model.file} matching its pin from {model.url}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(method: str, url: str, body: dict | None = None, timeout: float = 10) -> tuple[int, str, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def error_message(body: bytes) -> str:
    text = body.decode("utf-8", "replace")
    try:
        return str(json.loads(text).get("error", {}).get("message") or text)
    except (ValueError, AttributeError):
        return text


def check_wav(wav: bytes) -> tuple[str, float]:
    """Parse a RIFF/WAVE body (PCM or IEEE float) and fail unless it carries non-silent samples."""
    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        fail(f"speech body is not RIFF/WAVE (starts {wav[:12]!r})")
    fmt = b""
    data = None
    pos = 12
    while pos + 8 <= len(wav):
        chunk_id, size = wav[pos : pos + 4], struct.unpack_from("<I", wav, pos + 4)[0]
        body = wav[pos + 8 : pos + 8 + size]
        if chunk_id == b"fmt ":
            fmt = body
        elif chunk_id == b"data":
            data = body
            break
        pos += 8 + size + (size & 1)
    if len(fmt) < 16 or data is None:
        fail("WAV has no fmt or data chunk")
    audio_format, channels, rate, _, block_align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if audio_format == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE: the SubFormat GUID decides
        audio_format = struct.unpack_from("<H", fmt, 24)[0]
    if channels < 1 or rate < 8000 or block_align < 1:
        fail(f"implausible WAV format: {channels} channel(s), {rate} Hz, block {block_align}")
    frames = len(data) // block_align
    if frames < rate // 10:
        fail(f"WAV holds only {frames} frames at {rate} Hz")
    if audio_format == 1 and bits == 16:
        samples = struct.unpack_from(f"<{len(data) // 2}h", data)
        peak = max(abs(s) for s in samples) / 32768.0
    elif audio_format == 3 and bits == 32:
        samples = struct.unpack_from(f"<{len(data) // 4}f", data)
        peak = max(abs(s) for s in samples)
    else:
        fail(f"unexpected WAV sample format {audio_format}/{bits} bit")
    if not peak > 1e-3:
        fail(f"WAV is silent (peak {peak:.6f})")
    seconds = frames / rate
    return f"{channels} ch, {rate} Hz, {bits}-bit fmt {audio_format}, {seconds:.2f} s, peak {peak:.3f}", seconds


def speak(server: Path, directory: Path, env: dict[str, str], scratch: Path, args: argparse.Namespace,
          model: SmokeModel, model_path: Path) -> None:
    """Start audiocpp_server with this one model, wait until it is healthy with the model loaded,
    synthesize args.text with seeds 1..args.repeat and check every WAV, keeping them as
    <out-dir>/smoke-<model id>-seed<n>.wav. A clip outside args.min_seconds..args.max_seconds fails the
    smoke, after every seed has run: speech that is cut off or runs on is still a valid, non-silent WAV
    (0.16 s and 10 s of one sentence on ggml's SVE CPU variants), and only the length shows it."""
    port = free_port()
    config = {
        "host": "127.0.0.1",
        "port": port,
        "backend": args.backend,
        "lazy_load": False,
        "max_loaded_models": 1,
        # The server runs with the archive as its cwd, like the core starts it: the path is absolute.
        "models": [{**model.entry, "path": str(model_path)}],
    }
    config_path = scratch / f"server-{model.id}.json"
    config_path.write_text(json.dumps(config, indent=2))
    cmd = [str(server), "--config", str(config_path), "--no-ui", "--host", "127.0.0.1", "--port", str(port),
           "--backend", args.backend]
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, env=env, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
    )

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"  | {line.rstrip()}", flush=True)

    threading.Thread(target=pump, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + args.load_timeout
        while True:
            if proc.poll() is not None:
                fail(f"audiocpp_server exited with {proc.returncode} before it listened ({model.id})")
            try:
                status, _, body = http("GET", f"{base}/health", timeout=5)
                if status == 200 and json.loads(body).get("status") == "ok":
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError):
                pass
            if time.time() > deadline:
                fail(f"audiocpp_server did not become healthy in time ({model.id})")
            time.sleep(1)
        log(f"PASS /health ({model.id}): {body.decode('utf-8', 'replace')[:300]}")

        status, _, body = http("GET", f"{base}/v1/models")
        if status != 200:
            fail(f"/v1/models answered {status} ({model.id})")
        models = {m.get("id"): m for m in json.loads(body).get("data", [])}
        if not (models.get(model.id) or {}).get("loaded"):
            fail(f"/v1/models does not show {model.id} loaded: {body.decode('utf-8', 'replace')[:500]}")
        log(f"PASS /v1/models: {model.id} loaded")

        wrong: list[str] = []
        for seed in range(1, args.repeat + 1):
            started = time.time()
            status, content_type, wav = http(
                "POST",
                f"{base}/v1/audio/speech",
                {"model": model.id, "input": args.text, "seed": seed},
                timeout=args.speech_timeout,
            )
            elapsed = time.time() - started
            if status != 200:
                fail(f"/v1/audio/speech ({model.id}, seed {seed}) answered {status}: {error_message(wav)[:500]}")
            if "audio/wav" not in content_type and "audio/x-wav" not in content_type:
                log(f"note: Content-Type is {content_type!r}")
            summary, seconds = check_wav(wav)
            out = args.out_dir / f"smoke-{model.id}-seed{seed}.wav"
            out.write_bytes(wav)
            log(f"/v1/audio/speech ({model.id}, seed {seed}) in {elapsed:.1f}s: {summary} -> {out} ({len(wav)} bytes)")
            if not args.min_seconds <= seconds <= args.max_seconds:
                wrong.append(f"seed {seed}: {seconds:.2f} s")
        if wrong:
            fail(f"{model.id} speech outside {args.min_seconds}..{args.max_seconds} s: {', '.join(wrong)}")
        log(f"PASS /v1/audio/speech ({model.id}): {args.repeat} clips, all {args.min_seconds}..{args.max_seconds} s")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", required=True, type=Path, help="unpacked archive")
    parser.add_argument("--models-dir", required=True, type=Path,
                        help="model cache: the pinned GGUFs are downloaded here unless already present")
    parser.add_argument("--lib-dir", action="append", default=[], help="extra loader dir")
    parser.add_argument("--backend", default="cpu", help="--backend for audiocpp_server (default cpu)")
    parser.add_argument("--expect-backend", action="append", default=[],
                        help="a backend --version must list, e.g. metal, vulkan or cuda")
    parser.add_argument("--static-espeak", action="store_true",
                        help="the archive links eSpeak-ng (static_espeak): check its files and speak with Kokoro too")
    parser.add_argument("--text", default="Hello from Atomic Chat. This is a smoke test.")
    parser.add_argument("--repeat", type=int, default=12, help="clips to make, with seeds 1..N (default 12)")
    parser.add_argument("--min-seconds", type=float, default=1.0,
                        help="shortest believable clip of --text; anything shorter fails (default 1)")
    parser.add_argument("--max-seconds", type=float, default=8.0,
                        help="longest believable clip of --text; anything longer fails (default 8)")
    parser.add_argument("--out-dir", type=Path, default=Path(tempfile.gettempdir()) / "audiocpp-smoke",
                        help="where to keep the generated WAVs (smoke-<model>.wav)")
    parser.add_argument("--load-timeout", type=int, default=600)
    parser.add_argument("--speech-timeout", type=int, default=600)
    args = parser.parse_args()

    directory = args.dir.resolve()
    models_dir = args.models_dir.expanduser().resolve()
    args.out_dir = args.out_dir.expanduser().resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="audiocpp-smoke-"))
    env = child_env(directory, args.lib_dir, scratch)
    server = exe(directory, "audiocpp_server")
    exe(directory, "audiocpp_cli")

    try:
        version = run([str(server), "--version"], env, directory)
        lines = [line.strip() for line in version.splitlines() if line.strip()]
        if not any(line.startswith("audio.cpp ") for line in lines):
            fail("--version does not say `audio.cpp <version>`")
        backends_line = next((line for line in lines if line.startswith("backends:")), None)
        if backends_line is None:
            fail("--version has no `backends:` line")
        backends = [b.strip() for b in backends_line.split(":", 1)[1].split(",") if b.strip()]
        for wanted in args.expect_backend:
            if wanted not in backends:
                fail(f"--version lists backends {backends}, not {wanted}")
        log(f"PASS --version (backends {backends})")

        devices = run([str(server), "--list-devices"], env, directory)
        if "[CPU]" not in devices:
            fail("--list-devices shows no CPU device")
        log("PASS --list-devices")

        if args.static_espeak:
            if not (directory / ESPEAK_DATA).is_file():
                fail(f"{ESPEAK_DATA} is missing next to audiocpp_server")
            if not (directory / ESPEAK_LICENSES / "COPYING").is_file():
                fail(f"{ESPEAK_LICENSES.as_posix()}/COPYING is missing")
            log(f"PASS {ESPEAK_DATA} and {ESPEAK_LICENSES.as_posix()}/ present")
        else:
            present = [p.as_posix() for p in (Path(ESPEAK_DATA), ESPEAK_LICENSES) if (directory / p).exists()]
            if present:
                fail(f"eSpeak-ng files in an archive built without it: {', '.join(present)} "
                     "(pass --static-espeak for a static_espeak build)")
            log("PASS no eSpeak-ng files (built without eSpeak-ng)")

        sve = sorted(p.name for pattern in SVE_VARIANTS for p in directory.glob(pattern))
        if sve:
            fail(f"ggml CPU variants with SVE in the archive: {', '.join(sve)} (package-linux.sh leaves them out)")
        smoke_models = [MOSS, KOKORO] if args.static_espeak else [MOSS]
        for model in smoke_models:
            speak(server, directory, env, scratch, args, model, ensure_model(models_dir, model))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
