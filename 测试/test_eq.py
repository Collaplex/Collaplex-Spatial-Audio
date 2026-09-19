"""eq.py 自测: 平直直通 / 单段提升 / 总音量 / 邻段不串扰。"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np

DSP_DIR = "/home/lou/桌面/工作区/实验/collaplex音效/dsp"
sys.path.insert(0, DSP_DIR)

import common  # noqa: E402

RATE = 96000
EQ = os.path.join(DSP_DIR, "eq.py")
store = common.open_store()


def run(signal: np.ndarray, gains: list[float], vol_db: float) -> np.ndarray:
    common.write_params(store, gains, vol_db)
    proc = subprocess.run([str(DSP_DIR + "/../.venv/bin/python"), EQ], input=signal.astype("<f4").tobytes(),
                          capture_output=True, timeout=120,
                          env={**os.environ, "CX_RATE": str(RATE)})
    if proc.returncode != 0:
        print("DSP stderr:", proc.stderr.decode()[:400])
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float64)


def tone(freq: float, secs: float = 0.5, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(RATE * secs)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-12))


flat = [0.0] * common.EQ_BANDS
x = tone(1000.0)
tail = slice(4000, None)

print("== 1kHz 正弦, 比较输出/输入 ==")
print("平直(应 0 dB):          %+.2f dB" % (rms_db(run(x, flat, 0.0)[tail]) - rms_db(x[tail])))

g = flat.copy()
g[17] = 12.0
print("1kHz 段 +12(应 ~+12):   %+.2f dB" % (rms_db(run(x, g, 0.0)[tail]) - rms_db(x[tail])))

print("总音量 +6(应 ~+6):      %+.2f dB" % (rms_db(run(x, flat, 6.0)[tail]) - rms_db(x[tail])))

g = flat.copy()
g[7] = 12.0
print("100Hz 段 +12(应 ~0):    %+.2f dB" % (rms_db(run(x, g, 0.0)[tail]) - rms_db(x[tail])))

low = tone(100.0)
g = flat.copy()
g[7] = 12.0
print("100Hz 段 +12 测 100Hz:  %+.2f dB" % (rms_db(run(low, g, 0.0)[tail]) - rms_db(low[tail])))

print()
print("== 状态(共享内存) ==")
print(common.read_state(store))
