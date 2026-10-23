"""中低频沙沙定位: 播 200Hz 纯音, 改总音量, 录输出看谐波/非谐波成分。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import wave

import numpy as np
from scipy.io import wavfile

PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
RATE = 96000
FREQ = 200.0
SRC = "/tmp/thd_tone.wav"
SINK = ""
API = "http://127.0.0.1:8789"


def find_digital_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def write_tone() -> None:
    secs = 5.0
    n = int(RATE * secs)
    t = np.arange(n) / RATE
    x = (0.1 * np.sin(2 * np.pi * FREQ * t))          # 峰值 -20 dBFS
    ramp = np.minimum(1.0, np.minimum(np.arange(n) / 2000.0, (n - np.arange(n)) / 2000.0))
    x = x * ramp
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(SRC, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(np.repeat(data, 2).tobytes())


def set_volume(db: float) -> None:
    req = urllib.request.Request(API + "/api/volume", data=json.dumps({"db": db}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def capture(path: str) -> None:
    player = subprocess.Popen(["pw-play", "--target", "collaplex_vsink", SRC],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    rec = subprocess.Popen(["timeout", "3", "pw-record", "--target", SINK,
                            "-P", "{ stream.capture.sink = true }",
                            "--rate", str(RATE), "--channels", "2", "--format", "f32", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec.wait(timeout=12)
    player.terminate()
    player.wait(timeout=5)


def analyse(path: str, label: str) -> None:
    try:
        _rate, data = wavfile.read(path)
    except (OSError, ValueError):
        print("  %-12s 读不到录音" % label)
        return
    x = data.astype(np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    if x.size < 8192:
        print("  %-12s 录音太短" % label)
        return
    x = x[4096:4096 + 65536]
    x = x - float(np.mean(x))
    win = np.hanning(x.size)
    spec = np.abs(np.fft.rfft(x * win)) ** 2
    freqs = np.fft.rfftfreq(x.size, 1.0 / RATE)

    def band_energy(f0: float, f1: float) -> float:
        mask = (freqs >= f0) & (freqs < f1)
        return float(np.sum(spec[mask]))

    def tone_energy(f: float, half: float = 12.0) -> float:
        return band_energy(f - half, f + half)

    fund = tone_energy(FREQ)
    harmonics = sum(tone_energy(FREQ * k) for k in range(2, 9))
    total = float(np.sum(spec)) + 1e-30
    rest = max(total - fund - harmonics, 0.0)
    to_db = lambda v: 10.0 * np.log10(v + 1e-30)   # noqa: E731

    print("  %-12s 基频 %7.1f dB | 谐波(2~8次) %7.1f dB 相对基频 %+6.1f dB | "
          "其余(含噪声) %7.1f dB 相对基频 %+6.1f dB" % (
              label, to_db(fund), to_db(harmonics), to_db(harmonics) - to_db(fund),
              to_db(rest), to_db(rest) - to_db(fund)))


def main() -> None:
    global SINK
    SINK = find_digital_sink()
    if not SINK:
        print("找不到数字输出")
        return
    write_tone()
    print("素材: 200 Hz 纯音(峰值 -20 dBFS), 录音后分析 200k 分量之外的成分")
    print("判据: 谐波/其余成分相对基频越低越干净; 若 +6~+12 dB 时显著抬起, 就是限幅失真")
    print()
    for db in (0.0, 6.0, 12.0):
        set_volume(db)
        time.sleep(0.5)
        path = "/tmp/thd_%d.wav" % int(db)
        capture(path)
        analyse(path, "总音量 %+d dB" % int(db))
    set_volume(6.0)


if __name__ == "__main__":
    main()
