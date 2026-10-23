"""中低频沙沙定位 ③: 逐级旁路, 每级单独喂同一素材, 比频谱。"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time

import numpy as np
from scipy.io import wavfile

RATE = 96000
SRC = "/tmp/thd_tone.wav"
SINK = ""

PROBES = (100.0, 200.0, 300.0, 400.0, 600.0, 800.0)


def find_digital_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def capture(target: str, path: str) -> None:
    player = subprocess.Popen(["pw-play", "--target", target, SRC],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    rec = subprocess.Popen(["timeout", "3", "pw-record", "--target", SINK,
                            "-P", "{ stream.capture.sink = true }",
                            "--rate", str(RATE), "--channels", "2", "--format", "f32", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec.wait(timeout=12)
    player.terminate()
    player.wait(timeout=5)


def spec_of(path: str) -> tuple[np.ndarray, np.ndarray]:
    _rate, data = wavfile.read(path)
    x = data.astype(np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    x = x[8000:8000 + 65536]
    x = x - float(np.mean(x))
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
    return np.fft.rfftfreq(x.size, 1.0 / RATE), spec


def rel_at(freqs: np.ndarray, spec: np.ndarray, f: float, half: float = 15.0) -> float:
    mask = (freqs >= f - half) & (freqs < f + half)
    ref = float(np.max(spec)) + 1e-30
    if not np.any(mask):
        return -999.0
    return 10.0 * math.log10((float(np.max(spec[mask])) + 1e-30) / ref)


def main() -> None:
    global SINK
    SINK = find_digital_sink()
    stages = (
        ("旁路(直插设备)", SINK),
        ("只走 HRTF 级", "collaplex_hrtf_in"),
        ("走 HRTF+EQ 级", "collaplex_eq_in"),
        ("全链(含归一化)", "collaplex_vsink"),
    )
    print("素材 200 Hz 纯音; 每格 = 该频点相对基频的 dB(越负越干净)")
    print("  %-18s %s" % ("测点", "  ".join("%7.0fHz" % f for f in PROBES)))
    for idx, (label, target) in enumerate(stages):
        path = "/tmp/stage_%d.wav" % idx
        capture(target, path)
        try:
            freqs, spec = spec_of(path)
        except (OSError, ValueError, IndexError):
            print("  %-18s 读取失败" % label)
            continue
        print("  %-18s %s" % (label, "  ".join("%+8.1f" % rel_at(freqs, spec, f) for f in PROBES)))


if __name__ == "__main__":
    main()
