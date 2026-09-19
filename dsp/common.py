"""Collaplex 效果 DSP 公共部分: 前端 <-> DSP 的共享内存协议。

前端(web/server.py) 与 DSP 进程(filter-chain 的 pipe 插件载荷)通过这一块共享内存通信:

    参数区: 32 段 EQ 增益(dB) + 总音量(dB) + 混响湿量(0~1)   <- 前端写, DSP 读
    版本区: 参数版本号                                       <- 前端写完参数后 +1
    状态区: 每声道 输入/输出 RMS 与峰值(dB) + 推子             <- DSP 写, 前端读

DSP 是**每声道一个进程**(pipe 插件按声道 fork, 两个进程命令相同), 它们靠 flock
抢声道槽位(0 = 左, 1 = 右)各写自己那份状态, 前端才能拿到双声道共 4 个电平表。

版本号最后写, 所以 DSP 只在版本号变化时重读参数, 前端一次改多个值不会读到半更新。
"""
from __future__ import annotations

import fcntl
import mmap
import os
import struct

EQ_BANDS = 32
CHANNELS = 2
EQ_PATH = "/dev/shm/collaplex-eq"
LOCK_PATH = "/dev/shm/collaplex-eq.lock"
LOUDNESS_SHM = "/dev/shm/collaplex-loudness"

GAIN_LIMIT = 15.0
VOLUME_LIMIT = 12.0
WET_LIMIT = 1.0

# 1/3 倍频程中心频率(20 Hz 起, 32 段, 覆盖到 25 kHz)
EQ_FREQS: list[float] = [
    20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0,
    125.0, 160.0, 200.0, 250.0, 315.0, 400.0, 500.0, 630.0,
    800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0, 3150.0, 4000.0,
    5000.0, 6300.0, 8000.0, 10000.0, 12500.0, 16000.0, 20000.0, 25000.0,
]

_PARAMS = struct.Struct("<34d")     # 32 段增益 + 总音量 + 湿量
_VER = struct.Struct("<I")          # 参数版本号
_CH = struct.Struct("<6d")          # in_rms in_peak out_rms out_peak gain_db 保留
_STATE = struct.Struct("<12d")      # 2 声道
_LOUD_F = struct.Struct("<f")       # 归一化 DSP 状态里的单精度字段

PARAMS_OFF = 0
VER_OFF = _PARAMS.size
STATE_OFF = _PARAMS.size + _VER.size
SIZE = STATE_OFF + _STATE.size


def open_store() -> mmap.mmap:
    """打开(不存在则创建)共享内存映射。"""
    fd = os.open(EQ_PATH, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        if os.fstat(fd).st_size < SIZE:
            os.ftruncate(fd, SIZE)
        return mmap.mmap(fd, SIZE)
    finally:
        os.close(fd)


def read_params(store: mmap.mmap) -> tuple[list[float], float, float, int]:
    """读参数: (32 段增益 dB, 总音量 dB, 湿量 0~1, 版本号)。"""
    store.seek(PARAMS_OFF)
    values = _PARAMS.unpack(store.read(_PARAMS.size))
    store.seek(VER_OFF)
    version = _VER.unpack(store.read(_VER.size))[0]
    return (list(values[:EQ_BANDS]), float(values[EQ_BANDS]),
            float(values[EQ_BANDS + 1]), int(version))


def write_params(store: mmap.mmap, gains: list[float], volume_db: float, wet: float) -> int:
    """写参数并让版本号 +1(版本号最后写), 返回新版本号。"""
    if len(gains) != EQ_BANDS:
        raise ValueError("gains 长度必须是 %d" % EQ_BANDS)
    store.seek(VER_OFF)
    version = int(_VER.unpack(store.read(_VER.size))[0])
    store.seek(PARAMS_OFF)
    store.write(_PARAMS.pack(*[float(g) for g in gains], float(volume_db), float(wet)))
    version = (version + 1) & 0xFFFFFFFF
    store.seek(VER_OFF)
    store.write(_VER.pack(version))
    store.flush()
    return version


def read_state(store: mmap.mmap) -> dict[str, list[float]]:
    """读双声道状态: 每个字段是一个 [左, 右] 列表(dB, 未知时为 -120)。"""
    store.seek(STATE_OFF)
    raw = _STATE.unpack(store.read(_STATE.size))
    keys = ("in_rms", "in_peak", "out_rms", "out_peak", "gain_db")
    return {key: [float(raw[ch * 6 + i]) for ch in range(CHANNELS)]
            for i, key in enumerate(keys)}


def write_state(store: mmap.mmap, channel: int, in_rms: float, in_peak: float,
                out_rms: float, out_peak: float, gain_db: float) -> None:
    """写本声道的状态(DSP 侧调用)。"""
    if not 0 <= channel < CHANNELS:
        return
    store.seek(STATE_OFF + channel * _CH.size)
    store.write(_CH.pack(float(in_rms), float(in_peak), float(out_rms),
                         float(out_peak), float(gain_db), 0.0))


def claim_channel() -> int:
    """占一个声道槽位并返回它(0 = 左, 1 = 右)。

    pipe 插件对每个声道 fork 一个进程、命令完全一样, 只能用锁区分: 谁的锁在谁占位。
    锁随进程消失自动释放, 所以重启后会重新分配(左右可能互换, 电平相近时无影响)。
    """
    for channel in range(CHANNELS):
        path = "%s%d" % (LOCK_PATH, channel)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return channel          # 故意不关 fd: 进程活着锁就一直在
        except OSError:
            os.close(fd)
    return 0


def defaults() -> tuple[list[float], float, float]:
    """默认参数: 全平直 + 0 dB 总音量 + 0.3 混响湿量。"""
    return [0.0] * EQ_BANDS, 0.0, 0.3


def read_loudness_state() -> dict[str, object]:
    """读响度归一化 DSP 的状态: 它看到的电平与当前施加的增益。

    DSP 侧布局(每声道 32 字节, 见 dsp/loudness_norm.py):
        +0 平滑能量   +4 块能量   +8 输入峰值   +12 当前增益(dB)
        +64 目标响度(dB)
    peak 是线性幅度, 前端自己转 dBFS。
    """
    try:
        with open(LOUDNESS_SHM, "rb") as fh:
            mm = mmap.mmap(fh.fileno(), 0, prot=mmap.PROT_READ)
            try:
                peaks = [_LOUD_F.unpack_from(mm, ch * 32 + 8)[0] for ch in range(CHANNELS)]
                gains = [_LOUD_F.unpack_from(mm, ch * 32 + 12)[0] for ch in range(CHANNELS)]
                target = _LOUD_F.unpack_from(mm, 64)[0]
            finally:
                mm.close()
    except (OSError, ValueError):
        return {"peak": [-120.0, -120.0], "gain_db": [0.0, 0.0], "target_db": -16.0}
    return {
        "peak": [float(p) for p in peaks],
        "gain_db": [float(g) for g in gains],
        "target_db": float(target),
    }
