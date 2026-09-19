"""把共享内存参数写成默认值(安装脚本在共享内存不存在时调用)。

默认: 全平直 / 总音量 0 dB / 混响湿量 0.3。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common


def main() -> None:
    store = common.open_store()
    gains, volume_db, wet = common.defaults()
    version = common.write_params(store, gains, volume_db, wet)
    print("默认参数已写入: 全平直 / 总音量 %+.1f dB / 湿量 %.2f (版本 %d)"
          % (volume_db, wet, version))


if __name__ == "__main__":
    main()
