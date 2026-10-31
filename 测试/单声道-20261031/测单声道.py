"""单声道混合实测: 同一段"左右独立"的宽带噪声, 分别走 立体声/单声道 链, 在设备端取数。

抓取方式: pw-record --target 0 起一个不自动连线的采集流, 再把**输出设备的 monitor 口**
(pw-dump 里 sink 节点上的 monitor_FL / monitor_FR)手工接到它上面 —— 这样录到的就是
这台设备实际收到的样本, 不依赖 pw-record 的 --target 认不认 sink 名(它不认, 会退回默认源)。

判据:
  1. 结构: 单声道模式下 collaplex_mono_in/out 在; eq_out -> mono_in; mono_out -> 设备;
     且 eq_out **不**直连设备
  2. 位级: 单声道模式设备收到的 FL/FR 逐样本相同(相关 1.0000, 最大差≈0);
     立体声模式是双耳信号, 相关明显 < 1
  3. 混合而非"只取一路": 单声道 RMS ≈ 立体声那次 0.5*(L+R) 的 RMS(±15%)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

import numpy as np
import scipy.io.wavfile as wav

OUT = os.path.expanduser("~/归档/工作区/实验/collaplex-audio/测试/单声道-20261031")
ANALOG = "alsa_output.pci-0000_05_00.6.analog-stereo"
RATE = 48000


def sh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


def dump() -> list[dict[str, Any]]:
    raw = sh("pw-dump").stdout
    return json.loads(raw) if raw.strip() else []


def make_noise(path: str, sec: float = 3.0) -> None:
    """左右独立白噪声(带限), 立体声内容才有意义。"""
    rng = np.random.default_rng(20261031)
    n = int(RATE * sec)
    x = rng.standard_normal((n, 2)).astype(np.float32) * 0.10
    x = np.stack([np.convolve(x[:, c], [1, -0.95], mode="same") for c in range(2)], axis=1)
    b = np.ones(8, dtype=np.float32) / 8
    x = np.stack([np.convolve(x[:, c], b, mode="same") for c in range(2)], axis=1)
    x = np.clip(x, -0.9, 0.9)
    wav.write(path, RATE, (x * 32767).astype(np.int16))


def _ports(objs: list[dict[str, Any]]) -> tuple[dict[int, str], dict[int, tuple[str, str]]]:
    nodes: dict[int, str] = {}
    ports: dict[int, tuple[str, str]] = {}
    for o in objs:
        props = (o.get("info") or {}).get("props") or {}
        if o.get("type") == "PipeWire:Interface:Node":
            nodes[int(str(o["id"]))] = str(props.get("node.name", ""))
        elif o.get("type") == "PipeWire:Interface:Port":
            nid = int(str(props.get("node.id", -1)))
            ports[int(str(o["id"]))] = (nodes.get(nid, ""), str(props.get("port.name", "")))
    return nodes, ports


def wiring() -> dict[str, Any]:
    objs = dump()
    nodes, ports = _ports(objs)
    links: list[tuple[str, str]] = []
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Link":
            continue
        info = o.get("info") or {}
        src = ports.get(int(str(info.get("output-port-id", -1))), ("", ""))[0]
        dst = ports.get(int(str(info.get("input-port-id", -1))), ("", ""))[0]
        if src and dst:
            links.append((src, dst))
    names = set(nodes.values())
    return {
        "mono_nodes": sorted(n for n in names if "mono" in n),
        "eq_out_to": sorted({d for s, d in links if s == "collaplex_eq_out"}),
        "mono_out_to": sorted({d for s, d in links if s == "collaplex_mono_out"}),
    }


def capture(tag: str) -> tuple[np.ndarray, np.ndarray, str]:
    """播噪声进链, 同时把输出设备的 monitor 口接到采集流上, 录 2.5 秒。"""
    src = os.path.join(OUT, "noise.wav")
    dst = os.path.join(OUT, "cap_%s.wav" % tag)
    if os.path.exists(dst):
        os.unlink(dst)
    rec = subprocess.Popen(
        ["pw-record", "--target", "0", "--rate", str(RATE), "--channels", "2",
         "--format", "f32", dst], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 等采集流出现, 再把设备 monitor 口接上
    linked = False
    for _ in range(40):
        nodes, ports = _ports(dump())
        rec_ids = {nid for nid, name in nodes.items() if name == "pw-record"}
        has_in = any(name == "pw-record" and pname.startswith("input_")
                     for name, pname in ports.values())
        if rec_ids and has_in:
            time.sleep(0.2)
            ok = 0
            for chan in ("FL", "FR"):
                r = sh("pw-link", "%s:monitor_%s" % (ANALOG, chan),
                       "pw-record:input_%s" % chan)
                ok += 1 if r.returncode == 0 else 0
            linked = ok == 2
            break
        time.sleep(0.1)
    if not linked:
        rec.kill()
        raise RuntimeError("采集流没连上设备 monitor 口")
    time.sleep(0.4)
    t0 = time.time()
    sh("pw-play", "--target", "collaplex_vsink", src)
    print("    播放耗时 %.2fs" % (time.time() - t0))
    time.sleep(0.3)
    rec.terminate()
    try:
        rec.wait(timeout=5)
    except subprocess.TimeoutExpired:
        rec.kill()
    _rate, data = wav.read(dst)
    arr = data.astype(np.float64)
    if data.dtype.kind != "f":
        arr = arr / 32768.0
    if arr.ndim == 1:
        arr = np.stack([arr, arr], axis=1)
    return arr[:, 0], arr[:, 1], dst


def analyse(l: np.ndarray, r: np.ndarray) -> dict[str, float]:
    n = min(len(l), len(r))
    l, r = l[:n], r[:n]
    a, b = int(n * 0.25), int(n * 0.75)
    l, r = l[a:b], r[a:b]
    denom = float(np.sqrt(np.sum(l ** 2) * np.sum(r ** 2))) or 1.0
    return {
        "rms_l": float(np.sqrt(np.mean(l ** 2))),
        "rms_r": float(np.sqrt(np.mean(r ** 2))),
        "corr": float(np.sum(l * r) / denom),
        "maxdiff": float(np.max(np.abs(l - r))),
        "rms_mid": float(np.sqrt(np.mean(((l + r) / 2) ** 2))),
    }


def set_device(name: str, mono: bool) -> dict[str, Any]:
    out = sh("curl", "-s", "--noproxy", "*", "-X", "POST", "-H",
             "Content-Type: application/json", "-d",
             json.dumps({"name": name, "mono": mono}),
             "http://127.0.0.1:8789/api/device").stdout
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "raw": out[:200]}


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    make_noise(os.path.join(OUT, "noise.wav"))
    result: dict[str, Any] = {}

    print("=== 1) 立体声直连(参考) ===")
    print("面板:", set_device(ANALOG, False).get("message"))
    time.sleep(1.5)
    w1 = wiring()
    print("接线:", w1)
    l, r, path = capture("stereo")
    st = analyse(l, r)
    print("立体声:", {k: round(v, 6) for k, v in st.items()}, "->", path)
    result["stereo"], result["wiring_stereo"] = st, w1

    print("=== 2) 单声道混合(开) ===")
    print("面板:", set_device(ANALOG, True).get("message"))
    time.sleep(2.0)
    w2 = wiring()
    print("接线:", w2)
    l2, r2, path2 = capture("mono")
    mo = analyse(l2, r2)
    print("单声道:", {k: round(v, 6) for k, v in mo.items()}, "->", path2)
    result["mono"], result["wiring_mono"] = mo, w2

    print("=== 判定 ===")
    pred = st["rms_mid"]
    ratio = mo["rms_mid"] / pred if pred else 0.0
    print("FL/FR 相关: 立体声 %.4f → 单声道 %.6f" % (st["corr"], mo["corr"]))
    print("FL/FR 最大差: 立体声 %.4f → 单声道 %.6f" % (st["maxdiff"], mo["maxdiff"]))
    print("混合电平: 单声道 %.5f / 预测 0.5(L+R) %.5f = %.3f" % (mo["rms_mid"], pred, ratio))
    ok1 = mo["corr"] > 0.9999 and mo["maxdiff"] < 1e-4 and st["corr"] < 0.999
    ok2 = 0.85 < ratio < 1.15
    ok3 = (w2.get("eq_out_to") == ["collaplex_mono_in"]
           and w2.get("mono_out_to") == [ANALOG]
           and w1.get("eq_out_to") == [ANALOG])
    print("结构接线:", "通过" if ok3 else "不通过")
    print("两路同信号(单声道)且立体声两路不同:", "通过" if ok1 else "不通过")
    print("0.5+0.5 混合(不是只取一路):", "通过" if ok2 else "不通过")
    result["verdict"] = {"identical": ok1, "summed": ok2, "wiring": ok3, "ratio": ratio}
    with open(os.path.join(OUT, "结果.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    sys.exit(0 if (ok1 and ok2 and ok3) else 1)


if __name__ == "__main__":
    main()
