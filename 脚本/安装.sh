#!/bin/bash
# Collaplex 音效 — 把链路固化装到 PipeWire(用户级)
#
#   链路: 全局音频(应用输出) -> collaplex_vsink(虚拟声卡) -> 响度归一化(pipe DSP)
#          -> collaplex_hrtf_in(HRTF 双耳虚拟环绕 + 房间混响, 湿量 $WET) -> 数字输出(S/PDIF)
#
# 装什么:
#   $HOME/.config/pipewire/pipewire.conf.d/10-collaplex-vsink.conf   虚拟声卡 + 响度归一化
#   $HOME/.config/pipewire/pipewire.conf.d/20-collaplex-hrtf.conf    HRTF + 房间混响
#   $HOME/.local/bin/collaplex-loudness-norm                         pipe 插件入口(ASCII 路径)
#
# 设备名换了 / 项目挪了位置 / 想改湿量 -> 重跑本脚本(可变部分由脚本渲染, 定义在项目里)
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$HOME/.config/pipewire/pipewire.conf.d"
WRAP_DST="$HOME/.local/bin/collaplex-loudness-norm"
PY=/usr/bin/python3
WET="${WET:-0.3}"          # 混响湿量(湿度"3" = 0.3); export WET=0.5 可覆盖
HRTF_IN="collaplex_hrtf_in"

echo "项目根  : $ROOT"
echo "混响湿量: $WET"

# ---- 1. 探测数字输出设备(跳过 HDMI/显卡) ----
TARGET="$("$PY" - <<'EOF'
import json, subprocess
d = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
sinks = []
for n in d:
    if n.get("type") != "PipeWire:Interface:Node":
        continue
    p = n.get("info", {}).get("props", {}) or {}
    if p.get("media.class") != "Audio/Sink":
        continue
    nm = p.get("node.name") or ""
    if "hdmi" in nm.lower() or "gb206" in nm.lower() or "radeon" in nm.lower():
        continue
    sinks.append(nm)
dig = [s for s in sinks if "iec958" in s or "spdif" in s.lower()]
print(dig[0] if dig else "")
EOF
)"
if [ -z "$TARGET" ]; then
    echo "✗ 没找到数字输出(S/PDIF)设备。当前可用 sink:"
    wpctl status | sed -n '/Sinks:/,/Sources:/p'
    exit 1
fi
echo "数字输出: $TARGET"

# ---- 2. 渲染两级链路定义 ----
mkdir -p "$CONF_DIR" "$(dirname "$WRAP_DST")"
sed -e "s|__HRTF_IN__|$HRTF_IN|" -e "s|__WRAPPER__|$WRAP_DST|" \
    "$ROOT/pipewire/10-collaplex-vsink.conf" > "$CONF_DIR/10-collaplex-vsink.conf"
echo "已装配置: $CONF_DIR/10-collaplex-vsink.conf"
sed -e "s|__HRIR__|$ROOT/hrir|" -e "s|__REVERB__|$ROOT/reverb/房间混响IR-96k.wav|" \
    -e "s|__TARGET__|$TARGET|" -e "s|__WET__|$WET|" \
    "$ROOT/pipewire/20-collaplex-hrtf.conf" > "$CONF_DIR/20-collaplex-hrtf.conf"
echo "已装配置: $CONF_DIR/20-collaplex-hrtf.conf"

# ---- 3. 渲染并安装 pipe 插件入口(指向项目内的 DSP) ----
sed -e "s|__DSP__|$ROOT/dsp/loudness_norm.py|" \
    "$ROOT/bin/collaplex-loudness-norm" > "$WRAP_DST"
chmod 755 "$WRAP_DST"
echo "已装入口: $WRAP_DST  ->  $ROOT/dsp/loudness_norm.py"

# ---- 4. 让 PipeWire 重新加载 ----
# 重启前先收掉残留的 DSP: 空闲退出的旧实例会占住 /dev/shm 的槽位, 新的就起不来(静音)。
for p in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in "/usr/bin/python3 "*dsp/loudness_norm.py*) kill "${p#/proc/}" 2>/dev/null || true;; esac
done
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 7

# ---- 5. 自检 ----
echo
echo "=== 自检 ==="
"$PY" - "$HRTF_IN" <<'EOF'
import json, subprocess, sys
want = ["collaplex_vsink", "collaplex_vsink_out", "collaplex_hrtf_in", "collaplex_hrtf_out"]
d = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
have = {}
for n in d:
    if n.get("type") == "PipeWire:Interface:Node":
        nm = (n.get("info", {}).get("props", {}) or {}).get("node.name")
        if nm:
            have[nm] = n["id"]
for w in want:
    print("  %s %s%s" % ("✓" if w in have else "✗", w, (" (id %d)" % have[w]) if w in have else ""))
EOF
DP=$(for p in /proc/[0-9]*; do c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue; case "$c" in "/usr/bin/python3 "*dsp/loudness_norm.py*) echo "$p";; esac; done | wc -l)
[ "$DP" = "2" ] && echo "  ✓ 响度归一化 DSP 进程 2 个(左右各一)" || echo "  ✗ DSP 进程数=$DP(应为 2)"

ID="$("$PY" - <<'EOF'
import json, subprocess
d = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
for x in d:
    if x.get("type") == "PipeWire:Interface:Node":
        if (x.get("info", {}).get("props", {}) or {}).get("node.name") == "collaplex_vsink":
            print(x["id"])
EOF
)"
[ -n "$ID" ] && { wpctl set-default "$ID" >/dev/null 2>&1 || true; echo "  ✓ 默认输出 = collaplex_vsink(id $ID)"; }

echo
echo "实时状态:   python3 \"$ROOT/dsp/状态.py\""
echo "改湿量:     WET=0.5 bash 脚本/安装.sh     (重建后生效)"
echo "改目标电平: echo '{\"on\": true, \"target_db\": -16}' > ~/.local/state/cinema-spatial/loudness.json"
