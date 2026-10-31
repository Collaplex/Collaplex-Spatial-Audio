#!/usr/bin/env python3
"""蓝牙直连 + 单声道混合: 抓 bluez 输出节点的 monitor 口, 看两路是否同信号、有没有声音。

用法:  python3 测蓝牙单声道.py
前置:  面板已把输出切到 bluez_output.xx 且单声道混合打开(链路在跑)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

OUT = os.path.dirname(os.path.abspath(__file__))
SINK = "bluez_output.B0:4B:B0:58:1B:64"
NOISE = os.path.join(OUT, "noise.wav")
CAP = os.path.join(OUT, "cap_bt_mono.wav")


def sh(*args: str) -> str:
    """跑一条命令, 返回 stdout(带 3 秒超时, 不抛)。"""
    got = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return got.stdout


def analyze(path: str) -> dict[str, Any]:
    """读 wav 算左右相关 / 最大差 / RMS(用项目 venv 里的 numpy + scipy)。"""
    import numpy as np
    from scipy.io import wavfile  # type: ignore[import-untyped]

    _rate, data = wavfile.read(path)
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    left, right = x[:, 0], x[:, 1]
    den = float(np.sqrt(np.sum(left ** 2) * np.sum(right ** 2)))
    corr = float(np.sum(left * right) / den) if den else 0.0
    mid = 0.5 * (left + right)
    return {
        "rms_l": float(np.sqrt(np.mean(left ** 2))),
        "rms_r": float(np.sqrt(np.mean(right ** 2))),
        "corr": corr,
        "maxdiff": float(np.max(np.abs(left - right))) if len(left) else 0.0,
        "rms_mid": float(np.sqrt(np.mean(mid ** 2))),
    }


def capture() -> dict[str, Any]:
    """录 3 秒: 先挂 monitor 口, 再放噪声。"""
    import time
    proc = subprocess.Popen(
        ["pw-record", "--target", "0", "--rate", "48000", "--channels", "2",
         "--format", "f32", CAP],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 等采集流的输入口出现再挂 monitor(早了连不上, 晚了会丢开头)
        ready = False
        for _ in range(50):
            dump = sh("pw-dump")
            if '"node.name": "pw-record"' in dump and '"input_FL"' in dump:
                ready = True
                break
            time.sleep(0.1)
        if not ready:
            raise RuntimeError("采集流没起来")
        for channel in ("FL", "FR"):
            subprocess.run(["pw-link", "%s:monitor_%s" % (SINK, channel),
                            "pw-record:input_%s" % channel],
                           capture_output=True, text=True, timeout=10)
        time.sleep(0.4)
        subprocess.run(["pw-play", "--target", "collaplex_vsink", NOISE],
                       capture_output=True, text=True, timeout=20)
        time.sleep(0.5)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return analyze(CAP)


def main() -> None:
    """跑一次并给结论。"""
    got = json.loads(sh("curl", "-s", "--noproxy", "*", "http://127.0.0.1:8789/api/devices"))
    if got.get("routed") != SINK:
        print("当前没挂在 %s 上(实际: %s) —— 先在面板里切过去" % (SINK, got.get("routed")))
        sys.exit(2)
    stat = capture()
    print("蓝牙端读数:", {k: round(v, 5) for k, v in stat.items()})
    same = stat["corr"] > 0.9999 and stat["maxdiff"] < 1e-4
    loud = stat["rms_l"] > 0.002
    print("两路同信号(单声道混合已生效):", "通过" if same else "不通过")
    print("真的有声音送到蓝牙节点:", "通过" if loud else "不通过(似乎静音)")
    sys.exit(0 if (same and loud) else 1)


if __name__ == "__main__":
    main()
