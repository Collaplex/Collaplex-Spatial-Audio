# Collaplex 音效

耳机方向的空间音效链路（PipeWire）。

当前链路（2026-09-19 固化）：

```
全局音频(应用输出) → collaplex_vsink(虚拟声卡) → 响度归一化(pipe DSP) → 数字输出(S/PDIF)
```

## 安装

把项目里的定义部署到 PipeWire 用户级：

```sh
bash 脚本/安装.sh
```

脚本会：探测数字输出设备 → 渲染并安装链路配置到 `~/.config/pipewire/pipewire.conf.d/` →
装 pipe 插件入口到 `~/.local/bin/` → 重启 PipeWire → 自检（节点 / DSP 进程 / 默认输出）。

**设备名变化或项目挪了位置**，重跑这个脚本即可 —— 定义在项目里，部署可复现。

## 开关与调参

- 实时状态（推子 / 响度 / 输出电平）：`python3 dsp/状态.py`
- 目标电平（DSP 每秒重读，改完即时生效，不用重建链路）：

```sh
echo '{"on": true, "target_db": -16}' > ~/.local/state/cinema-spatial/loudness.json
```

## 文件

| 路径 | 作用 |
|---|---|
| `dsp/loudness_norm.py` | 响度归一化 DSP（pipe 插件载荷；左右各一进程，flock 共享能量算同一增益，声像不漂） |
| `dsp/状态.py` | 读共享内存看实时推子 / 电平 |
| `bin/collaplex-loudness-norm` | pipe 插件入口模板（纯 ASCII 路径；参数写在这里，配置里只给路径） |
| `pipewire/10-collaplex-vsink.conf` | 链路定义模板（`__TARGET__` 数字输出、`__WRAPPER__` 入口） |
| `脚本/安装.sh` | 渲染 + 部署 + 重启 + 自检 |

## 实测（2026-09-19 固化后复测）

| 输入素材 | DSP 推子 | 数字输出端实测 |
|---|---|---|
| −12 dBFS | −8.37 dB | −19.31 dBFS |
| −32 dBFS | −3.58 dB | −18.97 dBFS |

输入差 20.00 dB → 输出端实测差 **0.34 dB**；推子方向相反（响的压、轻的抬），
目标 −16 dBFS，限速 6 dB/s。

## 注意

- pipe 插件的 `command` 只吃「程序 + 一个参数」，且**不能用中文路径**（否则节点照建、
  音频绕过、零日志）。所以配置里给的是 `bin/` 里的 ASCII wrapper，由它转发到中文项目路径。
- `node.target` 写的是设备名，设备重连/换口后可能变；重跑 `脚本/安装.sh` 修正。
- 播放中的实测要抓 sink 的 monitor：`pw-record --target <sink 节点 id> -P '{ stream.capture.sink = true }'`。
