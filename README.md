# Collaplex 音效

把系统音频接成一条**耳机空间音效链**(PipeWire + 自研 DSP), 带本地控制面板。

**第二代(2026-09-19)**: 对第一代 `edgevoid/cinema-spatial` **推倒重构** —— 从"一张 40+ 节点的大图、
改个参数要重装重载"改成"每个效果级独立实例 + 自研 DSP、参数实时生效"。

| | 第一代 (cinema-spatial) | 第二代 (本项目) |
|---|---|---|
| 架构 | filter-chain 大图(40+ 节点), 串联调试 | **每个效果级独立实例** + 自研 DSP, 靠 `node.target` 串链, 可单独推流取证 |
| EQ | `param_eq` 三段, 改配置要重装 | **32 段 1/3 倍频程**(实时) + 10 个预设 |
| 响度 | 无 | **响度归一化**(能量滑窗 + 6 dB/s 限速, 左右 flock 共享增益防声像漂) |
| 混响 | 混在图里, 湿量写死 | convolver(3 秒长 IR) + **独立湿量级**(滑块实时可调) |
| 控制 | 简单开关 | 本地面板 **62 Hz 推送**: 4 个双声道电平表 / 归一化电平与动态增益 / 湿度与总音量 / 32 段 EQ |
| 分发 | deb | deb + 主命令 `collaplex-audio start/stop/web`(装完不动系统音频) |

第二代把"能跑"升级成**"可调 + 可验"**: 每个效果级都能单独推流取证, 参数走共享内存立即生效,
失真 / 削波 / 底噪 / 混响尾巴全部拿实测数据说话(见文末「实测」与「测试脚本」)。

```
全局音频(应用输出)
  → collaplex_vsink          虚拟声卡(全局默认输出)
  → 响度归一化              pipe DSP ×2(左右各一进程, flock 共享增益)
  → collaplex_hrtf_in       双耳 HRTF(SADIE II / H4, 4 条卷积)
  → 房间混响                convolver(3 秒 IR) + 湿量级(pipe DSP ×2)
  → collaplex_eq_in         32 段 EQ + 总音量 + 软限幅(pipe DSP ×2)
  → 数字输出(S/PDIF)
```

共 **6 个节点 + 6 个 DSP 进程**。全链 96 kHz。

## 安装

**源码树里(开发用):**

```sh
collaplex-audio start            # 等价于 bash 脚本/安装.sh
```

**deb 包:**

```sh
sudo dpkg -i collaplex-audio_1.0.0_all.deb
collaplex-audio start            # 装完不会自动接管, 必须手动这一步
```

依赖 `python3-numpy` / `python3-scipy` / `pipewire` / `wireplumber`。
**装完不动你的系统音频**: 不写 pipewire 配置、不做开机自启, 手动 `start` 才接管,
`stop` 即还原(默认输出回到硬件设备)。

## 主命令

日常就三个动作:

```sh
collaplex-audio start    # 启动: 接管系统音频(部署配置 + 重载 PipeWire + 自检)
collaplex-audio stop     # 停止: 恢复系统默认输出, 同时关掉面板
collaplex-audio web      # 面板: 起控制面板并打开浏览器
```

`start` 做的事: 探测数字输出 → 部署 pipe 插件入口 → 渲染 4 份配置 → **清掉残留 DSP**
(占住共享槽位会让新实例起不来) → 重载 PipeWire → 自检。
`WET=0.4 bash 脚本/安装.sh` 可指定初始湿量(默认 0.3)。
面板端口可换: `WEB_PORT=8899 collaplex-audio web`。

## 控制面板

`collaplex-audio web` → http://127.0.0.1:8789

| 区域 | 内容 |
|---|---|
| **电平** | 双声道 4 个表: 入 L / 出 L / 入 R / 出 R。条长 = 实时 RMS(−60~0 dBFS), 小字 = 本块峰值 |
| **电平归一化** | 响度目标滑块 + **输入电平条** + **动态增益推子**(0 dB 在 60% 刻度线上, 量程 −18~+12 dB) |
| **混响湿度** | 0 ~ 1, 实时生效 |
| **总音量** | ±12 dB, 实时生效 |
| **32 段 EQ** | 1/3 倍频程(20 Hz ~ 25 kHz), 每段 ±15 dB,**10 个预设** |

- 所有滑块: **滚轮调节 · Shift+滚轮微调 · 双击归零**
- 面板刷新 **62 Hz**(SSE 推送), 表头带**快起慢落 + 峰值保持 1.6 秒**
- 预设: 平直 / 流行 / 摇滚 / 爵士 / 古典 / 人声 / 低音 / 高音 / 深夜 / 柔化
  —— 每个只写 4~5 个「频率+增益」控制点, 在对数频率轴上插值成 32 段
- 参数落点: `/dev/shm/collaplex-eq`(前端写、DSP 读, 版本号保证不会读到半更新);
  响度目标写 `~/.local/state/cinema-spatial/loudness.json`(归一化 DSP 每秒重读)
- **改参数不用重装** —— 只有改 DSP 代码或 PipeWire 图才需要重跑 start

## 空间数据来源

| 用途 | 数据 | 来源 |
|---|---|---|
| 双耳 HRTF | **SADIE II** 受试者 **H4**, 96 kHz / 24 bit, 每条 4800 tap(约 50 ms) | University of York 音频实验室, [Zenodo DOI 10.5281/zenodo.12092466](https://doi.org/10.5281/zenodo.12092466) |
| 房间混响 | 自家房间模型程序化生成的双耳 IR(3 秒) | 本项目 `reverb/` |

- `hrir/` 的 12 条 = H4 的 6 个方向 × 左右耳(BL / BR / FC / FL / FR / LFE);
  链路实际用**前左 + 前右**共 4 条(`FL_L` / `FL_R` / `FR_L` / `FR_R`)做双耳化
- SADIE II 为**真人受试者**测量: 2818 个方向, 仰角 ±90°, 测距 1.2 m

## 实现要点与踩过的坑

### 结构

- **每个效果级做成独立实例**: 一个接入 sink + 一个输出 target, 靠 `node.target` 串成链。
  可以在任意一级单独 `pw-play` 推流验证, 不用在主链路上串联调试。
- **`pipe` 插件的 command 只吃「程序 + 一个参数」**(shebang 式 exec), 参数全写进 wrapper;
  且 wrapper 路径必须是**纯 ASCII**(中文路径静默失效)。
- **湿量为什么抽成独立 DSP**: 混响交给 PipeWire 的 convolver(能跑 3 秒长 IR), 但图里的
  mixer 增益是**加载时定死**的, 改它必须重载 = 断音。所以只把"湿量"抽出来做 DSP 增益级。
- 左右声道各 fork 一个 DSP 进程、命令相同, 靠 **flock 抢声道槽位**区分(进程死锁自动释放)。

### DSP

- **块长不能假设固定**: `quantum` 在 32 ~ 2048 之间浮动, 按实际读到的长度一对一处理。
  用 `np.resize` 补到固定长度会**重复填充** → 周期性伪影(听感"沙沙")。
- **增益要块内插值**: 整块共用一个增益会在块边界留台阶(zipper 噪声)。改为
  「上一块增益 → 本块增益」的逐样本斜坡后, 谐波(相对基频) +0/+6 dB 由 −12.1 / −15.9 dB
  降到 **−90.1 / −70.6 dB**。
- **软限幅阈值要贴近 0**: 上限 `ceiling=0.99 / knee=0.06`(介入点 −0.63 dBFS)。
  曾用 −3.9 dBFS, 而**音乐**的峰值(−4 ~ 0 dBFS)长期落在里面被压 → 中低频谐波。
- 归一化用**能量滑窗 + 限速**(目标 −14 dBFS, 6 dB/s), 左右进程 flock 共享同一增益 → 声像不漂。

### 陷阱

- **`convolver` 的 `gain` 是线性倍数, 不是 dB** —— 写 `-18.0` 会变成"反相 ×18", 电平反而更高。
  房间 IR 未归一化(频响在 100~200 Hz 高 20 dB), 所以要标定成 `gain = 0.126`。
- **`param_eq` 端口名带编号**(`In 1` / `Out 1`), 写成 `:In` 会让**整张图加载失败**, 模块静默降级成直通。
- **峰值相关的失真, 纯音测不出来** —— 纯音峰值因子仅 3 dB, 音乐是 10~14 dB。
  必须用高峰值因子素材测, 见 `测试/test_limit.py`。
- **改完 `watchdog` / 常驻脚本要重启巡检** —— 旧定义还在内存里按旧参数拉服务。
- 清进程时匹配串要够特异: 写 `*dsp/eq.py*` 会匹配到**自己的命令行**而自杀,
  至少写成 `*python*dsp/eq.py*`。

## 实测(2026-09-19)

| 指标 | 数据 |
|---|---|
| 响度归一化 | 输入差 20.00 dB → 输出端差 **0.00 dB** |
| HRTF 双耳化 | 出口左右相关系数 **0.679**(直通 = 1.000) |
| 混响 | 湿量 0 → 无尾巴; 湿量 1 → **3 秒平滑衰减尾巴**(−11 → −129 dBFS 实测序列) |
| 总音量线性 | +0/+6/+12 dB → RMS 提升 +0.00 / +5.99 / +11.20 dB |
| 软限幅 | +12 dB 输出峰值 **−1.11 dBFS**, 贴顶样本 0% |
| 面板刷新 | **62 Hz**(SSE 推送, 2 秒实测 124 帧) |
| 失真 | 纯音谐波(相对基频) +0/+6 dB: **−91.1 / −91.1 dB** |
| 白噪底噪 | 旁路整条链 vs 经链路: 均为 **−240 dBFS**(链路不自造噪声) |

## 试听

```sh
pw-play --target collaplex_hrtf_in 某个.wav    # 只走 HRTF 核心
pw-play --target collaplex_vsink   某个.wav    # 走整条链
```

全系统: 默认输出就是 `collaplex_vsink`, Chrome / 播放器 / 游戏直接出声即走整条链。

## 开发

```sh
uvx pyright --project pyrightconfig.json         # 类型检查(要求 0 errors)
bash 打包deb.sh                                   # 出 deb 到 发布/
uv run --with websocket-client python 测试/cdp_拖动验证.py    # CDP 真鼠标验证
```

`测试/` 里是取证脚本(逐级频谱、谐波、削波、湿度尾巴、预设、拖动), **都用真实数据说话**:

| 脚本 | 验什么 |
|---|---|
| `test_eq.py` / `test_e2e_eq.py` | EQ 端到端(1 kHz +12 dB、总音量 +6 dB) |
| `test_limit.py` | 软限幅(高峰值因子素材, 常规段增益应精确) |
| `test_thd.py` | 谐波失真随总音量变化 |
| `test_stage_spec.py` | 逐级旁路频谱(定位是哪一级脏) |
| `test_wet.py` | 湿度 → 混响尾巴能量 |
| `cdp_拖动验证.py` / `cdp_预设验证.py` | 前端真点击/真拖动(不看 DOM, 看实际写入) |

## 文件

| 路径 | 作用 |
|---|---|
| `dsp/common.py` | 前端 ↔ DSP 共享内存协议(参数区 + 版本号 + 双声道状态区) |
| `dsp/loudness_norm.py` | 响度归一化 DSP |
| `dsp/eq.py` | 32 段 EQ + 总音量 + 软限幅 DSP |
| `dsp/wet_gain.py` | 混响湿量增益级 |
| `dsp/初始化.py` | 写共享内存默认值(安装脚本调用) |
| `dsp/状态.py` | 命令行看归一化的实时推子/电平 |
| `bin/collaplex-audio` | 主命令(start / stop / web) |
| `bin/*` | pipe 插件入口模板(ASCII 路径, 安装时渲染) |
| `pipewire/10,20,30-*.conf` | 虚拟声卡+归一化 / HRTF+混响+湿量级 / EQ |
| `pipewire/91-collaplex-clock.conf` | 强制 96 kHz(不然 IR 会被按 48k 降采样) |
| `web/server.py` `web/index.html` | 控制面板(纯标准库 + 单页, SSE 推送) |
| `hrir/` `reverb/` | HRIR(12 条)与房间 IR |
| `脚本/安装.sh` `脚本/面板.sh` | 部署 / 起面板 |
| `打包deb.sh` | 打 deb 到 `发布/` |
