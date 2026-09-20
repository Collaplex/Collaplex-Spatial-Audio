"""把参数写进共享内存: 优先用上次落盘的(params.json), 没有则用默认值。

安装脚本在共享内存不存在时调用它。平时开机后由 dsp/common.py 的 open_store()
自动完成同样的恢复(见那里的注释), 所以这个入口主要给安装/手动修复用。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common


def main() -> None:
    store = common.open_store()
    common.restore_params(store)        # 显式恢复一次(幂等)
    gains, volume_db, wet, version = common.read_params(store)
    source = "落盘文件" if common.load_saved_params() is not None else "默认值"
    nonzero = sum(1 for x in gains if abs(x) > 1e-9)
    print("参数已写入: 来源%s / 总音量 %+.1f dB / 湿量 %.2f / EQ 非零 %d 段 (版本 %d)"
          % (source, volume_db, wet, nonzero, version))


if __name__ == "__main__":
    main()
