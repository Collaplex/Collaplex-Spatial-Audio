"""决定性对比: 直接播到设备(旁路链路) vs 经过 Collaplex 链路, 噪声底谁更高。"""
from __future__ import annotations

import json
import subprocess
import time
import wave

import numpy as np
from scipy.io import wavfile

RATE = 96000
SRC = "/tmp/probe_silent.wav"
REC = "/tmp/rec_bypass.wav"
SINK = ""


def find_digital_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def write_silent() -> None:
    with wave.open(SRC, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(np.zeros(RATE * 4 * 2, dtype="<i2").tobytes())


def measure(target: str, label: str) -> None:
    player = subprocess.Popen(["pw-play", "--target", target, SRC],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.4)
    rec = subprocess.Popen(["timeout", "3", "pw-record", "--target", SINK,
                            "-P", "{ stream.capture.sink = true }",
                            "--rate", str(RATE), "--channels", "2", "--format", "f32", REC],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec.wait(timeout=12)
    player.terminate()
    player.wait(timeout=5)
    try:
        _rate, data = wavfile.read(REC)
    except (OSError, ValueError):
        print("  %-28s 读不到录音" % label)
        return
    arr = data.astype(np.float64)
    if arr.ndim > 1:
        arr = arr[:, 0]
    if arr.size < 1000:
        print("  %-28s 录音太短" % label)
        return
    rms = float(np.sqrt(np.mean(arr ** 2)))
    peak = float(np.max(np.abs(arr)))
    print("  %-28s RMS %8.2f dBFS | 峰值 %8.2f dBFS" % (
        label, 20 * np.log10(max(rms, 1e-12)), 20 * np.log10(max(peak, 1e-12))))


def main() -> None:
    global SINK
    SINK = find_digital_sink()
    if not SINK:
        print("找不到数字输出")
        return
    write_silent()
    print("设备:", SINK)
    print("素材: 全零(4 秒), 谁在引入噪声?")
    print()
    measure(SINK, "① 旁路(直接播到设备)")
    measure("collaplex_vsink", "② 经 Collaplex 链路")
    print()
    print("判据: ② - ① 的差值 = 链路自己引入的噪声。差值 < 6 dB 基本可忽略。")


if __name__ == "__main__":
    main()
