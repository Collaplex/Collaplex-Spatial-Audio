#!/bin/bash
# Collaplex 音效 — 把当前链路固化装到 PipeWire(用户级)
#
#   链路: 全局音频(应用输出) -> collaplex_vsink(虚拟声卡) -> 响度归一化(pipe DSP) -> 数字输出
#
# 装什么:
#   $HOME/.config/pipewire/pipewire.conf.d/10-collaplex-vsink.conf   链路定义(由本脚本渲染)
#   $HOME/.local/bin/collaplex-loudness-norm                         pipe 插件入口(ASCII 路径)
# 设备名换了 / 项目挪了位置 -> 重跑本脚本即可(这就是"固化"的意义: 定义在项目里, 部署可复现)
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DST="$HOME/.config/pipewire/pipewire.conf.d/10-collaplex-vsink.conf"
WRAP_DST="$HOME/.local/bin/collaplex-loudness-norm"
PY=/usr/bin/python3

echo "项目根: $ROOT"

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
        continue          # 显卡 HDMI, 不是我们要的数字输出
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

# ---- 2. 渲染并安装链路定义 ----
mkdir -p "$(dirname "$CONF_DST")" "$(dirname "$WRAP_DST")"
sed -e "s|__TARGET__|$TARGET|" -e "s|__WRAPPER__|$WRAP_DST|" \
    "$ROOT/pipewire/10-collaplex-vsink.conf" > "$CONF_DST"
echo "已装配置: $CONF_DST"

# ---- 3. 渲染并安装 pipe 插件入口(指向项目内的 DSP) ----
sed -e "s|__DSP__|$ROOT/dsp/loudness_norm.py|" \
    "$ROOT/bin/collaplex-loudness-norm" > "$WRAP_DST"
chmod 755 "$WRAP_DST"
echo "已装入口: $WRAP_DST  ->  $ROOT/dsp/loudness_norm.py"

# ---- 4. 让 PipeWire 重新加载 ----
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 6

# ---- 5. 自检 ----
echo
echo "=== 自检 ==="
N=$("$PY" - <<'EOF'
import json, subprocess
d = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
n = [x for x in d if x.get("type") == "PipeWire:Interface:Node"
     and (x.get("info", {}).get("props", {}) or {}).get("node.name") == "collaplex_vsink"]
print(len(n))
EOF
)
[ "$N" = "1" ] && echo "  ✓ 虚拟声卡节点 collaplex_vsink 在" || echo "  ✗ 虚拟声卡节点缺失(个数=$N)"

DP=$(for p in /proc/[0-9]*; do c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue; case "$c" in *dsp/loudness_norm.py*) echo "$p";; esac; done | wc -l)
[ "$DP" = "2" ] && echo "  ✓ 响度归一化 DSP 进程 2 个(左右各一)" || echo "  ✗ DSP 进程数=$DP(应为 2)"

ID=$("$PY" - <<'EOF'
import json, subprocess
d = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
for x in d:
    if x.get("type") == "PipeWire:Interface:Node":
        if (x.get("info", {}).get("props", {}) or {}).get("node.name") == "collaplex_vsink":
            print(x["id"])
EOF
)
if [ -n "$ID" ]; then
    wpctl set-default "$ID" >/dev/null 2>&1 || true
    echo "  ✓ 默认输出已设为 collaplex_vsink(id $ID)"
fi

echo
echo "开关/目标:  python3 \"$ROOT/dsp/状态.py\"          # 看实时推子"
echo "改目标电平: echo '{\"on\": true, \"target_db\": -16}' > ~/.local/state/cinema-spatial/loudness.json"
