"""分析出一条"该补多少"的曲线, 供设计均衡滤波器用。

两件事:
  ① HRIR 漫反射场均衡(DF-EQ): 这套 HRIR 是原始测量数据, 没做过漫反射场均衡,
     表现为"对侧耳低频比自身 1 kHz 高 7~8 dB、所有 IR 高频高 4~5 dB" -> 链路低频过量。
     标准做法: 用所有方向 IR 的**功率平均**当参考, 取 1/sqrt(P_avg) 作为均衡曲线。
  ② 房间 IR 的频响校平: 混响 IR 低频(20~200 Hz)比中高频高 20~25 dB, 湿路灌低频。

只做分析、打印曲线, 不写任何文件。
"""
from __future__ import annotations

import glob
import os
import warnings

import numpy as np
from scipy.io import wavfile

warnings.filterwarnings("ignore")

RATE = 96000
PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
N = 1 << 15                      # FFT 长度 -> 频率分辨率 2.93 Hz

BANDS: list[float] = []
_f = 20.0
while _f <= 20000.0:
    BANDS.append(round(_f, 2))
    _f *= 2 ** (1 / 6)           # 1/6 倍频程


def read_mono(path: str) -> np.ndarray:
    _sr, data = wavfile.read(path)
    a = np.asarray(data, dtype=np.float64)
    if a.ndim > 1:
        a = a[:, 0]
    return a


def smooth_log(freq: np.ndarray, curve: np.ndarray, frac: float = 1 / 6) -> np.ndarray:
    """在 log 频率轴上按 1/frac 倍频程做移动平均(曲线要平滑, 不然是把噪声也均衡了)。"""
    out = np.empty_like(curve)
    for i, fc in enumerate(freq):
        if fc <= 0:
            out[i] = curve[i]
            continue
        m = (freq >= fc * 2 ** (-frac / 2)) & (freq <= fc * 2 ** (frac / 2))
        out[i] = float(np.mean(curve[m])) if bool(np.any(m)) else curve[i]
    return out


def main() -> None:
    freq = np.fft.rfftfreq(N, 1.0 / RATE)
    files = sorted(glob.glob(os.path.join(PROJ, "hrir", "*.wav")))

    power = np.zeros(N // 2 + 1)
    for path in files:
        a = read_mono(path)
        spec = np.fft.rfft(a, N)
        power += np.abs(spec) ** 2
    power /= len(files)

    # 漫反射场均衡曲线: 1/sqrt(功率平均), 归一到 1 kHz
    eq = 1.0 / np.sqrt(power + 1e-30)
    eq = smooth_log(freq, eq, 1 / 6)
    ref_mask = (freq >= 900) & (freq <= 1120)
    eq /= float(np.mean(eq[ref_mask])) if bool(np.any(ref_mask)) else 1.0
    eq_db = 20 * np.log10(eq + 1e-30)

    print(f"HRIR 文件 {len(files)} 条, 功率平均后取 1/sqrt(P) 得均衡曲线")
    print("① HRIR 所需均衡(相对 1 kHz, dB) —— 负值 = 要压掉")
    line: list[str] = []
    for fc in BANDS:
        if fc > 12000:
            break
        m = (freq >= fc * 2 ** (-1 / 12)) & (freq <= fc * 2 ** (1 / 12))
        v = float(np.mean(eq_db[m])) if bool(np.any(m)) else 0.0
        line.append(f"{fc:>7.0f}Hz:{v:+5.1f}")
    for i in range(0, len(line), 5):
        print("   " + "  ".join(line[i:i + 5]))

    print()
    for lo, hi, tag in ((20, 200, "低频 20-200"), (200, 1000, "中低 200-1k"),
                        (1000, 3000, "中高 1k-3k"), (3000, 12000, "高频 3k-12k")):
        m = (freq >= lo) & (freq <= hi)
        print(f"   均值 {tag:>14}: {float(np.mean(eq_db[m])):+6.2f} dB")

    # ---- ② 房间 IR 的频响校平需求 ----
    ir = read_mono(os.path.join(PROJ, "reverb", "房间混响IR-96k.wav"))
    n_ir = 1 << 18                                   # 2.73s IR -> 用大 FFT
    spec_ir = np.abs(np.fft.rfft(ir, n_ir)) ** 2
    f_ir = np.fft.rfftfreq(n_ir, 1.0 / RATE)
    # 用 1/6 倍频程平滑
    sm = np.zeros_like(spec_ir)
    for i in range(len(f_ir)):
        fc = f_ir[i]
        if fc <= 0:
            continue
        m = (f_ir >= fc * 2 ** (-1 / 12)) & (f_ir <= fc * 2 ** (1 / 12))
        sm[i] = float(np.mean(spec_ir[m])) if bool(np.any(m)) else spec_ir[i]
    ir_db = 10 * np.log10(sm + 1e-30)
    ref_m = (f_ir >= 700) & (f_ir <= 1500)
    ir_db -= float(np.mean(ir_db[ref_m]))

    print()
    print("② 房间 IR 自己的频响(相对 0.7-1.5 kHz 均值, dB) —— 正值 = 该频段过强")
    line = []
    for fc in BANDS:
        if fc > 12000:
            break
        m = (f_ir >= fc * 2 ** (-1 / 12)) & (f_ir <= fc * 2 ** (1 / 12))
        v = float(np.mean(ir_db[m])) if bool(np.any(m)) else 0.0
        line.append(f"{fc:>7.0f}Hz:{v:+6.1f}")
    for i in range(0, len(line), 5):
        print("   " + "  ".join(line[i:i + 5]))
    for lo, hi, tag in ((20, 200, "低频 20-200"), (200, 1000, "中低 200-1k"),
                        (1000, 3000, "中高 1k-3k"), (3000, 12000, "高频 3k-12k")):
        m = (f_ir >= lo) & (f_ir <= hi)
        print(f"   均值 {tag:>14}: {float(np.mean(ir_db[m])):+6.2f} dB")

    # ---- ③ 同相合并响应: 实际内容大多是相关的(人声/低频居中), 两耳信号是**相加** ----
    #   功率平均(①)假设各方向互不相关, 会低估低频; 同相合并才是复核实测的那个口径。
    def merge_power(a_name: str, b_name: str) -> np.ndarray:
        xa = np.fft.rfft(read_mono(os.path.join(PROJ, "hrir", a_name)), N)
        xb = np.fft.rfft(read_mono(os.path.join(PROJ, "hrir", b_name)), N)
        return np.abs(xa + xb) ** 2

    p_merge = (merge_power("FL_L.wav", "FR_L.wav") + merge_power("FL_R.wav", "FR_R.wav")) / 2.0
    eq_m = 1.0 / np.sqrt(p_merge + 1e-30)
    eq_m = smooth_log(freq, eq_m, 1 / 6)
    eq_m /= float(np.mean(eq_m[ref_mask])) if bool(np.any(ref_mask)) else 1.0
    eq_m_db = 20 * np.log10(eq_m + 1e-30)

    print()
    print("③ 同相合并响应所需的均衡(左耳 = FL_L+FR_L, 右耳 = FL_R+FR_R 取平均)")
    line = []
    for fc in BANDS:
        if fc > 12000:
            break
        m = (freq >= fc * 2 ** (-1 / 12)) & (freq <= fc * 2 ** (1 / 12))
        v = float(np.mean(eq_m_db[m])) if bool(np.any(m)) else 0.0
        line.append(f"{fc:>7.0f}Hz:{v:+5.1f}")
    for i in range(0, len(line), 5):
        print("   " + "  ".join(line[i:i + 5]))
    for lo, hi, tag in ((20, 200, "低频 20-200"), (200, 1000, "中低 200-1k"),
                        (1000, 3000, "中高 1k-3k"), (3000, 12000, "高频 3k-12k")):
        m = (freq >= lo) & (freq <= hi)
        print(f"   均值 {tag:>14}: {float(np.mean(eq_m_db[m])):+6.2f} dB")


if __name__ == "__main__":
    main()
