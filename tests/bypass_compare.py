"""直出 vs 经链路 —— 量化"为什么经过链路听起来更好"。

听感(老板 2026-09-20): "直出有闷感; 经链路干净、有层次、有空间感但不糊。"

同一个粉噪素材, 分别投 **硬件 sink**(= 直出, 绕过链路) 与 **collaplex_vsink**(= 经链路),
都从硬件的 monitor 抓回来, 量三件事:

1. **响度 / 峰值因子** —— 归一化把内容提到恒定电平。听音电平变了, 人耳等响曲线下的
   "主观频段平衡"跟着变(小声听什么都薄), 这是"有层次"的一部分来源。
2. **1/3 倍频程(相对 1 kHz)** —— 直出 = 素材原样; 链路里 HRTF 做了**同相合并响应均衡**
   (-7.2 dB 低频) + 房间 IR 低频校平。低频不过量 -> 不再**掩蔽**中高频细节 = "不糊"。
3. **L/R 相关系数** —— HRTF 双耳化把两耳去相关(1.0 -> ~0.7), 声音从"头内"移到"头外",
   这就是"空间感"的客观对应量。
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
TMP = "/tmp/cx_ab"
SRC = "/tmp/cx_ab/粉噪.wav"

sys.path.insert(0, os.path.join(PROJ, "dsp"))
import common  # noqa: E402

BANDS = [20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0,
         250.0, 315.0, 400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0,
         2000.0, 3150.0, 5000.0, 8000.0, 12500.0]

_sink = ""


def ensure_source() -> None:
    """没有素材就生成一段粉噪(峰值 -12 dBFS)。"""
    if os.path.exists(SRC):
        return
    os.makedirs(os.path.dirname(SRC), exist_ok=True)
    rng = np.random.default_rng(20260920)
    x = rng.normal(0.0, 1.0, RATE * 8)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1.0 / RATE)
    spec[1:] /= np.sqrt(f[1:])
    y = np.fft.irfft(spec, x.size)
    y = y / float(np.max(np.abs(y))) * 0.25
    wavfile.write(SRC, RATE, y.astype(np.float32))
    print("已生成素材:", SRC)


def find_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def capture(target: str, out: str, settle: float = 3.0, dur: float = 2.4) -> None:
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


def analyse(path: str) -> tuple[list[float], float, float, float, float, float]:
    """-> (1/3oct 相对 1k, 峰值, RMS, 峰值因子, 超阈值占比, L/R 相关)"""
    _sr, data = wavfile.read(path)
    x = data.astype(np.float64)
    mono = x[:, 0] if x.ndim > 1 else x
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(mono ** 2)))
    crest = 20 * math.log10(peak / (rms + 1e-15) + 1e-15)
    over = float(np.mean(np.abs(mono) > 0.93))
    corr = 1.0
    if x.ndim > 1:
        left = np.asarray(x[:, 0], dtype=np.float64)
        right = np.asarray(x[:, -1], dtype=np.float64)   # 单列时取同一列, 相关恒为 1
        n = min(left.size, right.size)
        left, right = left[:n], right[:n]
        if float(np.std(left)) > 1e-12 and float(np.std(right)) > 1e-12:
            corr = float(np.corrcoef(left, right)[0, 1])
    seg = mono[: 65536 * 3].copy()
    seg = seg - float(np.mean(seg))
    spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size))) ** 2
    freq = np.fft.rfftfreq(seg.size, 1.0 / RATE)
    vals: list[float] = []
    for fc in BANDS:
        m = (freq >= fc * 2 ** (-1 / 6)) & (freq <= fc * 2 ** (1 / 6))
        vals.append(float(np.sum(spec[m])) if bool(np.any(m)) else 1e-30)
    ref = vals[BANDS.index(1000.0)] + 1e-30
    return ([10 * math.log10(v / ref) for v in vals],
            20 * math.log10(peak + 1e-15), 20 * math.log10(rms + 1e-15),
            crest, over, corr)


def main() -> None:
    global _sink
    os.makedirs(TMP, exist_ok=True)
    ensure_source()
    _sink = find_sink()
    if not _sink:
        print("没找到数字输出 sink")
        return
    print("硬件 sink:", _sink)

    st = common.open_store()
    gains, vol, wet_now, _v = common.read_params(st)
    print("面板当前: 湿量 %.2f / 总音量 %+.1f dB / EQ 非零 %d 段"
          % (wet_now, vol, sum(1 for g in gains if abs(g) > 1e-9)))
    print()

    cases = [("A 直出(绕链路)", _sink), ("B 经链路", "collaplex_vsink")]
    rows = []
    for label, target in cases:
        out = os.path.join(TMP, label.split()[0] + ".wav")
        capture(target, out)
        rows.append((label, *analyse(out)))
        time.sleep(0.4)

    print("%-18s %8s %8s %10s %12s %12s" %
          ("条件", "峰值", "RMS", "峰值因子", "超阈值", "L/R相关"))
    for label, _lv, pk, rms, crest, over, corr in rows:
        print("%-18s %7.1f %8.1f %9.1fdB %11.2f%% %12.3f"
              % (label, pk, rms, crest, over * 100, corr))

    print()
    print("1/3 倍频程(相对 1 kHz, dB)")
    print("%8s %12s %12s %10s" % ("Hz", "A 直出", "B 经链路", "差(B-A)"))
    for i, fc in enumerate(BANDS):
        a, b = rows[0][1][i], rows[1][1][i]
        print("%8.0f %12.1f %12.1f %+10.1f" % (fc, a, b, b - a))

    print()
    for lo, hi, tag in ((20, 200, "低频"), (250, 1000, "中低"), (1250, 3150, "中高"), (5000, 12500, "高频")):
        a = float(np.mean([rows[0][1][i] for i, f in enumerate(BANDS) if lo <= f <= hi]))
        b = float(np.mean([rows[1][1][i] for i, f in enumerate(BANDS) if lo <= f <= hi]))
        print("%-6s %5g~%-6g  直出 %+6.1f   链路 %+6.1f   差 %+5.1f dB" % (tag, lo, hi, a, b, b - a))
    print()
    print("响度差(B-A) = %+.1f dB    L/R 相关: 直出 %.3f -> 链路 %.3f"
          % (rows[1][3] - rows[0][3], rows[0][6], rows[1][6]))


if __name__ == "__main__":
    main()
