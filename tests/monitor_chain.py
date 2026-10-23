"""链路监控: 每秒采样各级电平与进程数, 抓"刷视频后没声音"的现场。

    .venv/bin/python tests/monitor_chain.py [秒数, 默认 900]

输出一行一次采样, 同时追加到 /tmp/链路监控.log。

判读(没声音时看哪一级先掉到 -120):
    归一化入 掉     -> 播放流没进来(pipewire 层: 流断/没连上/设备层)
    归一化出 掉     -> 归一化 DSP 在静音
    EQ入 掉         -> 湿量级或 HRTF 级断了
    EQ出 掉         -> EQ / 总音量级断了
    EQ出有电平却没声音 -> 输出设备层(设备掉线/被静音)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import mmap

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dsp"))
import common  # noqa: E402

LOG = Path("/tmp/链路监控.log")
FLOOR = -119.9


def db(x: float) -> float:
    """线性幅度转 dBFS。"""
    if x <= 1e-6:
        return FLOOR
    import math
    return 20.0 * math.log10(x)


def dsp_count() -> int:
    """数 DSP 进程(按 cmdline 精确匹配 /usr/share 或项目路径下的 dsp/*.py)。"""
    n = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit() or not (p / "cmdline").exists():
            continue
        try:
            c = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        if "/dsp/" in c and c.rstrip().endswith(".py") and "python" in c:
            n += 1
    return n


def sample(store: mmap.mmap) -> str:
    """采一次。"""
    st = common.read_state(store)
    ll = common.read_loudness_state()
    peaks = ll.get("peak")
    gains = ll.get("gain_db")
    pin = db(max(float(peaks[0]), float(peaks[1]))) if isinstance(peaks, list) and peaks else FLOOR
    gin = (float(gains[0]) + float(gains[1])) / 2 if isinstance(gains, list) and gains else 0.0
    # 归一化出口电平 = 入口峰值 x 当前增益
    pout = pin + gin
    cols = []
    for i in (0, 1):
        cols.append("%6.1f %6.1f %6.1f %6.1f" % (
            st["in_rms"][i], st["out_rms"][i], st["in_peak"][i], st["out_peak"][i]))
    return ("%s  归一入%7.1f 增益%+5.1f 出%7.1f | L: 入%6.1f 出%6.1f 峰入%6.1f 峰出%6.1f"
            " | R: 入%6.1f 出%6.1f 峰入%6.1f 峰出%6.1f | DSP %d"
            % (time.strftime("%H:%M:%S"), pin, gin, pout,
               st["in_rms"][0], st["out_rms"][0], st["in_peak"][0], st["out_peak"][0],
               st["in_rms"][1], st["out_rms"][1], st["in_peak"][1], st["out_peak"][1],
               dsp_count()))


def main() -> None:
    """跑监控。"""
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    store = common.open_store()
    print("采样中(每秒一次), 同时写入 %s" % LOG)
    print("列: 归一入/增益/出 | 之后是 EQ 级 L 与 R 的 入rms 出rms 入峰 出峰")
    with open(LOG, "a", buffering=1) as log:
        log.write("\n===== 监控开始 %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        for _ in range(secs):
            line = sample(store)
            print(line, flush=True)
            log.write(line + "\n")
            time.sleep(1.0)


main()
