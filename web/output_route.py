# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Collaplex
"""输出设备路由 + 自动挂载守护(纯标准库, 只依赖 pw-dump / pw-link)。

为什么需要它
------------
链路末段原来把输出设备名**写死在 30-collaplex-eq.conf** 里:

    node.target = "alsa_output.usb-EDIFIER_...iec958-stereo"

开机时那台设备要是还没枚举出来(USB 耳机晚到很常见: 2026-09-24 实测开机 08:57、
耳机 09:19:43 才出现), 会话管理器建链时目标不存在, 末段就连到别处或者干脆不连;
等设备出现了也**不会**自己改回来 —— 听感上就是"双重混响 / 声音不对", 只能
`collaplex-audio stop` + `start` 才好。

本模块把这件事拆成两半:

  1. **设备选择落盘**: `~/.local/state/cinema-spatial/output.json` 记住用户选了哪台
     设备(面板按钮改它), 下次 start 优先用它, 不再猜"第一台数字输出"。
  2. **自动挂载守护**: 后台线程每 3 秒核对一次"选中的设备在位 且 collaplex_eq_out
     **只**连到它"; 不对就拆掉错的、补上对的。设备一出现就自动挂上, 不用再手动重启。

守护只做"把末段挂到用户选的设备"这一件事, 不碰 DSP 参数、不重建链路。

单声道混合(模拟/单声道设备)
---------------------------
模拟口上挂的常常是只有一路的设备(3.5 口接单声道蓝牙音响、老功放、单只喇叭):
立体声送过去只响一边。给这台设备勾「单声道混合」后, 链路末段变成

    eq_out -> collaplex_mono_in -> 单声道混合 -> collaplex_mono_out -> 设备

混合级是 40-collaplex-mono.conf(内置 mixer 0.5+0.5, 不加 DSP 进程)。
"末段是哪个节点"由**部署时**决定(install.sh 按落盘的模式渲染): 部署了 mono 级末段
就是 mono_out, 没部署就是 eq_out。本模块只认节点在位与否, 不去猜模式 ——
模式变了要重建链路(重跑 install.sh, 约 10 秒), 否则会和 PipeWire 的自动连线打架。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any, TypedDict, cast

EQ_OUT = "collaplex_eq_out"                 # 立体声链路的末段节点
MONO_IN = "collaplex_mono_in"               # 单声道混合级: 接入
MONO_OUT = "collaplex_mono_out"             # 单声道混合级: 输出(单声道设备时它就是末段)
CHAIN_PREFIX = "collaplex_"                 # 链路自己的节点, 不能当输出设备
SINK_PREFIXES: tuple[str, ...] = ("alsa_output.", "bluez_output.")   # 真实硬件输出(声卡 / 蓝牙直连)
STATE_DIR = os.path.expanduser("~/.local/state/cinema-spatial")
OUTPUT_JSON = os.path.join(STATE_DIR, "output.json")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLL_SEC = 3.0                              # 守护核对间隔

_last_note = ""


class Choice(TypedDict):
    """落盘的设备选择(mono 是"这台设备要不要单声道混合"的记忆)。"""

    node_name: str
    description: str
    mono: bool


def _is_sink(name: str) -> bool:
    """真实硬件的输出节点名(声卡 / 蓝牙直连), 排除链路自己的虚拟节点。"""
    return name.startswith(SINK_PREFIXES) and not name.startswith(CHAIN_PREFIX)


def _sink_key(name: str) -> str:
    """设备稳定键: 蓝牙节点尾部 `.<序号>` 会随重连变化, 认设备/记模式都按去掉序号的键。"""
    if name.startswith("bluez_output."):
        head, _, tail = name.rpartition(".")
        if tail.isdigit():
            return head
    return name


def _resolve(name: str, names: set[str]) -> str:
    """把"记住的设备名"落到图里真实存在的节点名(蓝牙重连换了序号也认得出); 找不到返回空。"""
    if name in names:
        return name
    key = _sink_key(name)
    for cand in sorted(names):
        if _is_sink(cand) and _sink_key(cand) == key:
            return cand
    return ""


def _sink_names(nodes: dict[int, dict[str, Any]]) -> set[str]:
    """图里所有真实输出节点名(Audio/Sink 且不是链路自己的)。"""
    out: set[str] = set()
    for props in nodes.values():
        name = str(props.get("node.name", ""))
        if str(props.get("media.class", "")) == "Audio/Sink" and _is_sink(name):
            out.add(name)
    return out


def _run(*args: str) -> str:
    """跑一条命令, 返回 stdout(失败返回空串, 不抛)。"""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout


def _dump() -> list[dict[str, Any]]:
    """pw-dump 的对象列表(失败返回空表)。"""
    raw = _run("pw-dump")
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _node_props(objs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """节点 id -> props。"""
    out: dict[int, dict[str, Any]] = {}
    for obj in objs:
        if obj.get("type") == "PipeWire:Interface:Node":
            info = obj.get("info")
            props = (info or {}).get("props") or {}
            if isinstance(props, dict):
                out[int(obj["id"])] = props
    return out


def _port_map(objs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """端口 id -> props。"""
    out: dict[int, dict[str, Any]] = {}
    for obj in objs:
        if obj.get("type") == "PipeWire:Interface:Port":
            info = obj.get("info")
            props = (info or {}).get("props") or {}
            if isinstance(props, dict):
                out[int(obj["id"])] = props
    return out


def _link_pairs(objs: list[dict[str, Any]],
                nodes: dict[int, dict[str, Any]],
                ports: dict[int, dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """所有连线 -> (源节点名, 源端口名, 目标节点名, 目标端口名)。"""
    def name_of(port_id: int) -> tuple[str, str]:
        props = ports.get(port_id, {})
        nid = int(str(props.get("node.id", -1)))
        nname = str(nodes.get(nid, {}).get("node.name", ""))
        return nname, str(props.get("port.name", ""))

    out: list[tuple[str, str, str, str]] = []
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info") or {}
        # ★ 键名是 output-port-id / input-port-id(不是 output-port), 写错就一条连线都拿不到
        on, op = name_of(int(info.get("output-port-id", -1)))
        dn, dp = name_of(int(info.get("input-port-id", -1)))
        if on and dn:
            out.append((on, op, dn, dp))
    return out


def _graph() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]],
                      list[tuple[str, str, str, str]]]:
    """一次拿到(节点表, 端口表, 连线表)。"""
    objs = _dump()
    nodes = _node_props(objs)
    ports = _port_map(objs)
    return nodes, ports, _link_pairs(objs, nodes, ports)


def _port_by_channel(nodes: dict[int, dict[str, Any]],
                     ports: dict[int, dict[str, Any]],
                     node_name: str, direction: str) -> dict[str, str]:
    """某节点的端口按声道取名: {"FL": "output_FL", "FR": "output_FR"}。"""
    wanted_ids = {nid for nid, props in nodes.items()
                  if str(props.get("node.name", "")) == node_name}
    out: dict[str, str] = {}
    for props in ports.values():
        if int(str(props.get("node.id", -1))) not in wanted_ids:
            continue
        if str(props.get("port.direction", "")) != direction:
            continue
        channel = str(props.get("audio.channel", ""))
        pname = str(props.get("port.name", ""))
        if channel in ("FL", "FR"):
            out[channel] = pname
        elif pname.endswith("_FL"):
            out["FL"] = pname
        elif pname.endswith("_FR"):
            out["FR"] = pname
    return out


def hardware_sinks() -> list[dict[str, Any]]:
    """真实硬件输出设备清单(排除链路自己的虚拟节点)。

    选中的设备即使不在位也带一条(present=False), 这样面板里不会"选项消失"。
    """
    nodes, _ports, _links = _graph()
    found: list[dict[str, Any]] = []
    for props in nodes.values():
        if str(props.get("media.class", "")) != "Audio/Sink":
            continue
        name = str(props.get("node.name", ""))
        if not _is_sink(name):
            continue
        found.append({
            "name": name,
            "description": str(props.get("node.description", name)),
            "present": True,
        })
    found.sort(key=lambda d: str(d["name"]))
    chosen = saved()
    if chosen["node_name"] and all(d["name"] != chosen["node_name"] for d in found):
        found.append({
            "name": chosen["node_name"],
            "description": chosen["description"] or chosen["node_name"],
            "present": False,
        })
    return found


def _mono_map() -> dict[str, bool]:
    """读落盘的"哪台设备要单声道混合"(坏文件当空表)。"""
    try:
        with open(OUTPUT_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("mono")
    if not isinstance(raw, dict):
        return {}
    return {str(k): bool(v) for k, v in cast(dict[str, Any], raw).items()}


def saved() -> Choice:
    """读落盘的设备选择; 没有就返回空(调用方自行回落探测)。"""
    empty: Choice = {"node_name": "", "description": "", "mono": False}
    try:
        with open(OUTPUT_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    name = str(data.get("node_name", ""))
    return {
        "node_name": name,
        "description": str(data.get("description", "")),
        "mono": mono_pref(name),
    }


def mono_pref(name: str) -> bool:
    """这台设备记住的模式: True = 单声道混合(模拟/单声道设备)。"""
    if not name:
        return False
    modes = _mono_map()
    return modes.get(_sink_key(name), modes.get(name, False))


def save(name: str, description: str, mono: bool | None = None) -> None:
    """落盘设备选择(原子写: 临时文件 + rename)。

    mono=None 表示"只改设备, 模式沿用这台设备原来的记忆" —— 装脚本探测设备时就走这条,
    免得把用户勾好的单声道设置冲掉。
    """
    modes = _mono_map()
    if mono is not None and name:
        modes[_sink_key(name)] = mono
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = OUTPUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"node_name": name, "description": description, "mono": modes}, fh,
                  ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, OUTPUT_JSON)


def _end_node(names: set[str]) -> str:
    """链路末段节点: 部署了单声道混合级就是 mono_out, 否则 eq_out。"""
    return MONO_OUT if MONO_OUT in names else EQ_OUT


def mono_active() -> bool:
    """链路里有没有单声道混合级(= 当前部署是不是单声道模式)。"""
    nodes, _ports, _links = _graph()
    names = {str(p.get("node.name", "")) for p in nodes.values()}
    return _end_node(names) == MONO_OUT


def routed() -> str:
    """当前实际挂在哪台硬件设备上(没挂上返回空)。"""
    nodes, _ports, links = _graph()
    hardware = _sink_names(nodes)
    end = _end_node({str(p.get("node.name", "")) for p in nodes.values()})
    for on, _op, dn, _dp in links:
        if on == end and dn in hardware:
            return dn
    return ""


def present(name: str) -> bool:
    """设备现在在不在位(蓝牙重连换了节点序号也算在位)。"""
    nodes, _ports, _links = _graph()
    return bool(_resolve(name, {str(p.get("node.name", "")) for p in nodes.values()}))


def _first_input(nodes: dict[int, dict[str, Any]],
                 ports: dict[int, dict[str, Any]],
                 node_name: str) -> dict[str, str]:
    """真单声道设备只有一个输入口(声道名可能是 MONO) —— 拿第一个口当 FL 用。"""
    wanted_ids = {nid for nid, props in nodes.items()
                  if str(props.get("node.name", "")) == node_name}
    names: list[str] = []
    for props in ports.values():
        if int(str(props.get("node.id", -1))) not in wanted_ids:
            continue
        if str(props.get("port.direction", "")) != "in":
            continue
        names.append(str(props.get("port.name", "")))
    return {"FL": sorted(names)[0]} if names else {}


def _link_channels(nodes: dict[int, dict[str, Any]],
                   ports: dict[int, dict[str, Any]],
                   links: list[tuple[str, str, str, str]],
                   src: str, dst: str) -> int:
    """把 src 的 FL/FR 按声道接到 dst(dst 缺哪个就不接); 返回补了几条。"""
    outs = _port_by_channel(nodes, ports, src, "out")
    ins = _port_by_channel(nodes, ports, dst, "in")
    if not ins:
        ins = _first_input(nodes, ports, dst)
    have = {(op, dp) for on, op, dn, dp in links if on == src and dn == dst}
    added = 0
    for channel in ("FL", "FR"):
        op, ip = outs.get(channel), ins.get(channel)
        if op is None or ip is None or (op, ip) in have:
            continue
        _run("pw-link", f"{src}:{op}", f"{dst}:{ip}")
        added += 1
    return added


def apply_routing(target: str) -> tuple[bool, str]:
    """把链路末段挂到 target: 先拆掉连到别处的线, 再按声道补上。

    返回 (是否成功, 说明)。幂等: 已经挂对了就什么都不做。
    """
    if not target:
        return False, "没选输出设备"
    nodes, ports, links = _graph()
    names = {str(p.get("node.name", "")) for p in nodes.values()}
    if not _is_sink(target):
        return False, "不是硬件输出设备: " + target
    real = _resolve(target, names)
    if not real:
        return False, "设备不在位"
    target = real
    end = _end_node(names)
    if end not in names:
        return False, "链路没在跑"

    removed: list[str] = []
    for on, op, dn, dp in links:
        if on == end and dn != target:
            _run("pw-link", "-d", f"{on}:{op}", f"{dn}:{dp}")
            removed.append(dn)

    added = _link_channels(nodes, ports, links, end, target)
    if end == MONO_OUT and MONO_IN in names:
        added += _link_channels(nodes, ports, links, EQ_OUT, MONO_IN)
    detail = []
    if removed:
        detail.append("拆掉连到 " + "/".join(sorted(set(removed))))
    if added:
        detail.append(f"补上 {added} 条声道连线")
    where = "已挂到 " + target
    if end == MONO_OUT:
        where += "(经单声道混合)"
    return True, (where + ("(" + ", ".join(detail) + ")" if detail else "(本来就挂对了)"))


def redeploy() -> tuple[bool, str]:
    """按落盘的设备/模式重建链路 —— 重跑安装脚本(含重启 PipeWire, 约 10 秒)。

    只有"模式变了"(立体声 <-> 单声道混合)才需要它: 末段是哪个节点是部署期就定下的,
    运行期改不了, 硬拆硬接会和 PipeWire 的自动连线打架。
    """
    script = os.path.join(ROOT, "scripts", "install.sh")
    if not os.path.exists(script):
        return False, "找不到安装脚本: " + script
    try:
        done = subprocess.run(["bash", script], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "重建失败: %s" % exc
    if done.returncode != 0:
        lines = [ln for ln in (done.stderr or done.stdout).strip().splitlines() if ln.strip()]
        return False, "重建失败: " + (lines[-1] if lines else "退出码 %d" % done.returncode)
    return True, "链路已重建"


def status() -> dict[str, Any]:
    """给面板用的一段状态: 选中设备 / 实际挂载 / 设备清单 / 模式 / 守护备注。"""
    chosen = saved()
    now_routed = routed()
    active = mono_active()
    return {
        "device": chosen["node_name"],
        "description": chosen["description"],
        "routed": now_routed,
        "present": present(chosen["node_name"]) if chosen["node_name"] else False,
        "devices": hardware_sinks(),
        "mono": chosen["mono"],                 # 这台设备记住的模式(用户意图)
        "mono_active": active,                  # 链路当前实际是不是单声道混合
        "note": _last_note,
    }


def select(name: str, description: str = "", mono: bool | None = None) -> tuple[bool, str]:
    """面板按钮走过来: 落盘 + 立刻挂载一次(mono 只改记忆, 生效要重建链路)。"""
    save(name, description, mono)
    ok, msg = apply_routing(name)
    return ok, msg


def _watch() -> None:
    """守护循环: 设备在位就确保挂载正确, 不在位就等着(设备一出现立刻挂上)。"""
    global _last_note
    while True:
        try:
            chosen = saved()
            target = chosen["node_name"]
            if not target:
                _last_note = "没选输出设备"
                time.sleep(POLL_SEC)
                continue
            nodes, _ports, _links = _graph()
            real = _resolve(target, {str(p.get("node.name", "")) for p in nodes.values()})
            if not real:
                _last_note = "等待设备: " + (chosen["description"] or target)
            elif routed() != real:
                ok, msg = apply_routing(real)
                _last_note = msg if ok else ("挂载失败: " + msg)
            else:
                _last_note = "已挂到 " + (chosen["description"] or target)
                if chosen["mono"] != mono_active():
                    _last_note += "(模式改了: 点「应用」重建链路才生效)"
        except Exception as exc:                    # 守护线程绝不能死
            _last_note = "守护异常: %s" % exc
        time.sleep(POLL_SEC)


def start_watcher() -> threading.Thread:
    """起守护线程(daemon: 面板退出就跟着走)。"""
    thread = threading.Thread(target=_watch, name="collaplex-output-watch", daemon=True)
    thread.start()
    return thread
