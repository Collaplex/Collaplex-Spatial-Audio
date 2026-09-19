"""32 段 EQ + 总音量 —— pipe 插件载荷(单声道块进, 单声道块出)。

参数实时来自共享内存(dsp/common.py): 32 段增益(dB) + 总音量(dB)。
做法: 每段一个 peaking biquad, 32 段级联成二阶节(sos), 用 scipy.signal.sosfilt
逐块滤波(C 实现, 保留滤波状态, 无额外延迟), 末级乘总音量。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
from scipy.signal import sosfilt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

RATE = int(os.environ.get("CX_RATE", "96000"))
BLOCK_BYTES = 4096
N = BLOCK_BYTES // 4            # 每块样本数(1024)
Q = 4.318                       # 1/3 倍频程


def design_peaking(freq: float, gain_db: float, fs: int, q: float) -> list[float]:
    """RBJ cookbook 的 peaking biquad, 返回一行 sos [b0 b1 b2 1 a1 a2]。

    gain_db = 0 时结果恰好是单位传递函数, 所以 32 段可以一直挂着(不必增删节点)。
    """
    amp = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq / fs
    alpha = math.sin(w0) / (2.0 * q)
    cos_w0 = math.cos(w0)
    b0 = 1.0 + alpha * amp
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * amp
    a0 = 1.0 + alpha / amp
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / amp
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def build_sos(gains: list[float], fs: int) -> np.ndarray:
    """32 段增益 -> sos 矩阵(32 x 6)。"""
    return np.array([design_peaking(freq, gain, fs, Q)
                     for freq, gain in zip(common.EQ_FREQS, gains)], dtype=np.float64)


def level_db(block: np.ndarray) -> tuple[float, float]:
    """返回 (RMS dBFS, 峰值 dBFS)。"""
    if block.size == 0:
        return -120.0, -120.0
    rms = float(np.sqrt(np.mean(block * block)))
    peak = float(np.max(np.abs(block)))
    rms_db = 20.0 * math.log10(rms) if rms > 1e-9 else -120.0
    peak_db = 20.0 * math.log10(peak) if peak > 1e-9 else -120.0
    return rms_db, peak_db


def main() -> None:
    store = common.open_store()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    version = -1
    sos = build_sos(common.defaults()[0], RATE)
    zi = np.zeros((sos.shape[0], 2), dtype=np.float64)
    volume = 1.0
    volume_db = 0.0

    while True:
        raw = stdin.read(BLOCK_BYTES)
        if not raw:
            break
        if len(raw) % 4:
            raw = raw[: len(raw) - (len(raw) % 4)]

        gains, want_volume_db, new_version = common.read_params(store)
        if new_version != version:
            version = new_version
            sos = build_sos(gains, RATE)
            volume_db = want_volume_db
            volume = 10.0 ** (volume_db / 20.0)

        x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
        if x.size != N:
            x = np.resize(x, N)

        y, zi = sosfilt(sos, x, zi=zi)
        out = y * volume

        in_rms, in_peak = level_db(x)
        out_rms, out_peak = level_db(np.clip(out, -1.0, 1.0))
        common.write_state(store, in_rms, in_peak, out_rms, out_peak, volume_db)

        stdout.write(np.clip(out, -1.0, 1.0).astype("<f4").tobytes())
        stdout.flush()


if __name__ == "__main__":
    main()
