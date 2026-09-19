"""HRTF 独立实例的接入/输出测试: 播到 collaplex_hrtf_in, 录数字输出。"""
import array
import json
import math
import random
import subprocess
import time
import wave

RATE = 48000
DEVNULL = subprocess.DEVNULL
import sys
IN_SINK = sys.argv[1] if len(sys.argv) > 1 else "collaplex_hrtf_in"


def exit_id() -> int:
    d = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
    for n in d:
        if n.get("type") == "PipeWire:Interface:Node":
            p = n.get("info", {}).get("props", {}) or {}
            if p.get("media.class") == "Audio/Sink" and (p.get("node.name") or "").endswith("iec958-stereo"):
                return int(n["id"])
    raise SystemExit("找不到数字输出")


def gen_white(path: str, db: float = -12.0, secs: float = 6.0) -> None:
    n = int(RATE * secs)
    rnd = random.Random(11)
    x = [rnd.gauss(0, 1) for _ in range(n)]
    cur = (sum(v * v for v in x) / n) ** 0.5
    k = (10 ** (db / 20)) * 32768 / cur
    d = array.array("h")
    for v in x:
        s = int(max(-32760, min(32760, v * k)))
        d.append(s)
        d.append(s)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(d.tobytes())


def gen_impulse(path: str, lead: float = 0.5, secs: float = 3.0) -> None:
    d = array.array("h", [0] * (int(RATE * secs) * 2))
    i = int(lead * RATE) * 2
    d[i] = d[i + 1] = 30000
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(d.tobytes())


def capture(src: str, rec_secs: float) -> str:
    out = "/tmp/hr_out.wav"
    r = subprocess.Popen(["pw-record", "--target", str(exit_id()), "-P", "{ stream.capture.sink = true }",
                          "--rate", str(RATE), "--channels", "2", out], stderr=DEVNULL)
    time.sleep(0.5)
    p = subprocess.Popen(["pw-play", "--target", IN_SINK, src], stderr=DEVNULL)
    time.sleep(rec_secs)
    for x in (r, p):
        x.terminate()
        x.wait()
    return out


def load(path: str) -> tuple[list[int], list[int], int]:
    w = wave.open(path)
    fr = w.getframerate()
    d = array.array("h")
    d.frombytes(w.readframes(w.getnframes()))
    return list(d[0::2]), list(d[1::2]), fr


def rms_db(seg: list[int]) -> float:
    if not seg:
        return -99
    v = (sum(x * x for x in seg) / len(seg)) ** 0.5 / 32768
    return 20 * math.log10(v) if v > 0 else -99


def corr(a: list[int], b: list[int]) -> float:
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / math.sqrt(va * vb)


gen_white("/tmp/hr_white.wav")
gen_impulse("/tmp/hr_imp.wav")

print("【接入→输出: 白噪(左右相同) -12 dBFS 推入 collaplex_hrtf_in】")
f = capture("/tmp/hr_white.wav", 4.0)
L, R, fr = load(f)
s = slice(2 * fr, 4 * fr)
print("  左 %7.2f dBFS   右 %7.2f dBFS" % (rms_db(L[s]), rms_db(R[s])))
print("  左右相关系数 %.3f   (直通=1.000; 双耳化后应明显 <1)" % corr(L[s], R[s]))

print("\n【脉冲响应(混响湿量 0.3 的尾巴)】")
f = capture("/tmp/hr_imp.wav", 2.5)
L, R, fr = load(f)
mx = max(range(len(L)), key=lambda i: abs(L[i]))
print("  峰值 %.1f ms" % (mx / fr * 1000))
for lo, hi in ((0, 3), (3, 15), (15, 40), (40, 100), (100, 250), (250, 600), (600, 1200), (1200, 2000)):
    a, b = mx + int(lo * fr / 1000), mx + int(hi * fr / 1000)
    print("   %5d~%-5d ms: %7.2f dBFS" % (lo, hi, rms_db(L[a:b])))
