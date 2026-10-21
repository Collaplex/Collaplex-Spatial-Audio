"""重做房间混响 IR: 把主峰对齐到 0ms, 只保留混响本体。

★ 2026-10-21 修正: 原来硬编码 `TRIM_MS = 22.0`, 但 `.orig.wav` 的主峰在 **30.4 ms**
  —— 裁完湿声仍然比干声晚 8.4 ms, 与零延迟干路相加就是 8~22ms 的延迟复制 +
  梳状滤波, 听感"好几层混响叠在一起"。同时旧的 `FADE_MS = 55.0` 把前 55 ms 压平,
  等于把湿声整段推到 55ms 之后, 又是一层延迟。

  现在:
    1. **按主峰自动裁剪** —— trim = 主峰位置 - LEAD_MS, 主峰落在 ~0.5 ms
    2. **0.5 ms 微渐入** —— 只防起点咔哒; 渐入长度必须 ≤ LEAD_MS, 否则主峰被窗压掉
    3. 归一化到 TARGET_PEAK, 与实听版本同量级(conf 里 gain 不变)

★ 声道: 原 IR 是**双声道立体声**(左右相关 0.884), 处理必须逐声道做、保持双声道输出。
  2026-09-19 版这里写了 `x = x[:, 0]` —— 把右声道丢掉了, 输出的 IR 变成单声道,
  而 filter-chain 里 revL/revR 读同一个文件 -> 两路湿声 100% 同相: 混响糊在正中间、
  低频同相叠加 +6 dB。现在改成: 双声道各自处理后原样写回, conf 里用
  `channel = 0` / `channel = 1` 分别取左右。

自检: 跑完 `np.argmax(np.abs(ir[:,0])) / rate * 1000` 必须 < 2 ms。

产出 reverb/房间混响IR-96k.wav(原文件备份为 .orig.wav), 并打印标定用的增益。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from scipy.io import wavfile

IR = Path("reverb/房间混响IR-96k.wav")
BAK = Path("reverb/房间混响IR-96k.orig.wav")
LEAD_MS = 0.5          # 主峰前保留的余量: 湿声必须与零延迟干声对齐, 否则就是回声
FADE_MS = 0.5          # 渐入必须 ≤ LEAD_MS(否则主峰被窗压掉, argmax 会漂到后面的反射上)
TARGET_PEAK = 0.0975   # 与 2026-10-21 实听版本同量级, conf 里 gain=0.61 保持不变


def main() -> None:
    """处理 IR, 逐声道, 保持双声道。"""
    if not BAK.exists():
        shutil.copy2(IR, BAK)
        print(f"已备份当前 IR -> {BAK}")

    # ★ 一律从原始备份读: 当前 IR 可能已经被上一版降成单声道了
    rate, data = wavfile.read(BAK)
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    n_ch = int(x.shape[1])
    n0 = int(x.shape[0])
    if n_ch > 1:
        corr0 = float(np.corrcoef(x[:, 0], x[:, 1])[0, 1])
        print(f"源 IR: {n0} 样 x {n_ch} 声道, 左右相关 {corr0:.4f}")
    else:
        print(f"源 IR: {n0} 样 x {n_ch} 声道")
    peak0 = float(np.abs(x).max())
    rms0 = float(np.sqrt((x ** 2).mean()))

    # ★ 按主峰自动对齐: 原版硬编码 TRIM_MS=22 而主峰在 30.4ms, 湿声永远晚 8ms
    dominant = int(np.argmax(np.abs(x[:, 0])))
    trim = max(0, dominant - int(rate * LEAD_MS / 1000))
    print(f"主峰在 {dominant / rate * 1000:.2f} ms -> 裁掉前 {trim / rate * 1000:.2f} ms")
    y = x[trim:].copy()

    # 前 FADE_MS 余弦渐入(升余弦的前半段) —— 逐声道同一个窗
    fade = int(rate * FADE_MS / 1000)
    if fade < len(y):
        t = np.arange(fade) / fade
        w = 0.5 * (1 - np.cos(np.pi * t))
        y[:fade] *= w[:, None]

    peak1 = float(np.abs(y).max())
    rms1 = float(np.sqrt((y ** 2).mean()))
    print(f"原 IR: 峰值 {peak0:.4f}, RMS {rms0:.6f}")
    print(f"新 IR: {len(y)} 样, 峰值 {peak1:.4f}, RMS {rms1:.6f}")
    print(f"峰值比 {peak1 / peak0:.4f} ({20 * np.log10(peak1 / peak0):+.2f} dB)")
    print(f"RMS 比 {rms1 / rms0:.4f} ({20 * np.log10(rms1 / rms0):+.2f} dB)")

    # 保持峰值量级与原版一致(后面 gain 仍按 0.126 用, 电平不会跑)
    y *= TARGET_PEAK / peak1
    wavfile.write(IR, rate, y.astype(np.float32))
    print(f"已写入 {IR}")

    if n_ch > 1:
        corr = float(np.corrcoef(y[:, 0], y[:, 1])[0, 1])
        print(f"\n保持双声道: 处理后左右相关 = {corr:.4f}")
        print(f"  左 RMS {np.sqrt((y[:, 0] ** 2).mean()):.6f} / 右 RMS {np.sqrt((y[:, 1] ** 2).mean()):.6f}")

    print("\n新 IR 每 10ms 包络(左声道):")
    z = np.abs(y[:, 0])
    for k in range(0, 14):
        a, b = int(rate * 0.01 * k), int(rate * 0.01 * (k + 1))
        if b > len(z):
            break
        print(f"  {k * 10:4d} ms   {20 * np.log10(max(z[a:b].max(), 1e-7)):7.1f} dB")


main()
