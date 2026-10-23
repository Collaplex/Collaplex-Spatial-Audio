"""端到端: 虚拟声卡 -> 归一化 -> HRTF -> EQ -> 数字输出, 验证 EQ 段增益与总音量。"""
from __future__ import annotations

import array
import json
import math
import os
import subprocess
import sys
import time
import wave

import numpy as np

DSP_DIR = "/home/lou/桌面/工作区/实验/collaplex音效/dsp"
sys.path.insert(0, DSP_DIR)
import common  # noqa: E402

RATE = 96000
WAV = "/tmp/e2e_tone.wav"
DEVNULL = subprocess.DEVNULL
store = common.open_store()


def nodes() -> dict[str, int]:
    out = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    res: dict[str, int] = {}
    for n in json.loads(out):
        if n.get("type") != "PipeWire:Interface:Node":
            continue
        props = (n.get("info") or {}).get("props") or {}
        name = props.get("node.name") or ""
        if name:
            res[name] = int(n["id"])
    return res


def make_tone(freq: float = 1000.0, secs: float = 6.0, db: float = -12.0) -> None:
    t = np.arange(int(RATE * secs)) / RATE
    amp = 10.0 ** (db / 20.0) * math.sqrt(2.0)
    mono = np.clip(amp * np.sin(2.0 * math.pi * freq * t), -1.0, 1.0)
    inter = np.empty(mono.size * 2, dtype="<i2")
    inter[0::2] = (mono * 32767).astype("<i2")
    inter[1::2] = (mono * 32767).astype("<i2")
    with wave.open(WAV, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(inter.tobytes())


def play_and_record(target: str, rec_sink: str, secs: float = 6.0) -> np.ndarray:
    rec_path = "/tmp/e2e_rec.wav"
    rec = subprocess.Popen(["pw-record", "--target", str(rec_sink), "-P",
                            "{ stream.capture.sink = true }", "--rate", str(RATE),
                            "--channels", "2", rec_path],
                           stdout=DEVNULL, stderr=DEVNULL)
    time.sleep(0.5)
    play = subprocess.Popen(["pw-play", "--target", target, WAV], stdout=DEVNULL, stderr=DEVNULL)
    time.sleep(secs)
    play.terminate()
    play.wait()
    time.sleep(0.3)
    rec.terminate()
    rec.wait()
    with wave.open(rec_path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    block = data[2::2]
    return block[int(RATE * 2.0): int(RATE * 2.0) + int(RATE * 2.0)]


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-12))


def main() -> None:
    ids = nodes()
    sink_id = next(v for k, v in ids.items() if "iec958" in k)
    make_tone()

    flat = [0.0] * common.EQ_BANDS
    common.write_params(store, flat, 0.0)
    time.sleep(0.3)
    base = rms_db(play_and_record("collaplex_vsink", sink_id))
    print("链路节点: %s" % ", ".join(sorted(k for k in ids if k.startswith("collaplex"))))
    print("平直基线(1 kHz 经 HRTF): %+.2f dBFS" % base)

    gains = flat.copy()
    gains[17] = 12.0
    common.write_params(store, gains, 0.0)
    time.sleep(0.3)
    boosted = rms_db(play_and_record("collaplex_vsink", sink_id))
    print("1 kHz 段 +12 dB:        %+.2f dBFS  (差 %+.2f dB)" % (boosted, boosted - base))

    common.write_params(store, flat, 6.0)
    time.sleep(0.3)
    vol = rms_db(play_and_record("collaplex_vsink", sink_id))
    print("总音量 +6 dB:           %+.2f dBFS  (差 %+.2f dB)" % (vol, vol - base))

    common.write_params(store, flat, 0.0)
    time.sleep(0.2)
    print("已恢复平直 / 0 dB")


if __name__ == "__main__":
    main()
