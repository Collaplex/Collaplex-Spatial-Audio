# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Collaplex
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
import json
import mmap
import os
import struct

EQ_BANDS = 32
CHANNELS = 2
EQ_PATH = "/dev/shm/collaplex-eq"
LOCK_PATH = "/dev/shm/collaplex-eq.lock"
LOUDNESS_SHM = "/dev/shm/collaplex-loudness"

# 参数落盘位置: 面板每次改参数都会写这里, 共享内存不存在时(开机/重启后)自动恢复。
# 没有它的话 /dev/shm 一清空参数就全归零 —— 听感上就是"混响突然没了"(湿量 0)。
# 与响度归一化的状态文件 loudness.json 同目录。
STATE_DIR = os.path.expanduser("~/.local/state/cinema-spatial")
PARAMS_JSON = os.path.join(STATE_DIR, "params.json")

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


def load_saved_params() -> tuple[list[float], float, float] | None:
    """读落盘的参数; 文件不存在或内容坏了都返回 None。"""
    try:
        with open(PARAMS_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
        gains = [float(x) for x in data["eq"]]        # type: ignore[union-attr]
        if len(gains) != EQ_BANDS:
            return None
        return (gains, float(data["volume_db"]), float(data["wet"]))  # type: ignore[arg-type]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_params(gains: list[float], volume_db: float, wet: float) -> None:
    """把参数落盘(先写临时文件再改名, 避免别处读到半截)。值没变就不写。"""
    old = load_saved_params()
    if old is not None and old[0] == gains and old[1] == volume_db and old[2] == wet:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = PARAMS_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"eq": gains, "volume_db": volume_db, "wet": wet}, fh)
        os.replace(tmp, PARAMS_JSON)
    except OSError:
        pass        # 落盘失败不该影响听音(比如状态目录不可写), 静默降级


def restore_params(store: mmap.mmap) -> None:
    """把落盘的参数(没有则默认值)恢复进共享内存, 带上下限夹取。"""
    saved = load_saved_params()
    if saved is None:
        gains, volume_db, wet = defaults()
    else:
        raw_gains, raw_vol, raw_wet = saved
        gains = [max(-GAIN_LIMIT, min(GAIN_LIMIT, g)) for g in raw_gains]
        volume_db = max(-VOLUME_LIMIT, min(VOLUME_LIMIT, raw_vol))
        wet = max(0.0, min(WET_LIMIT, raw_wet))
    write_params(store, gains, volume_db, wet)


def open_store() -> mmap.mmap:
    """打开(不存在则创建)共享内存映射。

    文件是**新创建**的时候(开机/重启后 /dev/shm 已清空)顺手把落盘的参数恢复进去 ——
    否则参数全是 0, 听感上就是"混响没了、EQ 也没了"。恢复幂等, 两个声道进程同时判定
    为"新建"也不要紧(写进去的值一样)。
    """
    created = True
    try:
        created = os.path.getsize(EQ_PATH) < SIZE
    except OSError:
        pass
    fd = os.open(EQ_PATH, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        if os.fstat(fd).st_size < SIZE:
            os.ftruncate(fd, SIZE)
        store = mmap.mmap(fd, SIZE)
    finally:
        os.close(fd)
    if created:
        restore_params(store)
    return store


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
