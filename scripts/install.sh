#!/bin/bash
# Collaplex 音效 — 把链路固化装到 PipeWire(用户级)
#
#   链路: 全局音频(应用输出) -> collaplex_vsink(虚拟声卡)
#         -> 响度归一化(pipe DSP) -> collaplex_hrtf_in(双耳 HRTF + 房间混响) -> 数字输出
#
# 装什么:
#   $HOME/.local/bin/collaplex-loudness-norm            pipe 插件入口(指向本项目 dsp/)
#   $HOME/.config/pipewire/pipewire.conf.d/10-...conf   虚拟声卡 + 响度归一化
#   $HOME/.config/pipewire/pipewire.conf.d/20-...conf   HRTF 核心(接入 + 输出)
#
# 湿量: WET=0.3 环境变量覆盖(默认 0.3)
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$HOME/.config/pipewire/pipewire.conf.d"
WRAPPER="$HOME/.local/bin/collaplex-loudness-norm"
WET="${WET:-0.3}"
HRTF_IN="collaplex_hrtf_in"
EQ_IN="collaplex_eq_in"
# 解释器: 默认用项目 venv(自带 scipy、版本可控); deb 安装场景没有 venv, 退回系统 python3
PY="${PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=/usr/bin/python3

# ---- 1. 探测输出设备 ----
# 优先用**记住的选择**(面板按钮/上次 start 落盘的 output.json) —— 设备名跨重插是稳定的
# (含 USB 序列号), 所以"现在不在位"也照样先按它建链, 设备一插上由面板守护自动挂上。
# 没记忆过就照旧: 先找数字输出(iec958), 再退到任意硬件输出。
TARGET_INFO=$(/usr/bin/python3 - "$ROOT/web" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])

import output_route  # noqa: E402

chosen = output_route.saved()
sinks = output_route.hardware_sinks()
desc_of = {d["name"]: d["description"] for d in sinks}
target = chosen["node_name"]
if not target:
    for d in sinks:
        if "iec958" in d["name"] or "digital" in d["name"].lower():
            target = d["name"]
            break
    if not target and sinks:
        target = sinks[0]["name"]
if not target:
    print("\t\t")
    raise SystemExit
desc = desc_of.get(target, "")
output_route.save(target, desc)          # 落盘: 面板守护与下次 start 共用这一份选择
present = any(d["name"] == target and d["present"] for d in sinks)
print("%s\t%s\t%s" % (target, desc, "在位" if present else "不在位"))
PY
)
TARGET=$(printf '%s' "$TARGET_INFO" | cut -f1)
TARGET_DESC=$(printf '%s' "$TARGET_INFO" | cut -f2)
TARGET_STATE=$(printf '%s' "$TARGET_INFO" | cut -f3)
if [ -z "$TARGET" ]; then
    echo "找不到任何输出设备, 也没有记住的选择 —— 先把耳机/声卡接上再 start"
    exit 1
fi
echo "输出设备: $TARGET (${TARGET_DESC:-未知}) [$TARGET_STATE]"
if [ "$TARGET_STATE" != "在位" ]; then
    echo "  ★ 设备当前不在位: 链路先建好, 设备一出现由面板守护(output_route)自动挂上, 不用重启"
fi

# ---- 2. 部署 pipe 插件入口(必须 ASCII 路径) ----
mkdir -p "$(dirname "$WRAPPER")"
sed -e "s|__PY__|$PY|" -e "s|__DSP__|$ROOT/dsp/loudness_norm.py|" "$ROOT/bin/collaplex-loudness-norm" > "$WRAPPER"
sed -e "s|__PY__|$PY|" -e "s|__DSP__|$ROOT/dsp/eq.py|" \
    -e "s|__REVERB__|$ROOT/reverb/room-ir-96k.wav|" \
    "$ROOT/bin/collaplex-eq" > "$HOME/.local/bin/collaplex-eq"
chmod 755 "$WRAPPER" "$HOME/.local/bin/collaplex-eq"
echo "已装入口: $WRAPPER / $HOME/.local/bin/collaplex-eq"

# ---- 3. 渲染两份配置 ----
mkdir -p "$CONF_DIR"
sed -e "s|__WRAPPER__|$WRAPPER|" -e "s|__HRTF_IN__|$HRTF_IN|" \
    "$ROOT/pipewire/10-collaplex-vsink.conf" > "$CONF_DIR/10-collaplex-vsink.conf"
sed -e "s|__HRIR__|$ROOT/hrir|" -e "s|__REVERB__|$ROOT/reverb/room-ir-96k.wav|" \
    -e "s|__EQ_IN__|$EQ_IN|" -e "s|__WET__|$WET|" -e "s|__WET_WRAPPER__|$HOME/.local/bin/collaplex-wet|" \
    "$ROOT/pipewire/20-collaplex-hrtf.conf" > "$CONF_DIR/20-collaplex-hrtf.conf"
sed -e "s|__PY__|$PY|" -e "s|__DSP__|$ROOT/dsp/wet_gain.py|" \
    "$ROOT/bin/collaplex-wet" > "$HOME/.local/bin/collaplex-wet"
chmod 755 "$HOME/.local/bin/collaplex-wet"
sed -e "s|__EQ_WRAPPER__|$HOME/.local/bin/collaplex-eq|" -e "s|__WET_WRAPPER__|$HOME/.local/bin/collaplex-wet|" -e "s|__TARGET__|$TARGET|" \
    "$ROOT/pipewire/30-collaplex-eq.conf" > "$CONF_DIR/30-collaplex-eq.conf"
cp "$ROOT/pipewire/91-collaplex-clock.conf" "$CONF_DIR/91-clock.conf"
# 共享内存不存在时写入默认参数(全平直 / 0 dB / 湿量 0.3); 已存在则保留用户当前设置
if [ ! -f /dev/shm/collaplex-eq ]; then
    "$PY" "$ROOT/dsp/bootstrap.py"
fi
echo "已装配置: 10-collaplex-vsink.conf / 20-collaplex-hrtf.conf(混响湿量 $WET) / 30-collaplex-eq.conf"

# ---- 4. 重启前清掉残留 DSP(占住共享槽位会让新实例起不来) ----
for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in
        *python*dsp/loudness_norm.py*) kill -9 "${p#/proc/}" 2>/dev/null || true;;
        *python*dsp/eq.py*) kill -9 "${p#/proc/}" 2>/dev/null || true;;
        *python*dsp/wet_gain.py*) kill -9 "${p#/proc/}" 2>/dev/null || true;;
    esac
done
sleep 1

# ---- 5. 重载 ----
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 8

# ---- 6. 自检 ----
echo "=== 自检 ==="
echo -n "节点: "; pw-dump | grep -o '"node.name": "collaplex[^"]*"' | sort -u | tr '\n' ' '; echo
n=0
for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in "/usr/bin/python3 "*dsp/loudness_norm.py*) n=$((n+1));; esac
done
echo "DSP 进程数: $n (归一化, 应为 2)"
m=0
for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in *python*dsp/eq.py*) m=$((m+1));; esac
done
echo "EQ 进程数: $m (应为 2)"
w=0
for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in *python*dsp/wet_gain.py*) w=$((w+1));; esac
done
echo "湿量进程数: $w (应为 2)"
echo -n "默认输出: "; wpctl inspect @DEFAULT_SINK@ 2>/dev/null | grep -m1 "node.name" | sed 's/.*= //'

# ---- 5. 面板自启(systemd --user, 登录即起) ----
# 链路本身不需要服务: conf 在 ~/.config/pipewire/pipewire.conf.d/, PipeWire 起来就自动加载。
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed -e "s|__ROOT__|$ROOT|g" -e "s|__PY__|$PY|g" -e "s|__PORT__|8789|g" \
    "$ROOT/systemd/collaplex-audio-panel.service" > "$UNIT_DIR/collaplex-audio-panel.service"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
    if systemctl --user enable --now collaplex-audio-panel.service 2>/dev/null; then
        echo "面板服务: 已启用(登录自启) http://127.0.0.1:8789"
    else
        echo "面板服务: 未能启用(可手动 collaplex-audio web)"
    fi
fi
