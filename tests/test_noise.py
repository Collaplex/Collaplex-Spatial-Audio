"""① 块长一对一验证 ② 数字输出底噪测量。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
sys.path.insert(0, os.path.join(PROJ, "dsp"))

import common  # noqa: E402

PY = os.path.join(PROJ, ".venv/bin/python")
EQ = os.path.join(PROJ, "dsp/eq.py")
REC = "/tmp/noise_probe.wav"


def find_digital_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def block_test() -> None:
    print("=== ① 块长一对一(不等长块不应被填充) ===")
    store = common.open_store()
    for nbytes in (4096, 2048, 1024, 512, 384):
        n = nbytes // 4
        tone = np.sin(2 * np.pi * 1000 * np.arange(n) / 96000.0)
        x = (0.3 * tone).astype("<f4")
        common.write_params(store, [0.0] * 32, 0.0)
        proc = subprocess.run([PY, EQ], input=x.tobytes(), capture_output=True, timeout=60)
        ok = len(proc.stdout) == len(x.tobytes())
        print("  喂 %5d 字节 -> 出 %5d 字节   %s" % (len(x.tobytes()), len(proc.stdout),
                                                    "✓ 一对一" if ok else "✗ 被填充!"))


def noise_test(sink: str) -> None:
    print()
    print("=== ② 数字输出底噪(静默 3 秒, 单位 dBFS) ===")
    if not sink:
        print("  找不到数字输出, 跳过")
        return
    for label, extra in (("静默(无播放)", []),):
        subprocess.run(["timeout", "3", "pw-record", "--target", sink,
                        "-P", "{ stream.capture.sink = true }",
                        "--rate", "96000", "--channels", "2", "--format", "f32", REC, *extra],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _rate, data = wavfile.read(REC)
        except (OSError, ValueError):
            print("  录音失败")
            return
        arr = data.astype(np.float64)
        if arr.ndim > 1:
            arr = arr[:, 0]
        if arr.size == 0:
            print("  空录音")
            return
        rms = float(np.sqrt(np.mean(arr ** 2)))
        peak = float(np.max(np.abs(arr)))
        print("  %-12s RMS %7.2f dBFS | 峰值 %7.2f dBFS | 样点 %d" % (
            label, 20 * np.log10(max(rms, 1e-12)), 20 * np.log10(max(peak, 1e-12)), arr.size))
        print("  (参考: 数字域本底一般应低于 -90 dBFS; 明显高于此值说明链路在自造噪声)")


def main() -> None:
    block_test()
    noise_test(find_digital_sink())


if __name__ == "__main__":
    main()
