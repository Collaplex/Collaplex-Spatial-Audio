"""湿度滑块验证: 播脉冲, 测输出里的混响尾巴能量随湿量变化。"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import wave

import numpy as np
from scipy.io import wavfile

RATE = 96000
IMPULSE = "/tmp/imp.wav"
REC = "/tmp/imp_rec.wav"
API = "http://127.0.0.1:8789"


def sink() -> str:
    """找数字输出设备的 node.name(含 iec958 的那个)。"""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=20).stdout
        nodes = json.loads(out)
    except (OSError, ValueError) as exc:
        print("  (pw-dump 失败: %s)" % exc)
        return ""
    for node in nodes:
        props = (node.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        name = str(props.get("node.name", ""))
        if "iec958" in name:
            return name
    print("  (没找到 iec958 设备, 现有 sink 已打印在上方)")
    return ""


def post(path: str, body: dict[str, float]) -> None:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def make_impulse() -> None:
    """0.1 秒脉冲 + 1.9 秒静音 = 2 秒素材。

    加静音尾巴是因为 pw-play 起播和 pw-record 启动都有几百毫秒延迟,
    素材太短会在录音真正开始前就播完(录到全零)。
    """
    n = int(RATE * 3.0)
    x = np.zeros(n)
    m = int(RATE * 0.1)
    t = np.arange(m) / RATE
    start = int(RATE * 1.0)          # 脉冲放第 1 秒: 等录音真正跑起来再响
    x[start:start + m] = 0.5 * np.sin(2 * np.pi * 1000 * t) * np.hanning(m)
    data = (x * 32767).astype("<i2")
    with wave.open(IMPULSE, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(np.repeat(data, 2).tobytes())


def read_rec() -> np.ndarray:
    """读回录音文件(取左声道)。"""
    try:
        _rate, data = wavfile.read(REC)
    except OSError:
        return np.zeros(1, dtype=np.float64)
    arr = np.asarray(data, dtype=np.float64)
    return arr[:, 0] if arr.ndim > 1 else arr


def tail_db(rec: np.ndarray) -> float:
    """从录音里的峰值(脉冲)之后 0.2~1.2 秒算能量 = 混响尾巴。

    用峰值自校准, 免得依赖"录音开始时脉冲正好在第 N 秒"。
    """
    if rec.size < RATE:
        return -120.0
    peak_idx = int(np.argmax(np.abs(rec)))
    start = peak_idx + int(0.2 * RATE)
    end = min(peak_idx + int(1.2 * RATE), rec.size)
    if end <= start:
        return -120.0
    rms = float(np.sqrt(np.mean(rec[start:end] ** 2)))
    return 20.0 * np.log10(rms + 1e-12)


def main() -> None:
    dev = sink()
    if not dev:
        print("找不到数字输出设备, 退出")
        return
    print("  设备: %s" % dev.split(".")[-3])
    make_impulse()
    for wet in (0.0, 0.3, 0.6, 1.0):
        post("/api/wet", {"wet": wet})
        time.sleep(0.3)
        # 先起录音再播放: pw-record 启动有延迟, 反过来会漏掉脉冲
        play = subprocess.Popen(["pw-play", "--target", "collaplex_vsink", IMPULSE],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        subprocess.run(["timeout", "3.5", "pw-record", "--target", dev,
                        "-P", "{ stream.capture.sink = true }", "--rate", str(RATE),
                        "--channels", "2", "--format", "f32", REC],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        play.wait(timeout=6)
        recorded = read_rec()
        print("  湿量 %.2f -> 混响尾巴 %6.1f dBFS" % (wet, tail_db(recorded)))
    post("/api/wet", {"wet": 0.3})
    print("  (已恢复湿量 0.3)")


if __name__ == "__main__":
    main()
