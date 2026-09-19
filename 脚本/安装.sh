#!/bin/bash
# Collaplex 音效 — 把链路固化装到 PipeWire(用户级)
#
#   链路: 全局音频(应用输出) -> collaplex_vsink(虚拟声卡) -> collaplex_hrtf_in(双耳 HRTF + 房间混响, 湿量 $WET) -> 数字输出
#
# 装两个 filter/loopback 实例(各自独立, 靠 node.target 串成一条链):
#   ~/.config/pipewire/pipewire.conf.d/10-collaplex-vsink.conf   虚拟声卡(纯转发)
#   ~/.config/pipewire/pipewire.conf.d/20-collaplex-hrtf.conf    HRTF + 混响(独立实例, 有接入/输出)
# 用法: bash 脚本/安装.sh        湿量可覆盖: WET=0.15 bash 脚本/安装.sh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$HOME/.config/pipewire/pipewire.conf.d"
WET="${WET:-0.3}"
HRIR="$ROOT/hrir"
REVERB="$ROOT/reverb/房间混响IR-96k.wav"
HRTF_IN="collaplex_hrtf_in"

echo "== Collaplex 音效 - 固化安装 =="
echo "项目: $ROOT"

[ -f "$REVERB" ] || { echo "!! 缺房间 IR: $REVERB"; exit 1; }
[ -f "$HRIR/FL_L.wav" ] || { echo "!! 缺 HRIR: $HRIR"; exit 1; }

# ---- 1. 探测数字输出(S/PDIF) ----
TARGET="$(pw-dump | /usr/bin/python3 -c '
import json,sys
best=""
for n in json.load(sys.stdin):
    if n.get("type")!="PipeWire:Interface:Node":
        continue
    p=(n.get("info") or {}).get("props") or {}
    nm=p.get("node.name") or ""
    if p.get("media.class")=="Audio/Sink" and ("iec958" in nm or "digital" in nm.lower()):
        best=nm
print(best)
')"
[ -n "$TARGET" ] || { echo "!! 找不到数字输出设备(pw-dump 里没有 iec958/digital sink)"; exit 1; }
echo "数字输出: $TARGET"
echo "HRTF 湿量: $WET"

# ---- 2. 渲染并安装两份配置 ----
mkdir -p "$CONF_DIR"
sed -e "s|__HRTF_IN__|$HRTF_IN|" \
    "$ROOT/pipewire/10-collaplex-vsink.conf" > "$CONF_DIR/10-collaplex-vsink.conf"
sed -e "s|__HRIR__|$HRIR|" -e "s|__REVERB__|$REVERB|" \
    -e "s|__TARGET__|$TARGET|" -e "s|__WET__|$WET|" \
    "$ROOT/pipewire/20-collaplex-hrtf.conf" > "$CONF_DIR/20-collaplex-hrtf.conf"
echo "已装配置:"
echo "  $CONF_DIR/10-collaplex-vsink.conf"
echo "  $CONF_DIR/20-collaplex-hrtf.conf"

# ---- 3. 重新加载 ----
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 7

# ---- 4. 自检 ----
echo "---- 自检 ----"
MISS=0
for n in collaplex_vsink collaplex_vsink_out "$HRTF_IN" collaplex_hrtf_out; do
    if pw-dump | grep -q "\"node.name\": \"$n\""; then
        echo "  ✓ $n"
    else
        echo "  ✗ $n 缺失"; MISS=1
    fi
done
DEF="$(pw-dump | /usr/bin/python3 -c '
import json,sys
for n in json.load(sys.stdin):
    if n.get("type")=="PipeWire:Interface:Metadata":
        continue
' 2>/dev/null; wpctl inspect @DEFAULT_SINK@ 2>/dev/null | grep -m1 "node.name" | cut -d= -f2 | tr -d " \"")"
echo "  默认输出: ${DEF:-未知}"

if [ "$MISS" = "0" ]; then
    echo "完成。桌面/浏览器的声音现在都会走: 虚拟声卡 -> HRTF -> 数字输出。"
    echo "单元测试: pw-play --target collaplex_hrtf_in 某个.wav   (只听 HRTF 这一级)"
else
    echo "!! 有节点没起来, 看: journalctl --user -u pipewire -n 50"
    exit 1
fi
