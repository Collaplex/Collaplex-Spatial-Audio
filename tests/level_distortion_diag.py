"""电平 / 软限幅诊断 —— 同一素材只改电平, 看低频是不是被"限"出来的。

假设: 链路的**线性**频响是平的(扫频实测 ±2 dB), 粉噪测出来的低频 +20 dB
不是频响, 而是**软限幅的互调产物**(宽频信号被压在 -0.63 dBFS 阈值上,
互调差频堆积在低频 = 听感"低频脏", 高频碎屑 = "沙沙")。

判据: 同一粉噪素材, 只改投递点(是否经归一化 -> 决定电平),
线性/非线性两种状态下的 1/3 倍频程曲线应当出现巨大差异。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
from scipy.io import wavfile  # noqa: E402

RATE = 96000
PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
TMP = "/tmp/cx_lvl"
SRC = "/tmp/cx_diag/粉噪.wav"

sys.path.insert(0, os.path.join(PROJ, "dsp"))
import common  # noqa: E402

BANDS = [20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0,
         250.0, 315.0, 400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0,
         2000.0, 3150.0, 5000.0, 8000.0, 12500.0]

_sink = ""


def find_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def capture(target: str, out: str, settle: float = 3.0, dur: float = 2.2) -> None:
    player = subprocess.Popen(["pw-play", "--target", target, SRC],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(settle)
        rec = subprocess.Popen(
            ["timeout", str(int(dur) + 4), "pw-record", "--target", _sink,
             "-P", "{ stream.capture.sink = true }",
             "--rate", str(RATE), "--channels", "2", "--format", "f32", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rec.wait(timeout=dur + 8)
    finally:
        player.terminate()
        player.wait(timeout=5)


def analyse(path: str) -> tuple[list[float], float, float, float, float]:
    """-> (1/3 倍频程相对 1kHz 的 dB, 峰值dBFS, RMS dBFS, 峰值因子dB, 超阈值样本占比)"""
    _sr, data = wavfile.read(path)
    x = data.astype(np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x ** 2)))
    crest = 20 * math.log10(peak / (rms + 1e-15) + 1e-15)
    over = float(np.mean(np.abs(x) > 0.93))          # 软限幅阈值 ceiling-knee
    seg = x[: 65536 * 3]
    seg = seg - float(np.mean(seg))
    spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size))) ** 2
    freq = np.fft.rfftfreq(seg.size, 1.0 / RATE)
    vals: list[float] = []
    for fc in BANDS:
        m = (freq >= fc * 2 ** (-1 / 6)) & (freq <= fc * 2 ** (1 / 6))
        vals.append(float(np.sum(spec[m])) if bool(np.any(m)) else 1e-30)
    ref = vals[BANDS.index(1000.0)] + 1e-30
    return ([10 * math.log10(v / ref) for v in vals],
            20 * math.log10(peak + 1e-15), 20 * math.log10(rms + 1e-15), crest, over)


def main() -> None:
    global _sink
    _sink = find_sink()
    os.makedirs(TMP, exist_ok=True)
    st = common.open_store()
    gains, vol, wet_now, _v = common.read_params(st)
    print(f"素材 = 粉噪 (RMS -30.7 dBFS)  混响湿量 = {wet_now:.2f}  总音量 = {vol:+.1f} dB")
    print()

    cases: list[tuple[str, str, float, str]] = [
        ("1 直通(不过链, 参考)", _sink, wet_now, "参考"),
        ("2 绕归一化 湿0.0", "collaplex_hrtf_in", 0.0, "低电平"),
        ("3 绕归一化 湿当前", "collaplex_hrtf_in", wet_now, "低电平"),
        ("4 经归一化 湿当前", "collaplex_vsink", wet_now, "高电平"),
    ]
    rows: list[tuple[str, list[float], float, float, float, float, str]] = []
    for label, target, _wet, level in cases:
        if label.startswith("2"):
            common.write_params(st, gains, vol, 0.0)
        elif label.startswith("3"):
            common.write_params(st, gains, vol, wet_now)
        time.sleep(0.5)
        out = os.path.join(TMP, label.split()[0] + ".wav")
        capture(target, out)
        lv, pk, rms, crest, over = analyse(out)
        rows.append((label, lv, pk, rms, crest, over, level))
    common.write_params(st, gains, vol, wet_now)

    print(f"{'条件':<22} {'峰值':>8} {'RMS':>8} {'峰值因子':>9} {'超阈值样本':>10}")
    for label, _lv, pk, rms, crest, over, _level in rows:
        print(f"{label:<22} {pk:>7.1f} {rms:>8.1f} {crest:>8.1f}dB {over * 100:>9.2f}%")

    print()
    print("1/3 倍频程频响(相对 1 kHz, dB) —— 看低频是不是只在高电平时鼓起来")
    print(f"{'Hz':>8} " + " ".join(f"{r[0].split()[0]:>10}" for r in rows))
    for i, fc in enumerate(BANDS):
        print(f"{fc:>8.0f} " + " ".join(f"{r[1][i]:>+10.1f}" for r in rows))
    print()
    for label, lv, _pk, _rms, _crest, _over, level in rows:
        lo = np.mean([lv[i] for i, f in enumerate(BANDS) if 20 <= f <= 200])
        hi = np.mean([lv[i] for i, f in enumerate(BANDS) if 3000 <= f <= 12500])
        print(f"{label:<22} [{level}] 低频20-200均值 {lo:+6.1f} dB   高频3-12k均值 {hi:+6.1f} dB")


if __name__ == "__main__":
    main()
