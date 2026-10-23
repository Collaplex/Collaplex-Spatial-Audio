"""回声诊断: 播单脉冲, 录数字输出, 看是平滑混响衰减还是离散回声。

离散回声 = 主峰之后出现孤立的、比周围明显高的峰(延迟 20ms 以上);
平滑混响 = 包络单调下降。分湿量 0 / 0.3 各测一次, 好判断回声是不是混响造成的。
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np
from scipy.io import wavfile

RATE = 96000
MAT = Path("/tmp/echo_mat.wav")
REC = Path("/tmp/echo_rec.wav")
API = "http://127.0.0.1:8789"


def api(path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    """调面板 API。"""
    headers = {"Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(API + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def db(x: float) -> float:
    """线性转 dBFS。"""
    return 20 * np.log10(max(float(x), 1e-7))


def find_monitor() -> str:
    """找数字输出的 monitor 名字。"""
    out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=20).stdout
    for line in out.splitlines():
        if '"node.name"' in line and "iec958" in line:
            return line.split('"')[3] + ".monitor"
    return ""


def main() -> None:
    """跑诊断。"""
    n = int(RATE * 3.5)
    x = np.zeros(n, dtype=np.float32)
    x[int(RATE * 1.0)] = 0.5
    wavfile.write(MAT, RATE, (x * 32767).astype(np.int16))

    mon = find_monitor()
    print("录音设备:", mon or "(没找到, 用默认)")
    args = ["pw-record", "--rate", str(RATE), "--channels", "2", "--format", "s16", str(REC)]
    if mon:
        args[1:1] = ["--target", mon]

    # 静音 Chrome(它会往默认输出送声音, 污染测量), 完事恢复
    subprocess.run(["wpctl", "set-mute", "90", "1"], capture_output=True, timeout=10)

    for wet in (0.0, 0.3):
        api("/api/wet", {"wet": wet})
        time.sleep(0.3)
        REC.unlink(missing_ok=True)
        rec = subprocess.Popen(args)
        time.sleep(0.6)
        subprocess.run(["pw-play", "--target", "collaplex_vsink", str(MAT)], timeout=30)
        time.sleep(0.8)
        rec.terminate()
        rec.wait(timeout=5)

        _r, data = wavfile.read(REC)
        mono = data[:, 0].astype(np.float64) / 32768.0
        pk = int(np.argmax(np.abs(mono)))
        print(f"\n湿量 {wet:.2f}: 主峰 @ {pk / RATE * 1000:.1f} ms, {db(abs(mono[pk])):.1f} dBFS")
        seg: list[float] = []
        for k in range(1, 61):
            a = pk + int(RATE * 0.02 * (k - 1))
            b = pk + int(RATE * 0.02 * k)
            if b > len(mono):
                break
            seg.append(db(np.max(np.abs(mono[a:b]))))
        print("  主峰后每 20ms 包络(dBFS):")
        print("   ", " ".join(f"{v:.0f}" for v in seg[:40]))

    api("/api/wet", {"wet": 0.3})
    subprocess.run(["wpctl", "set-mute", "90", "0"], capture_output=True, timeout=10)
    print("\n(湿量恢复 0.3, Chrome 已取消静音)")


main()
