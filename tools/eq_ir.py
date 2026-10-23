"""给 IR 做频响校正 —— HRIR 漫反射场均衡 / 房间 IR 低频校平。

用法:
    python tools/eq_ir.py 分析     只看曲线, 不写文件
    python tools/eq_ir.py hrir     给 12 条 HRIR 做均衡(备份 .orig.wav)
    python tools/eq_ir.py reverb   给房间 IR 做低频校平(备份 .orig.wav)
    python tools/eq_ir.py 全部

为什么用 biquad 而不是频域均衡:
  低频搁架需要很长的 FIR 才能表达(42ms 的 FIR 只有 23Hz 分辨率), 而 IIR 搁架几个
  参数就够、且**因果**、不会把 IR 推出非因果的"前兆"。曲线本身是平滑的搁架型,
  biquad 的表征能力完全够。

★ 为什么 HRIR 要用"同相合并响应"而不是标准 DF-EQ:
  标准 DF-EQ 用各方向**功率平均**做参考(假设各方向不相关), 实测只要求压 1.5 dB;
  但真实内容(人声/低频居中, L≈R)两耳信号是**相加**的 —— 同相合并响应实测低频
  高 7.5 dB, 这才是用户听到的那个量。所以这里按同相合并来设计。
"""
from __future__ import annotations

import math
import os
import shutil
import sys
import warnings

import numpy as np
from scipy.io import wavfile
from scipy.signal import sosfilt, sosfreqz

warnings.filterwarnings("ignore")

RATE = 96000
PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
HRIR_DIR = os.path.join(PROJ, "hrir")
REVERB = os.path.join(PROJ, "reverb", "room-ir-96k.wav")

# 校正参数(由 tools/analyze_eq_curve.py 的曲线量出来)
HRIR_SHELF = (500.0, -7.2, 0.707)        # freq, gain_dB, Q  —— 整族 HRIR 共用同一条,
#                                          方向线索(ILD/ITD)因此不受影响
REVERB_SHELF = (300.0, -21.0, 0.70)      # 房间 IR 低频比中频高 24.5 dB, 压到约 +5 dB,
#                                          留一点厚度(真实房间的低频混响本就偏强)


def lowshelf(freq: float, gain_db: float, q: float, fs: int) -> list[float]:
    """RBJ cookbook 的低频搁架。"""
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq / fs
    alpha = math.sin(w0) / (2.0 * q)
    cos_w0 = math.cos(w0)
    sqrt_a = math.sqrt(a)
    b0 = a * ((a + 1) - (a - 1) * cos_w0 + 2 * sqrt_a * alpha)
    b1 = 2 * a * ((a - 1) - (a + 1) * cos_w0)
    b2 = a * ((a + 1) - (a - 1) * cos_w0 - 2 * sqrt_a * alpha)
    a0 = (a + 1) + (a - 1) * cos_w0 + 2 * sqrt_a * alpha
    a1 = -2 * ((a - 1) + (a + 1) * cos_w0)
    a2 = (a + 1) + (a - 1) * cos_w0 - 2 * sqrt_a * alpha
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def band_report(freq: np.ndarray, db: np.ndarray, label: str) -> None:
    out: list[str] = []
    for lo, hi, tag in ((20, 200, "20-200"), (200, 1000, "200-1k"),
                        (1000, 3000, "1k-3k"), (3000, 12000, "3k-12k")):
        m = (freq >= lo) & (freq <= hi)
        out.append(f"{tag}:{float(np.mean(db[m])):+5.1f}")
    print(f"   {label:<12} " + "  ".join(out))


def process(path: str, shelf: tuple[float, float, float], label: str,
            n_fft: int, write: bool) -> None:
    sr, data = wavfile.read(path)
    a = np.asarray(data, dtype=np.float64)
    stereo = a.ndim > 1
    if not stereo:
        a = a[:, None]

    n0, n_ch = a.shape
    spec_before = np.abs(np.fft.rfft(a[:, 0], n_fft)) ** 2
    freq = np.fft.rfftfreq(n_fft, 1.0 / sr)

    sos = np.array([lowshelf(shelf[0], shelf[1], shelf[2], sr)], dtype=np.float64)
    y = np.empty_like(a)
    for ch in range(n_ch):
        y[:, ch] = sosfilt(sos, a[:, ch])

    spec_after = np.abs(np.fft.rfft(y[:, 0], n_fft)) ** 2
    w_curve, h = sosfreqz(sos, worN=n_fft, fs=sr)
    curve_db = 20 * np.log10(np.abs(h) + 1e-12)
    curve_freq = np.asarray(w_curve, dtype=np.float64)

    print(f"[{label}] {os.path.basename(path)}  {n0} 样 x {n_ch} 声道")
    print(f"   搁架: freq={shelf[0]:.0f}Hz gain={shelf[1]:+.1f}dB Q={shelf[2]}")
    band_report(curve_freq, curve_db, "施加的曲线")
    b_db = 10 * np.log10(spec_before + 1e-30)
    a_db = 10 * np.log10(spec_after + 1e-30)
    ref = (freq >= 900) & (freq <= 1100)
    s = float(np.mean(a_db[ref] - b_db[ref]))
    band_report(freq, b_db - float(np.mean(b_db[ref])), "改前")
    band_report(freq, a_db - float(np.mean(a_db[ref])) - s, "改后")

    peak_b = float(np.max(np.abs(a)))
    peak_a = float(np.max(np.abs(y)))
    print(f"   峰值 {peak_b:.5f} -> {peak_a:.5f} ({20 * math.log10(peak_a / peak_b + 1e-12):+.2f} dB)")

    if write:
        # ★ 用独立的备份名。reverb 的 `.orig.wav` 是"剪直达声之前"的版本(那一步建的),
        #   这里存的是"频响校正之前"的版本, 两者不能混用同一个名字 —— 否则重跑一步
        #   会把另一步的基线覆盖掉。重跑顺序: 先 remake_reverb_ir.py, 再 eq_ir.py。
        bak = path + ".pre-eq.wav"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
            print(f"   已备份 -> {os.path.basename(bak)}")
        wavfile.write(path, sr, y.astype(np.float32))
        print("   已写回")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "分析"
    write = mode != "分析"

    if mode in ("分析", "hrir", "全部"):
        print("=== HRIR 漫反射场均衡(同相合并响应口径) ===")
        for name in sorted(os.listdir(HRIR_DIR)):
            if name.endswith(".wav") and not name.endswith(".orig.wav"):
                process(os.path.join(HRIR_DIR, name), HRIR_SHELF, "HRIR",
                        1 << 15, write and mode in ("hrir", "全部"))
        print()

    if mode in ("分析", "reverb", "全部"):
        print("=== 房间 IR 低频校平 ===")
        process(REVERB, REVERB_SHELF, "房间IR", 1 << 18,
                write and mode in ("reverb", "全部"))


if __name__ == "__main__":
    main()
