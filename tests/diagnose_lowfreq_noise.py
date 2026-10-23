"""低频脏 / 沙沙 —— 逐级录音诊断。

素材两种: 粉噪(宽频, 暴露频响) + 200 Hz 纯音(暴露低频谐波 = 沙沙)。
逐级旁路, 每级单独喂同一素材, 录下来对比:

    归一化后    -> collaplex_vsink 的监听(整链第一级的输出)
    干声(湿量0) -> HRTF+混响 级的输出, 但混响湿量临时置 0
    含混响      -> 同上, 湿量恢复 0.3(当前值)
    整链最终    -> 数字输出 sink 的监听(送到耳机的真实信号)

对比口径: 各 1/3 倍频程相对 1 kHz 的能量(dB) —— 低频被抬了多少一眼看出来。
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

RATE = 96000
PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
TMP = "/tmp/cx_diag"
sys.path.insert(0, os.path.join(PROJ, "dsp"))

import common  # noqa: E402

NOISE = os.path.join(TMP, "粉噪.wav")
TONE = os.path.join(TMP, "200Hz纯音.wav")

BANDS = [20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0,
         250.0, 315.0, 400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0,
         2000.0, 3150.0, 5000.0, 8000.0, 12500.0]


def find_digital_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def make_material() -> None:
    os.makedirs(TMP, exist_ok=True)
    rng = np.random.default_rng(20260920)
    n = RATE * 8
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.fft.rfftfreq(n, 1.0 / RATE)
    spec[1:] /= np.sqrt(f[1:] / 1000.0)          # 粉噪: 能量 ∝ 1/f
    spec[0] = 0.0
    pink = np.fft.irfft(spec, n)
    pink *= 0.15 / (np.max(np.abs(pink)) + 1e-12)
    stereo = np.stack([pink, pink], axis=1).astype(np.float32)
    wavfile.write(NOISE, RATE, stereo)

    t = np.arange(RATE * 8) / RATE
    tone = 0.25 * np.sin(2 * np.pi * 200.0 * t)
    wavfile.write(TONE, RATE, np.stack([tone, tone], axis=1).astype(np.float32))


def capture(target: str, src: str, out: str, settle: float = 3.2, dur: float = 2.0) -> None:
    """往 target 投素材, 从数字输出监听录 out。"""
    player = subprocess.Popen(["pw-play", "--target", target, src],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(settle)
        rec = subprocess.Popen(
            ["timeout", str(int(dur) + 3), "pw-record", "--target", SINK,
             "-P", "{ stream.capture.sink = true }",
             "--rate", str(RATE), "--channels", "2", "--format", "f32", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rec.wait(timeout=dur + 8)
    finally:
        player.terminate()
        player.wait(timeout=5)


def read_mono(path: str) -> np.ndarray:
    _rate, data = wavfile.read(path)
    x = data.astype(np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    return x


def band_levels(x: np.ndarray) -> tuple[list[float], float]:
    """每 1/3 倍频程的带内能量(dB, 相对 1 kHz 带), 以及整体峰值。"""
    seg = x[: 65536 * 3]
    seg = seg - float(np.mean(seg))
    spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size))) ** 2
    freqs = np.fft.rfftfreq(seg.size, 1.0 / RATE)
    vals: list[float] = []
    for fc in BANDS:
        m = (freqs >= fc * 2 ** (-1 / 6)) & (freqs <= fc * 2 ** (1 / 6))
        vals.append(float(np.sum(spec[m])) if bool(np.any(m)) else 0.0)
    ref = vals[BANDS.index(1000.0)] + 1e-30
    return [10.0 * math.log10(v / ref + 1e-30) for v in vals], float(np.max(np.abs(x)))


def harmonics(x: np.ndarray, f0: float = 200.0) -> list[float]:
    seg = x[: 65536 * 2]
    spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size))) ** 2
    freqs = np.fft.rfftfreq(seg.size, 1.0 / RATE)

    def at(f: float) -> float:
        m = (freqs >= f - 20) & (freqs <= f + 20)
        return float(np.max(spec[m])) if bool(np.any(m)) else 1e-30

    base = at(f0)
    return [10.0 * math.log10(at(f0 * k) / (base + 1e-30) + 1e-30) for k in (2, 3, 4, 5)]


def stats(x: np.ndarray) -> tuple[float, float, int]:
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x ** 2)))
    clipped = int(np.sum(np.abs(x) > 0.999))
    return 20 * math.log10(peak + 1e-12), 20 * math.log10(rms + 1e-12), clipped


SINK = ""


def main() -> None:
    global SINK
    SINK = find_digital_sink()
    if not SINK:
        print("找不到数字输出 sink")
        return
    make_material()

    st = common.open_store()
    gains, vol, wet_now, _ver = common.read_params(st)
    print(f"数字输出 = {SINK}")
    print(f"当前参数: 总音量 {vol:+.1f} dB, 混响湿量 {wet_now:.3f}")
    print()

    for tag, src in (("粉噪", NOISE), ("200Hz纯音", TONE)):
        print("=" * 78)
        print(f"素材: {tag}")
        # 参考: 直接打进数字输出(不过链路)
        ref = os.path.join(TMP, "ref.wav")
        capture(SINK, src, ref)
        xr = read_mono(ref)[RATE // 2:]
        pk, rms, cl = stats(xr)
        lv, _ = band_levels(xr)
        print(f"  {'直通(不过链)':<14} 峰值{pk:6.1f} RMS{rms:6.1f} dBFS 削波{cl:5d}")
        print(f"    {' '.join('%6.0f' % BANDS[i] for i in range(0, 13))}")
        print(f"    {' '.join('%6.1f' % lv[i] for i in range(0, 13))}")

        # 湿量 0 -> 湿量 原值, 各录一次(在 HRTF+混响 级的输出 = collaplex_eq_in 的监听)
        for wet_try, label in ((0.0, "湿量0(纯HRTF)"), (wet_now, "湿量当前")):
            common.write_params(st, gains, vol, wet_try)
            time.sleep(0.4)
            out = os.path.join(TMP, "stage_%s_%s.wav" % (tag, label))
            capture("collaplex_vsink", src, out)
            x = read_mono(out)[RATE // 2:]
            pk, rms, cl = stats(x)
            lv, _ = band_levels(x)
            print(f"  {label:<12} 峰值{pk:6.1f} RMS{rms:6.1f} dBFS 削波{cl:5d}")
            print(f"    {' '.join('%6.1f' % lv[i] for i in range(0, 13))}")
            if tag == "200Hz纯音":
                h = harmonics(x)
                print("    谐波(相对基频): 2f %+.1f  3f %+.1f  4f %+.1f  5f %+.1f dB" % tuple(h))
        common.write_params(st, gains, vol, wet_now)
        print()

    print("=" * 78)
    print("频响读法: 每列 = 该频段能量相对 1 kHz 的 dB(0 = 与 1 kHz 齐平)")


if __name__ == "__main__":
    main()
