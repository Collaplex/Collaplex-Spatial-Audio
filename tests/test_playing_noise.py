"""修复后: 有流但内容为静音/纯音时, 输出端有没有链路自造的噪声。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import wave

import numpy as np
from scipy.io import wavfile

PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
RATE = 96000
SILENT = "/tmp/probe_silent.wav"
TONE = "/tmp/probe_tone.wav"
REC_S = "/tmp/rec_silent.wav"
REC_T = "/tmp/rec_tone.wav"


def find_digital_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def write_wav(path: str, samples: np.ndarray) -> None:
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(np.repeat(data, 2).tobytes())


def play_record(src: str, dst: str, secs: float) -> None:
    player = subprocess.Popen(["pw-play", "--target", "collaplex_vsink", src],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.35)
    rec = subprocess.Popen(["timeout", str(secs), "pw-record", "--target", sink_name,
                            "-P", "{ stream.capture.sink = true }",
                            "--rate", str(RATE), "--channels", "2", "--format", "f32", dst],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec.wait(timeout=secs + 8)
    player.terminate()
    player.wait(timeout=5)


def analyse(path: str, label: str) -> None:
    try:
        _rate, data = wavfile.read(path)
    except (OSError, ValueError):
        print("  %s: 读不到录音" % label)
        return
    arr = data.astype(np.float64)
    if arr.ndim > 1:
        arr = arr[:, 0]
    if arr.size < 1000:
        print("  %s: 录音太短" % label)
        return
    rms = float(np.sqrt(np.mean(arr ** 2)))
    peak = float(np.max(np.abs(arr)))
    print("  %-22s RMS %8.2f dBFS | 峰值 %8.2f dBFS" % (
        label, 20 * np.log10(max(rms, 1e-12)), 20 * np.log10(max(peak, 1e-12))))


sink_name = ""


def main() -> None:
    global sink_name
    sink_name = find_digital_sink()
    if not sink_name:
        print("找不到数字输出")
        return

    secs = 4.0
    n = int(RATE * secs)
    silent = np.zeros(n)
    tone = 0.1 * np.sin(2 * np.pi * 1000 * np.arange(n) / RATE)   # -20 dBFS 纯音
    write_wav(SILENT, silent)
    write_wav(TONE, tone)

    print("数字输出:", sink_name)
    print()
    play_record(SILENT, REC_S, 3.0)
    analyse(REC_S, "播全零素材")
    play_record(TONE, REC_T, 3.0)
    analyse(REC_T, "播 -20dBFS 纯音")
    print()
    print("判据: 播全零时输出应远低于 -90 dBFS(链路不自造噪声);")
    print("      纯音那一行若有宽频沙沙, RMS 会明显高于纯音本身应有的 -23 dBFS 太多。")


if __name__ == "__main__":
    main()
