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

# ---- 1. 探测数字输出 ----
TARGET=$(pw-dump | /usr/bin/python3 -c '
import json, sys
for n in json.load(sys.stdin):
    if n.get("type") != "PipeWire:Interface:Node":
        continue
    p = (n.get("info") or {}).get("props") or {}
    if p.get("media.class") == "Audio/Sink":
        nm = p.get("node.name") or ""
        if "iec958" in nm or "digital" in nm.lower():
            print(nm)
            break
')
[ -n "$TARGET" ] || { echo "找不到数字输出设备(iec958)"; exit 1; }
echo "数字输出: $TARGET"

# ---- 2. 部署 pipe 插件入口(必须 ASCII 路径) ----
mkdir -p "$(dirname "$WRAPPER")"
sed -e "s|__DSP__|$ROOT/dsp/loudness_norm.py|" "$ROOT/bin/collaplex-loudness-norm" > "$WRAPPER"
sed -e "s|__PY__|$ROOT/.venv/bin/python|" -e "s|__DSP__|$ROOT/dsp/eq.py|" \
    "$ROOT/bin/collaplex-eq" > "$HOME/.local/bin/collaplex-eq"
chmod 755 "$WRAPPER" "$HOME/.local/bin/collaplex-eq"
echo "已装入口: $WRAPPER / $HOME/.local/bin/collaplex-eq"

# ---- 3. 渲染两份配置 ----
mkdir -p "$CONF_DIR"
sed -e "s|__WRAPPER__|$WRAPPER|" -e "s|__HRTF_IN__|$HRTF_IN|" \
    "$ROOT/pipewire/10-collaplex-vsink.conf" > "$CONF_DIR/10-collaplex-vsink.conf"
sed -e "s|__HRIR__|$ROOT/hrir|" -e "s|__REVERB__|$ROOT/reverb/房间混响IR-96k.wav|" \
    -e "s|__EQ_IN__|$EQ_IN|" -e "s|__WET__|$WET|" \
    "$ROOT/pipewire/20-collaplex-hrtf.conf" > "$CONF_DIR/20-collaplex-hrtf.conf"
sed -e "s|__EQ_WRAPPER__|$HOME/.local/bin/collaplex-eq|" -e "s|__TARGET__|$TARGET|" \
    "$ROOT/pipewire/30-collaplex-eq.conf" > "$CONF_DIR/30-collaplex-eq.conf"
echo "已装配置: 10-collaplex-vsink.conf / 20-collaplex-hrtf.conf(混响湿量 $WET) / 30-collaplex-eq.conf"

# ---- 4. 重启前清掉残留 DSP(占住共享槽位会让新实例起不来) ----
for p in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in
        "/usr/bin/python3 "*dsp/loudness_norm.py*) kill -9 "${p#/proc/}" 2>/dev/null || true;;
        *dsp/eq.py*) kill -9 "${p#/proc/}" 2>/dev/null || true;;
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
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in "/usr/bin/python3 "*dsp/loudness_norm.py*) n=$((n+1));; esac
done
echo "DSP 进程数: $n (归一化, 应为 2)"
m=0
for p in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in */dsp/eq.py*) m=$((m+1));; esac
done
echo "EQ 进程数: $m (应为 2)"
echo -n "默认输出: "; wpctl inspect @DEFAULT_SINK@ 2>/dev/null | grep -m1 "node.name" | sed 's/.*= //'
