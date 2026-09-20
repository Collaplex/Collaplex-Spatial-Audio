"""检查混响左右声道配置是否正确 —— 文件级校验 + 物理校验。

★ 判据的坑(2026-09-20 实测): **别用"尾巴左右相关系数"当判据**。
  混响尾巴的能量集中在低频(房间 IR 低频比中高频高 20 dB), 而 HRTF 左右耳在低频
  几乎没有头影差异 -> 无论 IR 是单声道还是立体声, 尾巴左右相关都接近 0.9996,
  完全不敏感, 会把"已修好"误判成"没生效"。

  正确的敏感判据 = **通道探针**: 把某个声道指向一个"左声道全零、右声道为真数据"的
  双声道文件, 再给 channel = 1。若该声道输出**没有**塌掉, 说明 channel 真的按索引
  取到了第 2 声道(实测左右差仅 1.1 dB, 而读错声道会掉 40 dB 以上)。

本脚本做能自动做的两项:
  1. 文件级: IR 是不是双声道? conf 里 revL/revR 有没有 channel = 0 / channel = 1?
  2. 物理级: 湿量拉满时, 左右两路湿声是不是都在出声(尾巴左右能量应当接近)?
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
from scipy.io import wavfile  # noqa: E402

RATE = 96000
PROJ = "/home/lou/桌面/工作区/实验/collaplex音效"
CONF = os.path.expanduser("~/.config/pipewire/pipewire.conf.d/20-collaplex-hrtf.conf")
SYSTEM_IR = "/usr/share/collaplex-audio/reverb/房间混响IR-96k.wav"
PROJ_IR = os.path.join(PROJ, "reverb/房间混响IR-96k.wav")
TMP = "/tmp/cx_chan"
BURST = os.path.join(TMP, "burst.wav")

sys.path.insert(0, os.path.join(PROJ, "dsp"))
import common  # noqa: E402


def find_sink() -> str:
    raw = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for node in json.loads(raw):
        props = (node.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == "Audio/Sink" and "iec958" in name:
            return name
    return ""


def check_files() -> bool:
    ok = True
    print("=== ① 文件级 ===")
    for path in (PROJ_IR, SYSTEM_IR):
        if not os.path.exists(path):
            print(f"  ? {path} 不存在")
            continue
        _sr, data = wavfile.read(path)
        a = np.asarray(data, dtype=np.float64)
        if a.ndim < 2:
            print(f"  ✗ {path} 是单声道 —— 混响两路会完全相同")
            ok = False
        else:
            corr = float(np.corrcoef(a[:, 0], a[:, 1])[0, 1])
            tag = "✓" if corr < 0.999 else "✗ 两声道内容相同"
            print(f"  {tag} {os.path.basename(path)}: {a.shape[1]} 声道, 左右相关 {corr:.4f}")
            if corr >= 0.999:
                ok = False
    with open(CONF, encoding="utf-8") as fh:
        text = fh.read()
    for name, want in (("revL", "channel = 0"), ("revR", "channel = 1")):
        line = next((ln for ln in text.splitlines() if f"name = {name} " in ln), "")
        hit = want in line
        print(f"  {'✓' if hit else '✗'} conf {name}: {'已带 ' + want if hit else '缺少 ' + want}")
        ok = ok and hit
    return ok


def make_burst() -> None:
    os.makedirs(TMP, exist_ok=True)
    rng = np.random.default_rng(11)
    n = int(RATE * 0.05)
    w = np.zeros(int(RATE * 2.5))
    w[:n] = rng.standard_normal(n) * 0.25
    wavfile.write(BURST, RATE, np.stack([w, w], axis=1).astype(np.float32))


def check_audio(sink: str) -> bool:
    print()
    print("=== ② 物理级 (湿量拉满, 尾巴左右能量) ===")
    make_burst()
    st = common.open_store()
    gains, vol, wet_now, _v = common.read_params(st)
    common.write_params(st, gains, vol, 1.0)
    time.sleep(0.6)
    out = os.path.join(TMP, "check.wav")
    player = subprocess.Popen(["pw-play", "--target", "collaplex_hrtf_in", BURST],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec = subprocess.Popen(
        ["timeout", "8", "pw-record", "--target", sink,
         "-P", "{ stream.capture.sink = true }",
         "--rate", str(RATE), "--channels", "2", "--format", "f32", out],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    player.wait(timeout=10)
    rec.wait(timeout=12)
    common.write_params(st, gains, vol, wet_now)

    _sr, data = wavfile.read(out)
    a = np.asarray(data, dtype=np.float64)
    if a.ndim < 2:
        print("  录音异常")
        return False
    env = np.abs(a).sum(axis=1)
    top = float(env.max())
    start = int(np.argmax(env > top * 0.02)) if top > 0 else 0
    tail = a[start + int(RATE * 0.3): start + int(RATE * 2.2)]
    if len(tail) < 1000:
        print("  尾巴太短")
        return False
    lr = 20 * math.log10(float(np.sqrt(np.mean(tail[:, 0] ** 2))) + 1e-15)
    rr = 20 * math.log10(float(np.sqrt(np.mean(tail[:, 1] ** 2))) + 1e-15)
    diff = abs(lr - rr)
    good = diff < 6.0 and lr > -80.0
    print(f"  尾巴 左 {lr:.1f} dBFS  右 {rr:.1f} dBFS  差 {diff:.1f} dB   "
          f"{'✓ 两路湿声都在出声' if good else '✗ 某一路没声音'}")
    print(f"  (湿量已恢复 {wet_now:.2f})")
    return good


def main() -> None:
    sink = find_sink()
    if not sink:
        print("找不到数字输出(iec958)")
        return
    files_ok = check_files()
    audio_ok = check_audio(sink)
    print()
    print(f"结果: 文件级 {'通过' if files_ok else '不通过'} / "
          f"物理级 {'通过' if audio_ok else '不通过'}")
    print("注: 要证明 channel 真的取到了不同声道, 需用通道探针法 —— 见本文件头注释。")


if __name__ == "__main__":
    main()
