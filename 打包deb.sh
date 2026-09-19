#!/bin/bash
# 把 collaplex-audio 打成 deb 包(纯脚本 + 数据, Architecture: all)
#
#   bash 打包deb.sh              默认版本 1.0.0
#   VER=1.0.1 bash 打包deb.sh    指定版本
#
# 产物: 发布/collaplex-audio_<版本>_all.deb
#
# 说明: 装到 /usr/share/collaplex-audio, 只提供 /usr/bin/collaplex-audio 一个入口。
#       不带 venv(依赖系统 python3-numpy / python3-scipy), 不装到 pipewire 配置目录 ——
#       也就是说**装完不会自动启用**, 要手动 collaplex-audio start, 也不做开机自启。
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
VER="${VER:-1.0.0}"
OUT="$ROOT/发布"
PKG="collaplex-audio_${VER}_all"
STAGE="$OUT/$PKG"

echo "=== 打包 collaplex-audio $VER ==="
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/share/collaplex-audio" "$STAGE/usr/bin"

# ---- 源码 ----
for d in dsp bin pipewire web hrir reverb 脚本; do
    cp -a "$ROOT/$d" "$STAGE/usr/share/collaplex-audio/"
done
cp "$ROOT/README.md" "$STAGE/usr/share/collaplex-audio/"
find "$STAGE/usr/share" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
chmod -R a+rX "$STAGE/usr/share"

# ---- 主命令 ----
install -m 755 "$ROOT/bin/collaplex-audio" "$STAGE/usr/bin/collaplex-audio"

# ---- control ----
SIZE=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: collaplex-audio
Version: ${VER}
Architecture: all
Maintainer: Collaplex <collaplex@localhost>
Installed-Size: ${SIZE}
Depends: python3 (>= 3.10), python3-numpy, python3-scipy, pipewire, wireplumber
Recommends: google-chrome | chromium
Section: sound
Priority: optional
Description: Collaplex 音效 — 全局音频的响度归一化 + 双耳 HRTF + 32 段 EQ + 房间混响
 把系统输出接成一条链: 虚拟声卡 -> 响度归一化 -> 双耳 HRTF(含房间混响) -> 32 段 EQ + 总音量
 -> 数字输出。自带本地控制面板(双声道电平表 / 归一化电平与动态增益 / 湿度与总音量滑块 /
 32 段 EQ + 10 个预设)。
 .
 HRTF 用 SADIE II 真人数据集(约克大学, 受试者 H4, 96 kHz / 512 tap)。
 .
 装完不会动你的系统音频: 手动跑 collaplex-audio start 才接管, stop 即还原。
EOF

# ---- 构建 ----
dpkg-deb --root-owner-group --build "$STAGE" "$OUT/${PKG}.deb" >/dev/null
echo "已生成: $OUT/${PKG}.deb"
dpkg-deb --info "$OUT/${PKG}.deb" | sed -n '1,12p'
echo
echo -n "大小: "; du -h "$OUT/${PKG}.deb" | cut -f1
