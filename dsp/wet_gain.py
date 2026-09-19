"""混响湿量增益级 —— pipe 插件载荷(单声道块进, 单声道块出)。

只做一件事: 把湿信号乘以共享内存里的湿量(0~1), 逐样本平滑过渡。

为什么需要它: 混响本身交给 PipeWire 的 convolver(能跑 3 秒长 IR), 但图里的
mixer 增益是**加载时定死**的, 改它必须重载 = 断音。把"湿量"抽成 DSP 之后
滑块就能实时生效。

理论说明: 混响 IR 是**未归一化**的原始响应, 中低频频响比中高频高约 20 dB,
所以 20- 里的混响 convolver 带 gain = 0.126 做标定。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

BLOCK_BYTES = 4096


def main() -> None:
    store = common.open_store()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    version = -1
    wet = 0.0
    wet_prev = 0.0

    while True:
        raw = stdin.read(BLOCK_BYTES)
        if not raw:
            break
        if len(raw) % 4:
            raw = raw[: len(raw) - (len(raw) % 4)]

        _gains, _volume_db, want_wet, new_version = common.read_params(store)
        if new_version != version:
            version = new_version
            wet = max(0.0, min(1.0, want_wet))

        x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
        if x.size == 0:
            continue

        # 逐样本线性过渡, 拖滑块时不会有台阶(zipper 噪声)
        out = x * np.linspace(wet_prev, wet, x.size)
        wet_prev = wet

        stdout.write(out.astype("<f4").tobytes())
        stdout.flush()


if __name__ == "__main__":
    main()
