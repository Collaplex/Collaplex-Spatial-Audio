"""扫频测链路频响 —— Farina 对数扫频 + 反卷积。

为什么不用粉噪: 粉噪是随机信号, 两次播放的片段不同、链路又有延迟,
用"两段比值谱"算传递函数得到的是相位抵消的垃圾(实测出现 -179 dB 的假深谷)。
确定性信号才能拿到真频响。

做法: 对数扫频 20 Hz -> 20 kHz, 反卷积得链路脉冲响应, 再做 FFT 看频响。
投递点选 collaplex_hrtf_in = **绕过响度归一化**, 测的是线性频响
(投 collaplex_vsink 则会带上归一化的自动增益)。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time

import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve

RATE = 96000
PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
TMP = "/tmp/cx_sweep"
SWEEP = os.path.join(TMP, "扫频.wav")
DUR = 8.0
F1, F2 = 20.0, 20000.0
TAIL = 3.0
AMP = 0.25

sys.path.insert(0, os.path.join(PROJ, "dsp"))
import common  # noqa: E402

BANDS: list[float] = []
_f = 20.0
while _f <= 20000.0:
    BANDS.append(round(_f, 2))
    _f *= 2 ** (1 / 12)


def find_digital_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def make_sweep() -> tuple[np.ndarray, np.ndarray]:
    n = int(DUR * RATE)
    t = np.arange(n) / RATE
    r = DUR / math.log(F2 / F1)
    phase = 2 * math.pi * F1 * r * (np.exp(t / r) - 1.0)
    x = AMP * np.sin(phase)
    k = np.exp(-t / r)                       # Farina 逆滤波器的幅度权
    inv = x[::-1] * k[::-1]
    inv *= 1.0 / (np.sum(inv ** 2) + 1e-30)
    return x.astype(np.float32), inv.astype(np.float64)


def measure(target: str, out: str) -> None:
    total = DUR + TAIL + 3.0
    rec = subprocess.Popen(
        ["timeout", str(int(total) + 6), "pw-record", "--target", _sink,
         "-P", "{ stream.capture.sink = true }",
         "--rate", str(RATE), "--channels", "2", "--format", "f32", out],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    play = subprocess.Popen(["pw-play", "--target", target, SWEEP],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    play.wait(timeout=DUR + 10)
    time.sleep(TAIL + 0.5)
    rec.terminate()
    rec.wait(timeout=8)


def response(path: str, inv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    _rate, data = wavfile.read(path)
    y = data.astype(np.float64)
    if y.ndim > 1:
        y = y[:, 0]
    ir = fftconvolve(y, inv)[: len(y)]
    peak = int(np.argmax(np.abs(ir)))
    ir = ir[peak:]
    n = 1 << 17
    # ★ 绝不能用 hanning: 它在起点为 0, 而 IR 的直达峰正好在起点 -> 主峰被抹掉,
    #   算出来的是窗函数的谱(实测表现为一条 +130 dB 的假斜线)。IR 本身有限长且已衰减,
    #   直接矩形截取即可; 截断点用线性渐出收尾避免硬切泄漏。
    seg = ir[:n].copy()
    fade = n // 8
    if len(seg) >= n:
        seg[-fade:] *= np.linspace(1.0, 0.0, fade)
    spec = np.abs(np.fft.rfft(seg, n))
    freq = np.fft.rfftfreq(n, 1.0 / RATE)
    return freq, spec


def smooth(freq: np.ndarray, spec: np.ndarray, ref_band: tuple[float, float] = (900.0, 1120.0)) -> list[tuple[float, float]]:
    mask = (freq >= ref_band[0]) & (freq <= ref_band[1])
    ref = float(np.max(spec[mask])) if bool(np.any(mask)) else 1.0
    out: list[tuple[float, float]] = []
    for fc in BANDS:
        m = (freq >= fc * 2 ** (-1 / 24)) & (freq <= fc * 2 ** (1 / 24))
        v = float(np.max(spec[m])) if bool(np.any(m)) else 0.0
        out.append((fc, 20 * math.log10(v / (ref + 1e-30) + 1e-30)))
    return out


_sink = ""


def main() -> None:
    global _sink
    _sink = find_digital_sink()
    SINK = _sink
    os.makedirs(TMP, exist_ok=True)
    x, inv = make_sweep()
    wavfile.write(SWEEP, RATE, np.stack([x, x], axis=1))

    st = common.open_store()
    gains, vol, wet_now, _v = common.read_params(st)
    print(f"数字输出: {SINK}")
    print(f"当前: 总音量 {vol:+.1f} dB / 湿量 {wet_now:.3f}")
    print()

    cases: list[tuple[str, str, str]] = [
        ("A 直通(不过链)", SINK, "A_直通"),
        ("B 链路-湿量0", "collaplex_hrtf_in", "B_湿0"),
        ("C 链路-湿量当前", "collaplex_hrtf_in", "C_湿当前"),
        ("D 整链(含归一化)", "collaplex_vsink", "D_整链"),
    ]

    res: dict[str, list[tuple[float, float]]] = {}
    for label, target, tag in cases:
        # ★ 每轮都要显式写一次湿量: 只写一次的话, 后面几轮会沿用被改过的值
        common.write_params(st, gains, vol, 0.0 if tag == "B_湿0" else wet_now)
        time.sleep(0.5)
        path = os.path.join(TMP, tag + ".wav")
        measure(target, path)
        freq, spec = response(path, inv)
        res[tag] = smooth(freq, spec)
        print(f"{label}  已录")
    common.write_params(st, gains, vol, wet_now)

    print()
    show = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 800,
            1000, 1600, 2500, 4000, 6300, 10000, 16000]
    print(f"{'Hz':>8} " + " ".join(f"{t:>12}" for t in ("A直通", "B湿0", "C湿0.3", "D整链")))
    for fc in show:
        row = []
        for tag in ("A_直通", "B_湿0", "C_湿当前", "D_整链"):
            cur = min(res[tag], key=lambda cv: abs(cv[0] - fc))
            row.append(f"{cur[1]:+11.1f}")
        print(f"{fc:>8} " + " ".join(row))

    print()
    for tag in ("B_湿0", "C_湿当前", "D_整链"):
        v = np.array([d for c, d in res[tag] if 20 <= c <= 330])
        fr = np.array([c for c, _d in res[tag] if 20 <= c <= 330])
        low = np.array([d for c, d in res[tag] if 20 <= c <= 200])
        print(f"{tag}: 20~330Hz 中位 {np.median(v):+.1f} dB, 峰谷跨度 {v.max() - v.min():.1f} dB, "
              f"最低 {v.min():+.1f} @{fr[int(np.argmin(v))]:.0f}Hz, 最高 {v.max():+.1f} @{fr[int(np.argmax(v))]:.0f}Hz")
        print(f"        20~200Hz 均值 {low.mean():+.1f} dB")


if __name__ == "__main__":
    main()
