# Collaplex 音效

耳机方向的空间音效链路（PipeWire）。

## 链路

```
全局音频(应用输出) → collaplex_vsink(虚拟声卡) → 响度归一化(pipe DSP)
                   → collaplex_hrtf_in(双耳 HRTF + 房间混响, 湿量 0.3)
                   → EQ(三段) + 总音量放大(+6 dB)
                   → 数字输出(S/PDIF)
```

## 安装

```sh
bash 脚本/安装.sh                # 默认: 混响湿量 0.3, 总音量 +6 dB
VOL_DB=9 bash 脚本/安装.sh       # 总音量改成 +9 dB
WET=0.4 bash 脚本/安装.sh        # 混响湿量改成 0.4
```

脚本:探测数字输出 → 部署 pipe 插件入口 → 渲染两份配置 → **清掉残留 DSP**(占住共享槽位会让新实例起不来)
→ 重载 PipeWire → 自检(4 个节点 + 2 个 DSP 进程)。

## 调 EQ

`pipewire/20-collaplex-hrtf.conf` 里 `param_eq` 节点(左右各一个, 两处一起改):

```
filters1 = [ { type = bq_lowshelf freq = 120  gain = 0.0 }          # 低频架
             { type = bq_peaking  freq = 1000 gain = 0.0 q = 1.0 } ] # 中频峰
filters2 = [ { type = bq_highshelf freq = 6000 gain = 0.0 } ]        # 高频架
```

`gain` 单位 dB(正=提升), 改完重跑 `安装.sh`。

★ 坑: `param_eq` 的端口名**带编号** —— `In 1` / `Out 1`(走 filters1)、`In 2` / `Out 2`(走 filters2)。
写成 `:In` / `:Out` 会报 `unknown input port` 并让**整张图加载失败**(模块静默降级成直通, 节点都不建)。

## 控制面板

```sh
bash 脚本/面板.sh            # http://127.0.0.1:8789
```

- 电平归一化(响度目标)、总音量、32 段 EQ **全部是滑块**:滚轮调节、Shift+滚轮微调(0.1 dB)、双击归零
- EQ 是 1/3 倍频程 32 段(20 Hz … 25 kHz),每段 ±15 dB,**实时生效**(DSP 从共享内存读参数,不用重装)
- 参数落点:`/dev/shm/collaplex-eq`(前端写、DSP 读,版本号保证不成半更新);响度目标写在
  `~/.local/state/cinema-spatial/loudness.json`(归一化 DSP 每秒重读)

## 实测(2026-09-19)

| 指标 | 数据 |
|---|---|
| 响度归一化 | 输入差 20.00 dB → 输出端差 **0.00 dB**(推子 −6.28 / +13.72) |
| HRTF 双耳化 | 出口左右相关系数 **0.679**(直通 = 1.000) |
| 混响 | 脉冲尾巴拖到 1.2 s(湿量 0.3) |
| 总音量 | 出口电平 −21.5 → **−16.2 dBFS**(+5.3 dB) |

## 试听

```sh
pw-play --target collaplex_hrtf_in 某个.wav    # 只走 HRTF 核心(单曲)
```

全系统:默认输出就是 `collaplex_vsink`,Chrome/播放器/游戏直接出声即走整条链。

## 文件

| 路径 | 作用 |
|---|---|
| `dsp/loudness_norm.py` | 响度归一化 DSP(pipe 插件载荷, 左右各一进程) |
| `dsp/状态.py` | 读共享内存看实时推子/电平 |
| `bin/collaplex-loudness-norm` | pipe 插件入口模板(必须是 ASCII 路径) |
| `pipewire/10-collaplex-vsink.conf` | 虚拟声卡 + 响度归一化 |
| `pipewire/20-collaplex-hrtf.conf` | HRTF 核心 + 混响 + EQ + 总音量 |
| `hrir/` `reverb/` | HRIR(12 个)与房间 IR |
| `脚本/安装.sh` | 渲染 + 部署 + 重置 DSP + 自检 |
