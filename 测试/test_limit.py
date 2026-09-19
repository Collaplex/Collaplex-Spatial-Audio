"""高峰值因子素材(类音乐)测限幅: 输出峰值 - 输入峰值 应等于总音量(+6dB, 未被压)。"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import wave

import numpy as np

RATE = 96000
WAV = "/tmp/music_like.wav"
API = "http://127.0.0.1:8789"


def make() -> None:
    secs = 5.0
    n = int(RATE * secs)
    t = np.arange(n) / RATE
    x = 0.15 * np.sin(2 * np.pi * 100 * t)
    idx = np.arange(2000)
    for i in range(0, n - 2000, int(RATE * 0.5)):
        x[i:i + 2000] += 0.6 * np.sin(2 * np.pi * 3000 * idx / RATE)
    x = np.clip(x, -0.99, 0.99)
    data = (x * 32767).astype("<i2")
    with wave.open(WAV, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(np.repeat(data, 2).tobytes())


def main() -> None:
    make()
    p = subprocess.Popen(["pw-play", "--target", "collaplex_vsink", WAV],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    for _ in range(5):
        d = json.loads(urllib.request.urlopen(API + "/api/state", timeout=10).read())
        lv = d["levels"]
        print("  入峰值 %6.2f | 出峰值 %6.2f | 增益 %+5.2f dB (总音量应 +6.00)" % (
            lv["in_peak"], lv["out_peak"], lv["out_peak"] - lv["in_peak"]))
        time.sleep(0.35)
    p.terminate()
    p.wait(timeout=5)


if __name__ == "__main__":
    main()
