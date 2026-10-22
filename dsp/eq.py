# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Collaplex
"""32 段 EQ + 总音量 —— pipe 插件载荷(单声道块进, 单声道块出)。

参数实时来自共享内存(dsp/common.py): 32 段增益(dB) + 总音量(dB)。
做法: 每段一个 peaking biquad, 32 段级联成二阶节(sos), 用 scipy.signal.sosfilt
逐块滤波(C 实现, 保留滤波状态, 无额外延迟), 末级乘总音量, 再做软限幅兜底。

混响不在这里: 它由 PipeWire 的 convolver 完成(能跑 3 秒长 IR), 湿量由独立的
湿量增益级(wet_gain.py)实时控制 —— 图里的 mixer 增益是加载时定死的, 改不了。

每声道一个进程(pipe 插件按声道 fork, 命令相同), 靠 flock 抢声道槽位(0 左 / 1 右),
各写自己那份电平状态 —— 前端因此能画双声道共 4 个电平表。
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
Q = 4.318                       # 1/3 倍频程

CEILING = 0.99                  # -0.09 dBFS 绝对上限(只防真削波)
KNEE = 0.06                     # 软拐点: 只有峰值超过约 -0.63 dBFS 才介入
# 注: 原先 ceiling=0.891 / knee=0.25 让介入点低到 -3.9 dBFS, 而正常音乐峰值在 -4~0 dBFS,
#     于是限幅长期工作 -> 中低频谐波失真(听感"沙沙")。阈值贴近 0 后平时完全透明。


def design_peaking(freq: float, gain_db: float, fs: int, q: float) -> list[float]:
    """RBJ cookbook 的 peaking biquad, 返回 sos 一行 [b0 b1 b2 1 a1 a2]。"""
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
    """32 段 peaking 级联成二阶节(平直段的 biquad 恰好是单位响应)。"""
    return np.array([design_peaking(freq, gain, fs, Q)
                     for freq, gain in zip(common.EQ_FREQS, gains)], dtype=np.float64)


def soft_limit(x: np.ndarray, ceiling: float = CEILING, knee: float = KNEE) -> np.ndarray:
    """软限幅: 幅值在 ceiling-knee 以内的样本原样通过, 超出部分用 tanh 平滑渐近 ceiling。

    和 np.clip 的区别: 不产生平顶(硬削听起来像炸麦), 只把峰值压下来。
    """
    threshold = ceiling - knee
    magnitude = np.abs(x)
    over = magnitude > threshold
    if not bool(np.any(over)):
        return x
    shaped = threshold + knee * np.tanh((magnitude[over] - threshold) / knee)
    result = x.copy()
    result[over] = np.sign(x[over]) * shaped
    return result


def level_db(block: np.ndarray) -> tuple[float, float]:
    """(RMS dBFS, 峰值 dBFS)。"""
    if block.size == 0:
        return -120.0, -120.0
    rms = float(np.sqrt(np.mean(block * block)))
    peak = float(np.max(np.abs(block)))
    rms_db = 20.0 * math.log10(rms) if rms > 1e-9 else -120.0
    peak_db = 20.0 * math.log10(peak) if peak > 1e-9 else -120.0
    return rms_db, peak_db


def main() -> None:
    store = common.open_store()
    channel = common.claim_channel()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    version = -1
    sos: np.ndarray | None = None
    zi: np.ndarray | None = None
    volume = 1.0
    volume_db = 0.0

    while True:
        raw = stdin.read(BLOCK_BYTES)
        if not raw:
            break
        if len(raw) % 4:
            raw = raw[: len(raw) - (len(raw) % 4)]

        gains, want_volume_db, _wet, new_version = common.read_params(store)
        if new_version != version:
            version = new_version
            sos = build_sos(gains, RATE)
            zi = np.zeros((sos.shape[0], 2), dtype=np.float64)
            volume_db = want_volume_db
            volume = 10.0 ** (volume_db / 20.0)

        x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
        if x.size == 0:
            continue

        if sos is None or zi is None:
            out = np.zeros(x.size, dtype=np.float64)
        else:
            y, zi = sosfilt(sos, x, zi=zi)
            out = soft_limit(y * volume)

        in_rms, in_peak = level_db(x)
        out_rms, out_peak = level_db(out)
        common.write_state(store, channel, in_rms, in_peak, out_rms, out_peak, volume_db)

        stdout.write(out.astype("<f4").tobytes())
        stdout.flush()


if __name__ == "__main__":
    main()
