#!/usr/bin/env python3
"""读 Collaplex 响度归一化的共享状态(诊断 / web 面板数据源)

共享区布局(见 loudness_norm.py 顶部注释):
  每声道 32 字节, 两声道:
    +0   f32  e_smooth  平滑能量(响度判据) —— 归一化的"输入电平"
    +4   f32  e_block   本块能量(快响应)
    +8   f32  peak      本块峰值
    +12  f32  gain_db   本块实际施加的增益 dB -> 推子
    +16  f64  t         时间戳(monotonic)
    +24  u32  n         已处理块数(判活)
  +64  f32  target_db 目标响度
  +68  u32  rate      采样率

用法:
  python3 state.py            打印一次
  python3 state.py --watch    每 0.5 秒刷新(诊断动态平衡过程)
"""
import json
import math
import mmap
import os
import struct
import sys
import time

SHM_PATH = "/dev/shm/collaplex-loudness"
CH_STRIDE = 32
SHM_SIZE = 80
TARGET_OFF = 64
RATE_OFF = 68
STALE_S = 1.0

EPS = 1e-12


def db(x: float) -> float:
    """线性能量/幅度 -> dBFS(0 以下)。"""
    return 10.0 * math.log10(x + EPS)


def read_slot(mm: mmap.mmap, i: int) -> dict[str, float]:
    base = i * CH_STRIDE
    e_smooth = struct.unpack_from("<f", mm, base)[0]
    e_block = struct.unpack_from("<f", mm, base + 4)[0]
    peak = struct.unpack_from("<f", mm, base + 8)[0]
    gain = struct.unpack_from("<f", mm, base + 12)[0]
    t = struct.unpack_from("<d", mm, base + 16)[0]
    n = struct.unpack_from("<I", mm, base + 24)[0]
    age = time.monotonic() - t
    return {
        "e_smooth": e_smooth,
        "e_block": e_block,
        "peak": peak,
        "gain_db": gain,
        "n": n,
        "age": age,
        "alive": age < STALE_S,
    }


def snapshot() -> dict[str, object]:
    if not os.path.exists(SHM_PATH):
        return {"ok": False, "err": "共享区不存在(响度归一化未启用)"}
    fd = os.open(SHM_PATH, os.O_RDONLY)
    try:
        mm = mmap.mmap(fd, SHM_SIZE, prot=mmap.PROT_READ)
        try:
            target = struct.unpack_from("<f", mm, TARGET_OFF)[0]
            rate = struct.unpack_from("<I", mm, RATE_OFF)[0]
            ch = [read_slot(mm, 0), read_slot(mm, 1)]
        finally:
            mm.close()
    finally:
        os.close(fd)

    out: dict[str, object] = {
        "ok": bool(ch[0]["alive"] or ch[1]["alive"]),
        "target_db": round(target, 2),
        "rate": rate,
    }
    for i, name in ((0, "L"), (1, "R")):
        c = ch[i]
        # 原始输入电平 = 快响应块能量; 处理后 = 原始 + 实际增益
        raw_db = db(c["e_block"])
        raw_pk = 20.0 * math.log10(c["peak"] + EPS) if c["peak"] > 0 else -120.0
        out["raw_" + name] = round(raw_db, 2) if c["alive"] else None
        out["raw_peak_" + name] = round(raw_pk, 2) if c["alive"] else None
        out["post_" + name] = round(raw_db + c["gain_db"], 2) if c["alive"] else None
        out["gain_db_" + name] = round(c["gain_db"], 2) if c["alive"] else None
        # 平滑响度(归一化真正依据的那条)
        out["loud_" + name] = round(db(c["e_smooth"]), 2) if c["alive"] else None
    if ch[0]["alive"] and ch[1]["alive"]:
        joint = ch[0]["e_smooth"] + ch[1]["e_smooth"]
        out["loud_joint"] = round(db(joint), 2)
        out["deviation_db"] = round(target - db(joint), 2)   # >0 = 还要提, <0 = 还要压
    return out


def main() -> None:
    watch = "--watch" in sys.argv
    try:
        if not watch:
            print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
            return
        print("每 0.5 秒刷新 (Ctrl-C 退出)")
        print("%8s %9s %9s %9s %9s %8s" % (
            "时间", "原始L", "原始R", "处理后L", "处理后R", "增益dB"))
        t0 = time.time()
        while time.time() - t0 < 3600:
            s = snapshot()
            if not s.get("ok"):
                print("  (无数据: %s)" % s.get("err", "声道未在跑"))
            else:
                fmt = lambda k: ("%9.2f" % s[k]) if s.get(k) is not None else "%9s" % "-"
                _g = s.get("gain_db_L")
                gv = float(_g) if isinstance(_g, (int, float)) else 0.0
                print("%8.1f %s %s %s %s %8.2f" % (
                    time.time() - t0, fmt("raw_L"), fmt("raw_R"),
                    fmt("post_L"), fmt("post_R"), gv))
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
