"""端到端: 总音量放大对响度与峰值的影响(播素材实测, 数据取自 EQ DSP 的状态区)。"""
from __future__ import annotations

import json
import math
import subprocess
import time
import urllib.request
import wave

import numpy as np

API = "http://127.0.0.1:8789"
WAV = "/tmp/e2e_white.wav"
RATE = 96000


def make_white() -> None:
    rng = np.random.default_rng(7)
    secs = 4.0
    x = rng.normal(0.0, 1.0, int(RATE * secs))
    x = x / (np.max(np.abs(x)) + 1e-12) * (10 ** (-12 / 20))   # 峰值 -12 dBFS
    data = (x * 32767).astype("<i2")
    with wave.open(WAV, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(np.repeat(data, 2).tobytes())


def post(path: str, payload: dict[str, object]) -> None:
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def state() -> dict[str, object]:
    raw = urllib.request.urlopen(API + "/api/state", timeout=10).read()
    return json.loads(raw)


def play_and_read() -> dict[str, float]:
    proc = subprocess.Popen(["pw-play", "--target", "collaplex_vsink", WAV],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.6)
    levels = state().get("levels")
    time.sleep(0.2)
    proc.terminate()
    proc.wait(timeout=5)
    if not isinstance(levels, dict):
        return {"in_rms": -120.0, "out_rms": -120.0, "out_peak": -120.0}
    return {str(k): float(v) for k, v in levels.items()}


def main() -> None:
    make_white()
    base: list[float] = []
    print("素材: 峰值 -12 dBFS 白噪")
    for db in (0.0, 6.0, 12.0):
        post("/api/volume", {"db": db})
        time.sleep(0.5)
        lv = play_and_read()
        out_rms = lv.get("out_rms", -120.0)
        out_peak = lv.get("out_peak", -120.0)
        base.append(out_rms)
        delta = out_rms - base[0]
        print("总音量 %+5.1f dB -> 出 RMS %6.2f dBFS (Δ%+5.2f) | 出峰值 %6.2f dBFS" % (
            db, out_rms, delta, out_peak))
    post("/api/volume", {"db": 6.0})


if __name__ == "__main__":
    main()
