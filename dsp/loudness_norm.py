#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Collaplex
"""Collaplex 响度归一化器 — PipeWire filter-chain `pipe` 插件载荷

★ 运行环境: 由 filter-chain 的 pipe 插件 fork, stdin=音频进, stdout=音频出。
★ 关键机制: filter-chain 会**按声道复制 graph**, 所以左右声道各起一个本进程实例,
   每个实例只看到**单声道**数据。为保持立体声声像不漂移, 两个实例通过 /dev/shm
   共享各自的能量值, 用"两声道能量和"作为联合响度, 算出**同一个增益**。

★ 铁律: 本进程**绝不能退出** —— pipe 插件的外部程序一旦退出, 音频立刻变静音。
   所有异常都吃掉, 出错时退回纯透传。

算法(简化版 EBU R128 思路, 纯 numpy 向量化, 零逐样本循环):
  块 RMS -> 能量 -> 指数滑动平均(≈400ms 短时响度) -> 门控 ->
  目标电平求增益(dB) -> 限速平滑 -> 峰值保护 -> 施加

环境变量调参(改配置时注入):
  LN_TARGET_DB   目标响度(dBFS), 默认 -16
  LN_GATE_DB     静音门限(dBFS), 默认 -50
  LN_MAX_BOOST   最大提升(dB), 默认 +12
  LN_MAX_CUT     最大衰减(dB), 默认 -18
  LN_SLEW_DB_S   增益变化速率上限(dB/s), 默认 6
  LN_WINDOW_S    响度平滑窗口(s), 默认 0.4
  LN_RATE        采样率, 默认 96000
  LN_BLOCK       每次读取字节数, 默认 4096
"""
import ctypes
import fcntl
import json
import math
import mmap
import os
import select
import signal
import struct
import sys
import time

import numpy as np

# ---------------- 参数 ----------------
TARGET_DB = float(os.environ.get("LN_TARGET_DB", "-16"))   # 兜底默认(状态文件优先)
# ★ 目标电平放状态文件: 面板/CLI 改它, DSP 每秒重读一次 -> 实时生效, 不用重建链路。
LOUDNESS_STATE = os.path.expanduser("~/.local/state/cinema-spatial/loudness.json")
GATE_DB = float(os.environ.get("LN_GATE_DB", "-50"))
MAX_BOOST = abs(float(os.environ.get("LN_MAX_BOOST", "12")))
MAX_CUT = -abs(float(os.environ.get("LN_MAX_CUT", "18")))   # ★ 必须为负, 传正数也自动取负
SLEW_DB_S = float(os.environ.get("LN_SLEW_DB_S", "6"))
WINDOW_S = float(os.environ.get("LN_WINDOW_S", "0.4"))
RATE = float(os.environ.get("LN_RATE", "96000"))
BLOCK = int(os.environ.get("LN_BLOCK", "4096"))
PEAK_CEIL = float(os.environ.get("LN_PEAK_CEIL", "0.985"))
FADE_BLOCKS = int(os.environ.get("LN_FADE_BLOCKS", "4"))       # 起播淡入块数(~170ms @96k/4096)
FADE_GAP_BLOCKS = int(os.environ.get("LN_FADE_GAP_BLOCKS", "24"))  # 连续静音多少块算"重新起播"
IDLE_LIMIT = int(os.environ.get("LN_IDLE_LIMIT", "600"))   # 兜底: 连续无数据这么多秒才退出
DEBUG = os.environ.get("LN_DEBUG", "") not in ("", "0")

_ERR = sys.stderr


def dbg(msg: str) -> None:
    if DEBUG:
        try:
            _ERR.write(msg + "\n")
            _ERR.flush()
        except Exception:
            pass

EPS = 1e-12
GATE_E = 10.0 ** (GATE_DB / 10.0)


def read_target() -> float:
    """当前目标电平(dBFS) —— 状态文件优先, 读不到回落到环境变量/默认.

    ★ 面板"响度归一化"推子写的就是这个值; 主循环每秒重读一次, 所以改完立即生效。
    """
    try:
        with open(LOUDNESS_STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict) and d.get("target_db") is not None:
            return max(-40.0, min(-6.0, float(d["target_db"])))
    except (OSError, ValueError, TypeError):
        pass
    return TARGET_DB

# ---------------- 共享区(两声道交换状态 + 面板读取) ----------------
# ★ web 面板也读这块内存, 所以**字段只增不改顺序**。
# 每声道 32 字节, 两声道; 其后是全局参数。
#   +0   f32  e_smooth  平滑能量(响度判据, ~400ms) —— 归一化的"输入电平"
#   +4   f32  e_block   本块能量(电平表用, 快响应)
#   +8   f32  peak      本块峰值(电平表用)
#   +12  f32  gain_db   本块**实际**施加的增益 dB(含峰值保护) -> 面板推子
#   +16  f64  t         时间戳(monotonic)
#   +24  u32  n         已处理块数(判活)
#   +28  f32  peak_out  ★本块**输出**峰值(2026-09-13 加; 面板"处理后电平"用它,
#                       旧版这里是 _pad=0, 面板会回退到估算值并截顶到 PEAK_CEIL)
#   +64  f32  target_db 目标响度(面板推子的基准线)
#   +68  u32  rate      采样率
SHM_PATH = "/dev/shm/collaplex-loudness"
CH_STRIDE = 32
SHM_SIZE = 80
TARGET_OFF = 64
RATE_OFF = 68


def set_pdeathsig() -> None:
    """父进程(pipewire filter-chain)一旦消失, 本进程立即被内核杀掉。
    ★ 否则本进程会变成孤儿: 继续持有槽位锁, 让新实例拿不到槽位。"""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGKILL)      # PR_SET_PDEATHSIG=1
    except Exception:
        pass


def claim_slot() -> int:
    """用 flock 抢一个声道槽位(0/1), 锁随进程退出自动释放。"""
    for i in (0, 1):
        try:
            fd = os.open("%s.lock%d" % (SHM_PATH, i), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return i  # fd 不关, 锁一直持有
        except OSError:
            try:
                os.close(fd)
            except Exception:
                pass
    # ★★★ 2026-09-13: 两个槽**都**被占 = 已经有一对 DSP 在跑, 本进程必须退出。
    #   旧版这里 `return 0` —— 第 3、4 个实例会**一起写槽0**(没有锁保护), 数据被劈开,
    #   整链静音(老板报"没声音了", 实测 4 个进程抢 2 个槽, 槽1 僵死在 152 秒前)。
    #   注意 DSP 的父进程是 **pipewire 主进程**而不是 filter-chain 实例, 所以重建链路
    #   时 pdeathsig 不触发 → 每重建一次就攒一对孤儿, 必须靠这里自我了断。
    sys.stderr.write("[loudness] 两个槽位都被占用(已有实例在跑) -> 本进程退出\n")
    sys.stderr.flush()
    raise SystemExit(0)


def open_shm() -> mmap.mmap:
    fd = os.open(SHM_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    if os.fstat(fd).st_size < SHM_SIZE:
        os.ftruncate(fd, SHM_SIZE)
    return mmap.mmap(fd, SHM_SIZE)


STALE_S = 1.0   # 对方声道超过这么久没更新, 视为不存在(防残留值污染联合响度)


def read_peer_energy(mm: mmap.mmap, other: int) -> float:
    """读另一声道的平滑能量; 陈旧(NaN/负/超时)一律当 0。"""
    base = other * CH_STRIDE
    try:
        e = struct.unpack_from("<f", mm, base)[0]
        t = struct.unpack_from("<d", mm, base + 16)[0]
    except Exception:
        return 0.0
    if e != e or e < 0.0:                    # NaN
        return 0.0
    if abs(time.monotonic() - t) > STALE_S:  # 陈旧
        return 0.0
    return e


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    set_pdeathsig()
    slot = claim_slot()
    mm = open_shm()
    other = 1 - slot

    # 全局参数写进共享区(面板推子的基准线)
    target_db = read_target()
    try:
        struct.pack_into("<f", mm, TARGET_OFF, target_db)
        struct.pack_into("<I", mm, RATE_OFF, int(RATE))
    except Exception:
        pass
    next_target_check = time.monotonic() + 1.0

    # 平滑状态
    energy_smooth = 0.0
    gain_db = 0.0
    g_prev = 1.0                       # 上一块末尾的线性增益(块间斜坡用)
    _n: int = 0

    block_seconds = BLOCK / 4.0 / RATE          # 每块时长(单声道 float32)
    alpha = 1.0 - math.exp(-block_seconds / WINDOW_S) if WINDOW_S > 0 else 1.0
    max_step_db = SLEW_DB_S * block_seconds

    carry = b""                                    # 不足 4 字节的尾巴
    idle = 0                                       # 连续无数据的秒数
    # ★ 起播淡入(2026-09-13): 从"长时间静音"回到有声的头几块, 上混的 6 路 HRTF 卷积
    #   会产生大幅过冲 —— 实测瞬间冲到 +7.8 dBFS(超满刻度 8dB, 削波), 之后指数衰减,
    #   面板的峰值栏于是"顶满"。淡入让信号平滑爬升即可压掉。
    #   只在**长静音**后触发(连续 FADE_GAP_BLOCKS 块无信号), 免得音乐里的安静段
    #   被反复淡入听出"呼吸"。
    fade_left = 0
    silent_blocks = 0

    # 父进程(filter-chain)死后怎么退出: 用"启动时的 ppid"做基准比对。
    #   为什么不用别的判据:
    #   1) filter-chain 死后管道写端不一定关闭, read() 不一定返回 EOF;
    #   2) 孤儿会被 systemd(subreaper)收养成 session manager 的子进程, ppid 不一定是 1,
    #      所以写死 "ppid==1" 不可靠 —— 但**跟启动时的 ppid 比**可靠。
    #   ★ 绝不用"若干秒无数据"当判据: 音频流本来就有静默期(视频间隙/暂停/切歌),
    #     按它退出会在正常使用中自断链路, 表现为"刷几条视频后没声音, 刷新才恢复"。
    _ppid0 = os.getppid()

    while True:
        try:
            ready, _, _ = select.select([stdin], [], [], 1.0)
        except Exception:
            ready = [stdin]
        if not ready:
            idle += 1
            if os.getppid() != _ppid0:
                break
            if idle >= IDLE_LIMIT:          # 兜底(默认 600s), 防止极长静默留下孤儿
                break
            continue
        idle = 0

        # 目标电平热更新: 面板/CLI 改了 loudness.json, 这里每秒跟上(不用重建链路)
        _now_t = time.monotonic()
        if _now_t >= next_target_check:
            next_target_check = _now_t + 1.0
            _t = read_target()
            if _t != target_db:
                target_db = _t
                try:
                    struct.pack_into("<f", mm, TARGET_OFF, target_db)
                except Exception:
                    pass

        try:
            raw = stdin.read(BLOCK)
        except Exception:
            break
        if not raw:
            break

        buf = carry + raw
        n = len(buf) - (len(buf) % 4)
        carry = buf[n:]
        if n == 0:
            continue

        try:
            x = np.frombuffer(buf[:n], dtype="<f4")
            if x.size == 0:
                continue

            # ---- 1) 块能量 + 滑窗平滑 ----
            energy = float(np.mean(x.astype(np.float64) ** 2))
            energy_smooth += alpha * (energy - energy_smooth)
            if energy_smooth < 0.0:
                energy_smooth = 0.0

            # ---- 2) 跨声道联合响度(两声道能量和) ----
            now = time.monotonic()
            base = slot * CH_STRIDE
            struct.pack_into("<f", mm, base, energy_smooth)
            struct.pack_into("<d", mm, base + 16, now)
            joint = energy_smooth + read_peer_energy(mm, other)

            # ---- 3) 目标增益(dB) ----
            if joint > GATE_E:
                if silent_blocks > FADE_GAP_BLOCKS:     # 长静音后重新起播 → 淡入
                    fade_left = FADE_BLOCKS
                silent_blocks = 0
                level_db = 10.0 * math.log10(joint + EPS)
                want_db = target_db - level_db
                want_db = max(MAX_CUT, min(MAX_BOOST, want_db))
            else:
                silent_blocks += 1
                want_db = gain_db                       # 静音段保持当前增益

            # ---- 4) 限速平滑(避免抽气/爆音) ----
            delta = want_db - gain_db
            if delta > max_step_db:
                delta = max_step_db
            elif delta < -max_step_db:
                delta = -max_step_db
            gain_db += delta

            # ---- 5) 峰值保护(快降, 只作用于本块) ----
            g = 10.0 ** (gain_db / 20.0)
            peak = float(np.max(np.abs(x))) if x.size else 0.0
            if peak * g > PEAK_CEIL > 0.0:
                g = PEAK_CEIL / (peak + EPS)
                gain_db = 20.0 * math.log10(g + EPS)     # 状态跟随, 下块再按限速恢复

            # ---- 6) 施加增益 ----
            # 块内逐样本线性过渡: 整块共用一个增益会在块边界留下台阶(zipper 噪声),
            # 表现为中低频的微弱谐波。用"上一块增益 -> 本块增益"的斜坡把它抹平。
            y = x * np.linspace(g_prev, g, x.size, dtype=np.float64)
            g_prev = g
            if fade_left > 0:                           # 起播淡入: 线性爬升, 压掉卷积过冲
                y = y * ((FADE_BLOCKS - fade_left + 1) / float(FADE_BLOCKS))
                fade_left -= 1
            out_peak = float(np.max(np.abs(y))) if y.size else 0.0   # ★ 输出峰值(面板用, 真实值)
            if y.dtype != np.float32:
                y = y.astype("<f4")

            # ---- 7) 写回 + 状态(web 面板读这块内存画 4 路电平/推子) ----
            _n += 1
            struct.pack_into("<f", mm, base + 4, energy)          # 本块能量(快响应)
            struct.pack_into("<f", mm, base + 8, peak)            # 本块峰值(输入)
            struct.pack_into("<f", mm, base + 12, gain_db)        # 实际增益 -> 推子
            struct.pack_into("<f", mm, base + 28, out_peak)       # ★ 本块输出峰值 -> 面板"处理后电平"
            struct.pack_into("<I", mm, base + 24, _n & 0xFFFFFFFF)
            if DEBUG and _n % 40 == 0:
                dbg("slot=%d blk=%d raw=%.6f smooth=%.8f joint=%.8f lvl=%.2f want=%.2f g=%.2f peak=%.4f" % (
                    slot, _n, energy, energy_smooth, joint,
                    10.0 * math.log10(joint + EPS) if joint > 0 else -999.0,
                    want_db, gain_db, peak))
            stdout.write(y.tobytes())
            stdout.flush()
        except Exception:
            # ★ 任何异常都不能让进程退出(退出=静音), 退回纯透传
            try:
                stdout.write(buf[:n] if n else raw)
                stdout.flush()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
