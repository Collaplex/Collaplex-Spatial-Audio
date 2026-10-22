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
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any

EQ_OUT = "collaplex_eq_out"                 # 链路末段节点(它的输出要挂到设备上)
CHAIN_PREFIX = "collaplex_"                 # 链路自己的节点, 不能当输出设备
SINK_PREFIX = "alsa_output."                # 真实硬件输出节点名前缀
STATE_DIR = os.path.expanduser("~/.local/state/cinema-spatial")
OUTPUT_JSON = os.path.join(STATE_DIR, "output.json")
POLL_SEC = 3.0                              # 守护核对间隔

_last_note = ""


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
        if not name.startswith(SINK_PREFIX) or name.startswith(CHAIN_PREFIX):
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


def saved() -> dict[str, str]:
    """读落盘的设备选择; 没有就返回空(调用方自行回落探测)。"""
    try:
        with open(OUTPUT_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"node_name": "", "description": ""}
    if not isinstance(data, dict):
        return {"node_name": "", "description": ""}
    return {
        "node_name": str(data.get("node_name", "")),
        "description": str(data.get("description", "")),
    }


def save(name: str, description: str) -> None:
    """落盘设备选择(原子写: 临时文件 + rename)。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = OUTPUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"node_name": name, "description": description}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, OUTPUT_JSON)


def routed() -> str:
    """当前实际挂在哪台硬件设备上(没挂上返回空)。"""
    nodes, _ports, links = _graph()
    hardware = {name for name in
                (str(p.get("node.name", "")) for p in nodes.values())
                if name.startswith(SINK_PREFIX)}
    for on, _op, dn, _dp in links:
        if on == EQ_OUT and dn in hardware:
            return dn
    return ""


def present(name: str) -> bool:
    """设备现在在不在位。"""
    nodes, _ports, _links = _graph()
    return any(str(p.get("node.name", "")) == name for p in nodes.values())


def apply_routing(target: str) -> tuple[bool, str]:
    """把链路末段挂到 target: 先拆掉连到别处的线, 再按声道补上。

    返回 (是否成功, 说明)。幂等: 已经挂对了就什么都不做。
    """
    if not target:
        return False, "没选输出设备"
    nodes, ports, links = _graph()
    names = {str(p.get("node.name", "")) for p in nodes.values()}
    if target not in names:
        return False, "设备不在位"
    if EQ_OUT not in names:
        return False, "链路没在跑"

    removed: list[str] = []
    for on, op, dn, dp in links:
        if on == EQ_OUT and dn != target:
            _run("pw-link", "-d", f"{on}:{op}", f"{dn}:{dp}")
            removed.append(dn)

    have = {(op, dp) for on, op, dn, dp in links if on == EQ_OUT and dn == target}
    outs = _port_by_channel(nodes, ports, EQ_OUT, "out")
    ins = _port_by_channel(nodes, ports, target, "in")
    added = 0
    for channel in ("FL", "FR"):
        op, ip = outs.get(channel), ins.get(channel)
        if op is None or ip is None or (op, ip) in have:
            continue
        _run("pw-link", f"{EQ_OUT}:{op}", f"{target}:{ip}")
        added += 1
    detail = []
    if removed:
        detail.append("拆掉连到 " + "/".join(sorted(set(removed))))
    if added:
        detail.append(f"补上 {added} 条声道连线")
    return True, ("已挂到 " + target + ("(" + ", ".join(detail) + ")" if detail else "(本来就挂对了)"))


def status() -> dict[str, Any]:
    """给面板用的一段状态: 选中设备 / 实际挂载 / 设备清单 / 守护备注。"""
    chosen = saved()
    now_routed = routed()
    return {
        "device": chosen["node_name"],
        "description": chosen["description"],
        "routed": now_routed,
        "present": present(chosen["node_name"]) if chosen["node_name"] else False,
        "devices": hardware_sinks(),
        "note": _last_note,
    }


def select(name: str, description: str = "") -> tuple[bool, str]:
    """面板按钮走过来: 落盘 + 立刻挂载一次。"""
    save(name, description)
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
            elif not present(target):
                _last_note = "等待设备: " + (chosen["description"] or target)
            elif routed() != target:
                ok, msg = apply_routing(target)
                _last_note = msg if ok else ("挂载失败: " + msg)
            else:
                _last_note = "已挂到 " + (chosen["description"] or target)
        except Exception as exc:                    # 守护线程绝不能死
            _last_note = "守护异常: %s" % exc
        time.sleep(POLL_SEC)


def start_watcher() -> threading.Thread:
    """起守护线程(daemon: 面板退出就跟着走)。"""
    thread = threading.Thread(target=_watch, name="collaplex-output-watch", daemon=True)
    thread.start()
    return thread
