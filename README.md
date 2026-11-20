# Minecraft 模组下载器（客户端 / 服务端 · 中文搜索 · 多线程）

给 Minecraft Java 版用的模组下载器：选定**客户端**还是**服务端**，它自己去把模组需要的**前置依赖**找齐一起下。
数据源是 [Modrinth](https://modrinth.com) 官方 API —— 免费、不用注册、不用填 Token。

## 快速开始

双击 **`启动下载器.bat`**（或直接运行 `run_gui.pyw`）。或是下载MCModDownloader.exe直接运行

需要 Python 3.10+（本机 3.14 已验证），**只用标准库，不需要 pip 安装任何东西**。

## 界面用法

| 步骤 | 操作 |
|---|---|
| 1 | 顶部选 **Minecraft 版本**、**加载器**（Fabric / Forge / NeoForge / Quilt）、**下载目标**（客户端 / 服务端） |
| 2 | 确认 **模组目录**（客户端默认 `%APPDATA%\.minecraft\mods`，服务端默认 `./server/mods`） |
| 3 | 左侧搜索（**中文或英文都行**）→ **双击**结果加入右侧队列，可加多个 |
| 4 | 点 **① 解析依赖** —— 列出本体 + 所有必需前置，标注哪些在目标端不可用 |
| 5 | 点 **② 开始下载** —— 大文件自动多线程分块，已完成文件跳过不重下（SHA512 校验） |

## 中文搜索

Modrinth 的搜索是纯英文索引，直接搜「钠」只会命中汉化包，搜不到 Sodium 本体。本工具内置了
**5289 条中文译名**，搜索时先用译名库做匹配、再把命中的项目置顶，然后合并 Modrinth 自己的结果。

```
搜「机械动力」 →  Create (机械动力)  ← 译名库命中，排第一
                Create: Dragons Plus (机械动力：龙+)
                Create: Interiors (机械动力：内饰)
搜「钠」       →  Sodium (钠)  ← 而不是「钠-汉化模组」
搜「匠魂」     →  Tinkers' Construct (匠魂)
```

细节：

- 模糊匹配：主名精确 > 别名 > slug > 前缀 > 包含；同分时名字短的靠前（「机械动力」排在「机械动力：内饰」之前）
- **繁体输入自动转简体**（`機械動力` 等同 `机械动力`），内置约 300 个高频繁简字对照
- 命中的项目会先校验**目标 MC 版本 + 加载器**，不匹配的不显示（避免加进队列后才发现没版本）
- 英文搜索照常可用，结果里也会补上中文名一列

### 译名数据从哪来

`mcmoddl/data/zh_names.json` 来自 **PCL2**（[Meloong-Git/PCL](https://github.com/Meloong-Git/PCL)）的
`PCLCS/Resource/WikiEntries.txt`，它是 PCL 用来做中文搜索的社区译名表，译名源自 **MC 百科 (mcmod.cn)**。
本项目只保留其中有 Modrinth slug 的条目，转换脚本在 `tools/build_zh_names.py`。

`zh_supplement.json` 是手工补充的 43 个常见模组（暮色森林、匠魂、沉浸工程等 PCL 库里缺的），
每个 slug 都用 Modrinth API 核实过存在且标题匹配，见 `tools/build_zh_supplement.py`。

想更新译名库：从 PCL 仓库下载新的 `WikiEntries.txt`，然后

```powershell
python tools\build_zh_names.py <WikiEntries.txt 路径>
```

## 多线程下载

两层并发，界面上可直接调：

```
下载线程  [3] 个文件并发  ×  [4] 线程分块     （4MB 以上的大文件才会分块；总连接数上限 16）
```

分块策略借鉴 PCL2 的 `ModNet.vb` 多线程下载引擎：

- 先发一个 96 字节的 `Range: bytes=0-95` 探测，**返回 206 才分块**；服务器不支持 Range 就老实单线程
  （硬上会拿到 200 全量响应，拼出来的 jar 是坏的 —— PCL 里对应 `NotSupportRange` 降级分支）
- 碎片要够大才切（对应 PCL 的 `FilePieceLimit`）：小于 4MB 的文件不切，每块至少 1MB
- 任何一块重试 3 次仍失败，就**整文件回退单线程重下**，不会拼出损坏的 jar
- 每块独立写 `.part.pN` 临时文件，全部成功后合并，再校验 SHA512 才改名落地
- 总连接数由信号量限制在 16，避免「8 个文件 × 8 块」把家用宽带打死

实测（18.3MB 的 Create，家用宽带）：

| 方式 | 耗时 | 加速比 |
|---|---|---|
| 单线程 | 14.32s | 1.00x |
| 4 线程分块 | 6.57s | **2.18x** |
| 8 线程分块 | 5.28s | **2.71x** |

三种方式下完的文件 SHA512 完全一致，且与 Modrinth 官方记录相符。

## 客户端 vs 服务端

判断依据是 Modrinth 版本对象的 `environment` 字段（`client_only` / `server_only` / `client_or_server_prefers_both`），
再用项目级的 `client_side` / `server_side`（`unsupported` 则排除）交叉验证。

- **客户端**：过滤掉仅服务端版本；Sodium、Iris、小地图这类纯客户端模组正常工作
- **服务端**：纯客户端模组标 `✗ 不支持` 并跳过 —— 把 Sodium 塞进服务端 `mods/` 只会让服务端启动崩溃
- 服务端解析时，若某模组需要「纯客户端前置」（如 Sodium Extra → Sodium），该前置会被跳过并说明原因

## 依赖解析规则

以 `Sodium Extra` 为例，点解析后自动带出：

```
Sodium Extra    mc1.21.1-0.9.5+fabric   本体        481.4 KB
└ Sodium        mc1.21.1-0.8.13-fabric  前置依赖    1.5 MB    被需要：Sodium Extra
```

- 只自动下载 `dependency_type == required` 的依赖，递归展开到没有新前置（带环检测、深度上限 12 层、最多 250 节点）
- `embedded`（已内嵌）不下载；`optional` 不下载但日志提示「建议安装」；`incompatible` 报冲突警告
- 作者指定的**精确依赖版本**会先校验是否适配当前 MC 版本和加载器 —— 不少老模组把依赖钉死在上一个游戏版本上
  （例如 1.21.1 的模组却指定 1.20.1 的库），这时自动改用最新兼容版本
- Quilt 找不到对应版本时自动回退用 Fabric 版（Quilt 本身兼容 Fabric 模组）

## 命令行用法

```powershell
# 中文搜索（只列出来，不下载）
python run_cli.py --mc 1.20.1 --loader forge --search 机械动力

# 取搜索结果第 1 项并安装（含全部前置）
python run_cli.py --mc 1.20.1 --loader forge --search 机械动力 --pick 1

# 服务端试跑，看会下什么
python run_cli.py --side server --mc 1.20.1 --loader forge --add create-steam-n-rails --dry-run

# 真下载，8 个文件并发、每个大文件 6 块
python run_cli.py --side server --mc 1.20.1 --loader forge --add create --jobs 8 --threads 6
```

常用参数：`--side client|server`、`--mc`、`--loader`、`--dir`、`--jobs`（文件并发）、`--threads`（分块数）、
`--optional`（连可选前置一起下）、`--dry-run`、`--no-zh`、`--list-versions`。

## 项目结构

```
MCModDownloader/
├─ 启动下载器.bat          双击启动 GUI
├─ run_gui.pyw             图形界面入口
├─ run_cli.py              命令行入口
├─ mcmoddl/
│  ├─ api.py               Modrinth API（重试 / 429 限流 / 中文补强搜索）
│  ├─ zh_search.py         中文译名索引（繁简转换 + 模糊匹配）
│  ├─ resolver.py          端过滤 + 递归依赖解析
│  ├─ downloader.py        文件级并发 + 单文件分块多线程 + SHA512
│  ├─ gui.py               tkinter 界面（深色主题，工作线程 + 队列回主线程）
│  ├─ cli.py               命令行实现
│  └─ data/
│     ├─ zh_names.json     5289 条译名（源自 PCL2 / MC 百科）
│     └─ zh_supplement.json 手工补充的 43 条常用模组
├─ tools/
│  ├─ build_zh_names.py    从 PCL 的 WikiEntries.txt 生成译名库
│  └─ build_zh_supplement.py 核实并生成补充译名
└─ tests/
   ├─ smoke_core.py         核心层真实网络测试（搜索 / 依赖树 / 端过滤 / 下载）
   ├─ test_zh_search.py     中文搜索回归（11 个用例，含繁体）
   ├─ test_multithread_download.py 单线程 vs 4/8 线程分块对比
   └─ gui_selftest.py       GUI 端到端自测（搜索→解析→下载，自动截图）
```

## 已知边界

- **只覆盖 Modrinth**。CurseForge 需要 API Key 且禁止第三方分发，未接入 —— 所以 Twilight Forest、
  ProjectE 这类只在 CurseForge 上的老模组搜不到
- 译名库覆盖 5289 个模组，冷门模组可能没有中文名，这时搜英文原名即可
- Forge 与 NeoForge 的模组不互通，不会跨加载器乱猜；Quilt 会自动回退 Fabric
- 可选前置不会自动装（日志里会列名字，加 `--optional` 可一并下载）
- 不解析整合包（`.mrpack`）
- 需要能访问 `api.modrinth.com` 和 `cdn.modrinth.com`

## 测试

```powershell
python tests\smoke_core.py               # 搜索 / 依赖树 / 端过滤 / 下载校验
python tests\test_zh_search.py           # 中文搜索 11 个用例
python tests\test_multithread_download.py # 分块下载一致性与加速比
python tests\gui_selftest.py             # GUI 全流程 + 自动截图
```

## 致谢

中文搜索的译名数据与分块下载的设计思路来自 **PCL2**（Plain Craft Launcher，作者 龙腾猫跃，
[github.com/Meloong-Git/PCL](https://github.com/Meloong-Git/PCL)）；译名原始出处是 **MC 百科 (mcmod.cn)**。
本项目是独立的 Python 实现，只借鉴了数据格式与工程思路，未复制其代码。
