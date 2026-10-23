"""链路envelope_scope: 直接读 DSP 共享内存, 拿"响度后(vsink_out)"与"EQ后(最终)"两处的包络。

★ 为什么这么做: 这台机器没有 ALSA sink 的 .monitor 节点, 手动把链路输出 link 进录音流
  会让录音流写 NaN(实测), 所以改用链路**自己**的电平表当示波器 —— DSP 每块(1024 样本
  = 10.7 ms @96k)把输入/输出峰值写进 /dev/shm, 高频率轮询就能还原包络。

判据(看形状, 不看绝对值):
  干路脉冲   -> 一个孤立尖峰, 之后立刻掉到 -100 dBFS 以下
  正常混响   -> 尖峰后**单调**衰减的尾巴
  ★双重混响 -> 尾巴里出现第二个驼峰 / 第二段衰减, 或 20~200 ms 处有远高于邻域的孤立峰

用法: python3 envelope_scope.py [标签]
"""
from __future__ import annotations

import mmap
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

EQ_SHM = "/dev/shm/collaplex-eq"
LOUD_SHM = "/dev/shm/collaplex-loudness"
IMPULSE = Path.home() / "桌面/工作区/实验/collaplex音效/tests/取证/脉冲.wav"
LABEL = sys.argv[1] if len(sys.argv) > 1 else "now"
POLL_S = 0.002          # 轮询间隔 2 ms(远快于 10.7 ms 的块率)
RUN_S = 6.0
PLAY_AT = 2.0           # 第 2 秒播脉冲

# common.py 布局: 参数(34×8)+版本(4) 之后是每声道 6×double
STATE_OFF = 34 * 8 + 4
CH_STRIDE = 6 * 8


def db(x: float) -> float:
    """线性 -> dBFS。"""
    return 20.0 * np.log10(max(float(x), 1e-7))


def read_eq(mm: mmap.mmap) -> tuple[list[float], list[float], list[float]]:
    """EQ 段: (每声道 in_peak, out_peak, 已处理块数)。"""
    inp: list[float] = []
    outp: list[float] = []
    cnt: list[float] = []
    for ch in range(2):
        base = STATE_OFF + ch * CH_STRIDE
        inp.append(struct.unpack_from("<d", mm, base + 8)[0])
        outp.append(struct.unpack_from("<d", mm, base + 24)[0])
    mm.seek(STATE_OFF + 2 * CH_STRIDE)
    for ch in range(2):     # 用 loudness 侧的块数当时间轴不合适, 这里用 out_peak 变化计数
        cnt.append(0.0)
    return inp, outp, cnt


def read_loud(mm: mmap.mmap) -> tuple[list[float], list[float], list[float]]:
    """响度段: (每声道输入峰值, 输出峰值, 施加增益 dB)。"""
    peaks: list[float] = []
    outs: list[float] = []
    gains: list[float] = []
    for ch in range(2):
        base = ch * 32
        peaks.append(struct.unpack_from("<f", mm, base + 8)[0])
        gains.append(struct.unpack_from("<f", mm, base + 12)[0])
        outs.append(struct.unpack_from("<f", mm, base + 28)[0])
    return peaks, outs, gains


def main() -> None:
    """采样 + 播脉冲 + 打印包络。"""
    feq = open(EQ_SHM, "rb")
    meq = mmap.mmap(feq.fileno(), 0, prot=mmap.PROT_READ)
    floud = open(LOUD_SHM, "rb")
    mloud = mmap.mmap(floud.fileno(), 0, prot=mmap.PROT_READ)

    # (t, loud_in, loud_out, eq_in, eq_out) —— 每次读到**变化**就记一笔(块级)
    rows: list[tuple[float, float, float, float, float]] = []
    t0 = time.monotonic()
    played = False
    last: dict[str, object] = {}
    while True:
        now = time.monotonic()
        el = now - t0
        if el > RUN_S:
            break
        if not played and el >= PLAY_AT:
            print("播脉冲...")
            subprocess.run(["pw-play", "--target", "collaplex_vsink", str(IMPULSE)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            played = True
        lp, lo, _lg = read_loud(mloud)
        ei, eo, _ = read_eq(meq)
        key = (round(lp[0], 6), round(lo[0], 6), round(ei[0], 6), round(eo[0], 6))
        if key != last.get("k"):
            last["k"] = key
            rows.append((el, lp[0], lo[0], ei[0], eo[0]))
        time.sleep(POLL_S)
    meq.close()
    mloud.close()

    print(f"\n########## 包络标签: {LABEL} ##########")
    print(f"块级采样 {len(rows)} 个点")
    # 找 EQ 后输出的主峰(用脉冲那一侧的左声道)
    eq_out = np.array([r[4] for r in rows], dtype=np.float64)
    times = np.array([r[0] for r in rows], dtype=np.float64)
    if eq_out.size == 0 or np.max(eq_out) <= 1e-9:
        print("!! EQ 后全程无信号 —— 链路没在跑")
        return
    pk = int(np.argmax(eq_out))
    print(f"主峰 {db(eq_out[pk]):.1f} dBFS @ 运行后 {times[pk]:.3f} s")
    print("\n主峰前后的块序列(相对主峰, 每行 8 个点):")
    lo_i, hi_i = max(0, pk - 8), min(len(rows), pk + 96)
    for i in range(lo_i, hi_i, 8):
        seg = rows[i:i + 8]
        txt = " ".join(f"{db(r[4]):6.1f}" for r in seg)
        print(f"  {(i - pk) * 10.7:>7.0f}ms  {txt}")
    # 孤立峰判据
    env = [db(v) for v in eq_out]
    print("\n孤立峰检测(比前后 3 点中位数高 8 dB 以上, 且主峰外):")
    hit = False
    for i in range(4, len(env) - 4):
        if abs(i - pk) <= 2 or env[i] < -70:
            continue
        nb = float(np.median(env[i - 4:i] + env[i + 1:i + 5]))
        if env[i] - nb > 8.0:
            print(f"   !! 距主峰 {(i - pk) * 10.7:.0f} ms: {env[i]:.1f} dBFS (邻域 {nb:.1f})")
            hit = True
    if not hit:
        print("   无(尾巴是单调衰减)")


main()
