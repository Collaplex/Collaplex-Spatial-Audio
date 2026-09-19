"""Collaplex 效果 DSP 公共部分: 前端 <-> DSP 的共享内存协议。

前端(web/server.py) 与 DSP 进程(pipe 插件载荷)通过这一块共享内存通信:

    参数区: 32 段 EQ 增益(dB) + 总音量(dB)   <- 前端写, DSP 读
    版本区: 参数版本号                        <- 前端写完参数后 +1
    状态区: 输入/输出 RMS 与峰值(dB)          <- DSP 写, 前端读

版本号最后写, DSP 只在版本号变化时重读参数, 所以前端一次改多个值也不会让
DSP 读到半更新的数据。
"""
from __future__ import annotations

import mmap
import os
import struct

EQ_BANDS = 32
EQ_PATH = "/dev/shm/collaplex-eq"

# 1/3 倍频程中心频率(20 Hz 起, 32 段, 覆盖到 25 kHz)
EQ_FREQS: list[float] = [
    20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0,
    125.0, 160.0, 200.0, 250.0, 315.0, 400.0, 500.0, 630.0,
    800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0, 3150.0, 4000.0,
    5000.0, 6300.0, 8000.0, 10000.0, 12500.0, 16000.0, 20000.0, 25000.0,
]

GAIN_LIMIT = 15.0
VOLUME_LIMIT = 12.0

_DATA = struct.Struct("<33d")   # 32 段增益 + 总音量
_VER = struct.Struct("<I")      # 参数版本号
_STATE = struct.Struct("<6d")   # in_rms in_peak out_rms out_peak 推子(dB) 保留

DATA_OFF = 0
VER_OFF = _DATA.size
STATE_OFF = _DATA.size + _VER.size
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


def read_params(store: mmap.mmap) -> tuple[list[float], float, int]:
    """读参数: (32 段增益 dB, 总音量 dB, 版本号)。"""
    store.seek(DATA_OFF)
    values = _DATA.unpack(store.read(_DATA.size))
    store.seek(VER_OFF)
    version = _VER.unpack(store.read(_VER.size))[0]
    return list(values[:EQ_BANDS]), float(values[EQ_BANDS]), int(version)


def write_params(store: mmap.mmap, gains: list[float], volume_db: float) -> int:
    """写参数并让版本号 +1, 返回新版本号。"""
    if len(gains) != EQ_BANDS:
        raise ValueError("gains 长度必须是 %d" % EQ_BANDS)
    store.seek(VER_OFF)
    version = int(_VER.unpack(store.read(_VER.size))[0])
    store.seek(DATA_OFF)
    store.write(_DATA.pack(*[float(g) for g in gains], float(volume_db)))
    version = (version + 1) & 0xFFFFFFFF
    store.seek(VER_OFF)
    store.write(_VER.pack(version))
    store.flush()
    return version


def read_state(store: mmap.mmap) -> dict[str, float]:
    """读状态(DSP 写的电平)。"""
    store.seek(STATE_OFF)
    in_rms, in_peak, out_rms, out_peak, gain_db, _spare = _STATE.unpack(store.read(_STATE.size))
    return {
        "in_rms": float(in_rms),
        "in_peak": float(in_peak),
        "out_rms": float(out_rms),
        "out_peak": float(out_peak),
        "gain_db": float(gain_db),
    }


def write_state(store: mmap.mmap, in_rms: float, in_peak: float, out_rms: float,
                out_peak: float, gain_db: float) -> None:
    """写状态(DSP 侧调用)。"""
    store.seek(STATE_OFF)
    store.write(_STATE.pack(float(in_rms), float(in_peak), float(out_rms),
                            float(out_peak), float(gain_db), 0.0))


def defaults() -> tuple[list[float], float]:
    """默认参数: 全平直 + 0 dB 总音量。"""
    return [0.0] * EQ_BANDS, 0.0
