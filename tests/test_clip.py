"""复现削波: 喂一个 -6 dBFS 正弦, 总音量 +12 dB, 看输出是被线性放大还是被削平。"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np

DSP_DIR = "/home/lou/桌面/工作区/实验/collaplex音效/dsp"
sys.path.insert(0, DSP_DIR)

import common  # noqa: E402

RATE = 96000
PY = "/home/lou/桌面/工作区/实验/collaplex音效/.venv/bin/python"
EQ = os.path.join(DSP_DIR, "eq.py")


def run(x: np.ndarray, gains: list[float], vol_db: float) -> np.ndarray:
    store = common.open_store()
    common.write_params(store, gains, vol_db)
    env = dict(os.environ, CX_RATE=str(RATE))
    proc = subprocess.run([PY, EQ], input=x.astype("<f4").tobytes(),
                          capture_output=True, timeout=60, env=env)
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float64)


flat = [0.0] * 32
t = np.arange(int(RATE * 0.4)) / RATE
x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float64)   # -6 dBFS 正弦

for vol in (0.0, 6.0, 12.0):
    y = run(x, flat, vol)
    seg = y[5000:]
    peak = float(np.max(np.abs(seg)))
    at_rail = float(np.mean(np.abs(seg) >= 0.9999)) * 100.0
    rms = float(np.sqrt(np.mean(seg * seg)))
    print("总音量 %+5.1f dB -> 峰值 %.4f (%.2f dBFS) | 贴顶样本 %.1f%% | RMS %.4f" % (
        vol, peak, 20 * np.log10(max(peak, 1e-12)), at_rail, rms))

print()
print("理论: -6 dBFS 正弦经 +6 dB 应到 %.3f, 经 +12 dB 应到 %.3f" % (
    0.5 * 10 ** (6 / 20), 0.5 * 10 ** (12 / 20)))
