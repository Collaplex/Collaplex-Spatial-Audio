"""三点电平测量: 虚拟声卡入口 / HRTF 输入(=归一化输出) / 数字输出。用来定位哪一级没处理。"""
import array
import json
import math
import subprocess
import time
import wave

RATE = 48000
DEVNULL = subprocess.DEVNULL
SRC = "/tmp/h_white.wav"


def ids() -> dict[str, int]:
    d = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
    out = {}
    for n in d:
        if n.get("type") != "PipeWire:Interface:Node":
            continue
        p = n.get("info", {}).get("props", {}) or {}
        nm = p.get("node.name") or ""
        if p.get("media.class") == "Audio/Sink":
            if nm == "collaplex_vsink":
                out["vsink"] = n["id"]
            elif nm == "collaplex_hrtf_in":
                out["hrtf_in"] = n["id"]
            elif nm.endswith("iec958-stereo"):
                out["out"] = n["id"]
        elif nm == "alsa_input.usb-EDIFIER_Technology_EDIFIER_Fit900NB_4250315939393214-00.mono-fallback":
            out["mic"] = n["id"]
    return out


def rec(target: int, secs: float, path: str) -> float:
    r = subprocess.Popen(["pw-record", "--target", str(target), "-P", "{ stream.capture.sink = true }",
                          "--rate", str(RATE), "--channels", "2", path], stderr=DEVNULL)
    time.sleep(secs)
    r.terminate()
    r.wait()
    w = wave.open(path)
    n = w.getnframes()
    d = array.array("h")
    d.frombytes(w.readframes(n))
    if not d:
        return -99
    rms = (sum(x * x for x in d) / len(d)) ** 0.5 / 32768
    return 20 * math.log10(rms) if rms > 0 else -99


ID = ids()
print("节点 id: %s" % ID)

print("\n--- 静音时(应 -99 = 抓的是 sink monitor; 若 -50 左右 = 抓到麦克风) ---")
for k in ("vsink", "hrtf_in", "out", "mic"):
    if k in ID:
        print("  %-8s %.2f dBFS" % (k, rec(ID[k], 1.0, "/tmp/t_%s.wav" % k)))

print("\n--- 播素材时(素材 -12 dBFS) ---")
p = subprocess.Popen(["pw-play", "--target", "collaplex_vsink", SRC], stderr=DEVNULL)
time.sleep(0.5)
res = {}
for k in ("vsink", "hrtf_in", "out", "mic"):
    if k in ID:
        res[k] = rec(ID[k], 1.2, "/tmp/p_%s.wav" % k)
p.terminate()
p.wait()
for k, v in res.items():
    print("  %-8s %.2f dBFS" % (k, v))
