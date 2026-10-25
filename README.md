# Amphoreus Arena v0.1

一个用于验证原生 Agent Harness 对局的最小框架。当前支持中国象棋、9/19 路围棋、DSH ACP、Codex exec/resume。象棋规则由 `pyffish` 提供；围棋采用独立的面积计分、全局同形禁止规则模块。模型接收局面并返回动作，渲染和结果判定由 Arena 完成。原生围棋会话默认首次提供完整图文局面，后续使用增量输入；也可选择每手完整输入或纯文本输入。

围棋入口、通用配置和插件扩展方式见 [围棋与游戏插件](docs/go-plugins.md)。`configs/go-9x9.json` 和 `configs/go-19x19.json` 默认均跑 4 手，象棋旧配置继续兼容。已完成 9 路真实对局（71 手认输终局）、19 路真实短局（4 手），以及增量输入的 19 路完整对局（186 手白方认输终局）；验证记录见 [VALIDATION.md](VALIDATION.md)。

可选 [原生工具终局流程](docs/go-agreement.md) 使用 `configs/go-19x19-agreement.json`：DSH / Codex 通过 MCP 提交、确认死子或请求争议续弈，参照中国 2002 规则的终局流程，原有规则继续保留。

## 本机运行（PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[video]'
.\.venv\Scripts\python.exe -m amphoreus_arena doctor

# 两个真实 Harness；默认图文输入，DSH 执红、Codex 执黑，12 个半回合，单步不限时
.\.venv\Scripts\python.exe -m amphoreus_arena run --output output\matches\my-match

# 本地随机走子，只验证程序管线，不消耗模型额度
.\.venv\Scripts\python.exe -m amphoreus_arena run --random --output output\matches\local-test

# 同模型、同强度，切换为纯文本输入
.\.venv\Scripts\python.exe -m amphoreus_arena run --observation-mode text

# 从标准开局到规则库判定自然终局，不设步数上限
.\.venv\Scripts\python.exe -m amphoreus_arena run --config configs\vision-match.json --until-end
```

输出目录必须尚不存在，避免覆盖已有对局。`run` 会使用现有 Harness 登录状态，并消耗相应模型额度。默认 `turn_timeout: null`，不限制单步模型推理时间；Harness 启动另有 `startup_timeout: 60` 秒限制。`--timeout 600` 可显式限制单步秒数，`--no-timeout` 可覆盖配置文件中的旧时限，两者互斥。非法动作、模型/协议异常或显式时限到期会保存失败现场并停止，不自动重试或换成随机玩家。

`--until-end` 和 `--max-plies` 互斥；配置中 `max_plies: null` 同样表示无步数上限。`configs/vision-match.json` 使用已验证的图文组合：DSH `deepseek-flash / low`、Codex `gpt-6-astra / low`，无步数上限、无单步时限，并明确使用本机 `E:/dsh` 登录状态。默认的 12 手配置使用相同模型与强度，但继承环境中的 DSH_HOME。不会自动导出 GIF 或视频。

在步数上限处正常停止的原生对局可用 `python -m amphoreus_arena resume <原输出目录> --config <原配置>` 取消步数上限继续到终局。续接前重放并核对全部局面，保留原 DSH ACP / Codex 会话 ID、工作区、增量输入与单步时限，写入 `checkpoint-NNNN.json` 和续接事件。默认入口拒绝运行中、失败或已有结果的棋局；模型、推理强度、规则和工具策略必须与原局一致。

针对明确的 Codex 容量不足失败，可显式追加 `--retry-failed`。目前仅支持增量围棋中、没有回复/工具调用/动作提交，且原生同一会话记录确认 `server_overloaded` 的回合；其它失败仍拒绝恢复。恢复前将失败输入、调用日志、棋谱及报告复制到独立的 `attempts/NNNN-XX/` 并核对散列，再向原会话提供一次完整局面同步，之后恢复短增量。不会自动循环重试或更换模型；续接入口也不会向已有回复或提交状态不明的回合重发请求。

DSH 返回棋点但被围棋规则拒绝时，可显式使用 `resume <原目录> --config <原配置> --retry-illegal`，与 `--retry-failed` 互斥。只接受行棋阶段的占用点、禁自杀或全局同形拒绝：必须在重放局面上复现同一错误、确认状态未改变、原生正常 end_turn、回复与协议一致，且无工具调用或动作提交。归档原尝试后，明确告知被拒绝的落点、原因和未提交事实，同步当前完整棋盘，让原会话重新决定；成功后恢复短增量。规则仍独立验证新动作；若再次非法会保留新失败并停止，不自动循环修正。

历史配置 `configs/long-smoke.json` 保留首轮纯文本比赛设置：DSH 默认 ACP 模型、Codex `gpt-6-astra`，均为 low，180 秒时限。新图文对局使用 `vision-match.json`；仅切换 `--observation-mode` 不会自动更换模型或为旧配置补上视觉路由。

原生 DSH / Codex 围棋默认 `prompt_mode: incremental`：每方的第一次输入包含规则、完整局面及配置的观察图；后续只发送该方上次动作的提交结果、对手新增动作、提子和必要的禁入点 / 协商状态，不再重发棋盘、历史、规则、合法动作列表或图片。上次思考用时写入 `your_last_turn.elapsed_seconds`；严格超过 120 秒时附加加快决策的提示，首次也说明 120 秒软目标，均不构成 timeout。规则模块继续验证动作。配置 `prompt_mode: full` 或传 `--prompt-mode full` 可恢复原输入方式；象棋和随机管线仍默认 full。

起始输入 v2 使用精简规则说明和棋盘，不再枚举全盘合法坐标；以少量 `illegal_empty` 禁入点替代，规则模块仍独立验证动作。旧 v1 记录继续按原格式审计。围棋 CLI 默认允许对局工作区内的辅助工具与脚本，禁止安装、部署、实现或使用围棋对弈软件 / 引擎，以及向围棋 AI、外部服务或其他 agent 索取着法与分析。玩家配置 `allow_auxiliary_tools: false` 可保留旧权限行为。启用时 DSH 只选择 ACP 的单次许可，Codex 在该局独立工作区使用 `workspace-write`，不修改全局设置；这些对局约束不等于通用工具的防作弊强隔离。

`configs/go-19x19-full-low.json` 是本机完整围棋配置：DSH `opencode-go/deepseek-v4.1-flash`、Codex `gpt-6.1-sol`，双方 low，无步数上限、无单步时限，使用协商规则与增量输入。运行时须使用新的输出目录。

复制 `arena.example.json` 为 `arena.local.json` 可覆盖本机设置，也可通过 `--config path.json` 指定。两方的 `kind` 可以交换，支持 `dsh`、`codex`、`random`。配置项：

| 位置 | 可选项 |
|---|---|
| 顶层 | `game`（xiangqi / go）、`game_options`、`players`、`max_plies`、`turn_timeout`（秒数或 null）、`startup_timeout`（秒数）、`observation_mode`（text / text_image）、`prompt_mode`（full / incremental）；旧象棋配置的 `red` / `black` / `initial_fen` 仍有效 |
| DSH 玩家 | `desktop_exe`、`home`（DSH_HOME）、`reasoning_effort`、`model`（ACP 广告的完整 option value）、`patches`（启动前加载的 YAML 路径列表）、`allow_auxiliary_tools`（布尔） |
| Codex 玩家 | `executable`、`model`、`reasoning_effort`、`allow_auxiliary_tools`（布尔） |
| 随机玩家 | `seed` |

DSH 默认定位 `D:/Software/DeepSeekHarness/DeepSeek Harness.exe` 内置的 CLI，通过 `ELECTRON_RUN_AS_NODE=1` 启动 ACP，继承本机 `DSH_HOME` 和登录状态。它不使用 PATH 中可能较旧的 npm `dsh`。若安装位置不同，配置 `desktop_exe`。也支持 `command` 数组指定其他 ACP 启动程序；其 `--version` 应能正常退出。默认配置加载随 Python 包分发的视觉路由补丁；示例 JSON 中的相对补丁路径以运行命令的目录为基准，示例命令应从项目根目录执行。

Codex 从 PATH 解析，也可配置绝对 `executable`。它使用原生 exec 和精确 session id resume，并通过 `--output-schema` 约束动作；保留本机账户配置，单次调用覆盖对局指定的模型与推理强度；未启用辅助工具时为只读，启用时允许写入该局独立工作区，均不弹审批 UI。DSH 在 ACP 初始化前通过 `--patch` 选择 `deepseek-official / deepseek-flash`；只在初始化后切换模型不足以启用图片。补丁不改写 DSH 全局配置，实际未声明图片能力时会报错停止。最终采用的配置、两个 Harness 版本与 session id 都保存在记录中。此版本验证接入可行性，不代表公平棋力评测。

## 查看和导出

`replay.html` 是回放控制台，`broadcast.html` 是独立的 1600×900 展示页。支持逐步查看、播放和调整速度；控制台外的展示窗口可同步播放。推荐通过本机 HTTP 打开；直接打开 HTML 也可离线回放。

```powershell
.\.venv\Scripts\python.exe -m amphoreus_arena serve output\matches\my-match
# 浏览器打开 http://127.0.0.1:8765/replay.html

# 只升级已有回放页面，并从原始日志补齐 token 统计；不会调用模型
.\.venv\Scripts\python.exe -m amphoreus_arena replay output\matches\my-match

.\.venv\Scripts\python.exe -m amphoreus_arena export output\matches\my-match --format mp4
.\.venv\Scripts\python.exe -m amphoreus_arena export output\matches\my-match --format gif --seconds 1
```

展示页通过本机 HTTP 每秒刷新记录，进行中的计时器持续更新。控制台可选择“跟随最新”，或手动回看历史。画面按 1600×900 设计，其他窗口尺寸等比缩放并留边。直播采集时直接将 `broadcast.html` 作为浏览器源，尺寸设为 1600×900；详细入口和扩展方式见 [转播界面说明](docs/broadcast.md)。

`export` 仍使用原有 1120×850 PNG 帧，尚未接入新转播界面。它不调用模型，每帧默认 0.8 秒。MP4 优先使用 PATH 上的 FFmpeg，否则使用 `[video]` 依赖自带的二进制。中文字体默认 Windows 微软雅黑；其他系统可设置 `ARENA_FONT` 指向字体文件。

右侧显示双方半身像、Harness、模型、推理强度、累计用时、本手耗时及 token；头顶气泡显示最近一步的落子说明。拖动回放同步切换统计和说明，结局只在末帧出现。DSH token 是 ACP 报告的上下文占用；当前验证的 Codex 版本报告会话累计输入和输出，包含缓存输入，不逐手累加已有累计值。这两种口径分别标明，缺失值显示 —。

DSH 模型来自 ACP 配置；Codex 显式配置的模型先显示为配置值，首步完成后尽力从该 session 的本机 `turn_context` 元数据读取实际模型（CLI JSONL 本身未报告模型）。未知信息显示为等待/未报告，不猜测。

Agent 返回 `{"move":"h3e3","explanation":"简短中文落子说明"}`。`explanation` 是面向观众的 1–2 句目的/风险摘要，保存在 turn 中，与规则动作分开；原始回答仍完整保留。旧棋谱没有说明时显示等待说明，原始记录区显示“本手暂无落子说明”。

每个输出目录包含：

- `match.json`：带 `schema_version` 的对局配置、玩家信息、动作、状态快照和结果。
- `events.jsonl`：回合开始、原始回答、落子、错误和结束事件。
- `frames/*.png`：原有离线渲染帧，供 export 使用。
- `observations/0001.png`：第 1 手落子前发给玩家的棋盘图；纯文本模式没有 PNG。
- `observations/0001.prompt.txt`、`0001.json`：该手实际提示词、来源局面、动作 schema 和图片/文本 SHA-256。`match.json` 的 `turn.input`、进行中的 `active_turn.input`、失败时的 `error.input` 引用相同输入；来源 `source_ply` 对应落子前快照编号。
- `replay.html`、`broadcast.html`、`web/`：回放控制台、独立转播页面及随对局一起保存的本地素材。
- `red/`、`black/`：各方原生协议日志、提示词、最终回答及独立工作目录。
- `error.log`：失败时的异常栈；`replay.mp4` / `replay.gif`：执行 export 后生成。

日志包含对局提示词和原生 Harness 输出，分享时应挑选对局与媒体文件。

## 模块与范围

`contracts.py` 定义 `Game`、`Harness`、观众 `Renderer` 与玩家 `ObservationRenderer` 边界。`runner.py` 负责交替调用与记录，通过注入的观察渲染函数生成图片，不依赖游戏规则。`games.py` 注册游戏工厂、双方身份、参数、PNG/观察渲染器和浏览器脚本，CLI 按注册表装配。象棋规则在 `xiangqi.py`，围棋规则在 `go.py`，ACP 与 Codex 事件解析在 `harnesses.py`。新增游戏实现这些接口并调用 `register_game`；新增 Harness 适配生命周期和动作返回。暂不做动态插件发现、赛事调度、复杂重试或断点续赛。

浏览器转播外壳位于 `web/broadcast.*`，回放控制在 `web/replay.*`，象棋 SVG 渲染单独放在 `web/xiangqi.js`。其他游戏可注册 `ArenaRenderers[game_id]`，复用双方角色、气泡、计时与 token 面板；未注册时使用保存的帧。`presentation` 配置可覆盖标题、角色、配色和阵营名称。

坐标采用 pyffish 原生的 a–i / 1–10；一个半回合指一方走一步。完整历史交给规则库，以便检测重复局面。库报告的可申领终局自动申领，未另行实现完整比赛裁决。达到 `max_plies` 是 `stopped`，没有胜负或和棋结果；真实终局才记录 `finished` 和 winner。回放明确区分这些状态。

`full` 输入模式默认使用 `text_image`：每手在落子前从当前局面生成 900×1000 棋盘观察图，同时提供 FEN、完整历史和当前合法着法。图中红下黑上、四边标坐标和行棋方，没有直播角色或落子后的信息。DSH 使用 ACP 图片块，Codex 首次和续接均使用原生 `--image`；每方仍复用自己的对局会话，每手只新附上当前图片。直播画面与模型观察图独立。

图文提示词仅在原提示词前增加“附件是落子前的当前局面，存在分歧以文本为准”的说明；合法列表继续约束实际动作，仍返回 1–2 句公开落子说明。`--observation-mode text` 保留原文本提示词，不附图。此次未叠加新的规则提示、后继局面查询工具或自动纠错，图片也不能保证消除模型的后继推演错误。象棋保留原工具限制；围棋采用上述辅助工具策略，禁止将着法选择交给围棋软件、引擎或 AI。

## 读图与固定局面对照

```powershell
# 先生成图片，随后仅凭图片识别两个中残局的全部棋子
.\.venv\Scripts\python.exe scripts\eval-vision.py --output output\evals\my-vision-test --phase read

# 读图验证后，重放原局第 12、35、43、58 手之前的局面
# 2 个 Harness × 4 个局面 × 文本/图文 × 2 次重复 = 32 次单步请求
.\.venv\Scripts\python.exe scripts\eval-vision.py --output output\evals\my-vision-test --phase replay

# 仅读取已有结果重新生成报告，不调用模型
.\.venv\Scripts\python.exe scripts\eval-vision.py --output output\evals\my-vision-test --phase report
```

默认原局为 `output/matches/dsh-codex-long-01`，可通过 `--match` 指定包含原始提示词的同格式记录；当前测试局面编号固定。每次请求使用新会话，文本和图文组使用完全相同的原提示词，图文组只增加棋盘附件；两次重复交换输入模式顺序。每个 Harness 内串行、两个 Harness 并行；`--parallel-cases` 可改为最多两个独立请求并行，包括同一个 Harness。配置 `timeout: null` 不限制模型请求时长，数值则为秒数；本机当前默认已按用户要求取消单步时限，连接和版本探测仍有各自的短超时。`--phase all` 顺序执行读图与对照，`--phase prepare` 仅生成局面和图片，`--repeats` 调整重复次数。

`configs/vision-eval.json` 将 DSH 固定为 `deepseek-flash / low`，Codex 为 `gpt-6-astra / low`。DSH 配套 `dsh-vision.patch.yml` 在 ACP 初始化前选定视觉模型，使 `promptCapabilities.image` 为 true；仅初始化后切换模型不足以启用图片。本机登录状态沿用 `E:/dsh`，项目补丁不会修改该目录的全局配置。此 DSH 模型与原长局的 `deepseek-v4-flash` 不同，因此只在本轮同模型的文本/图文组之间比较效果。

产物包含 `report.html`、`summary.json`、`fixtures.json`、棋盘图片和逐请求原生日志。记录图片 SHA-256、提示词 SHA-256、实际模型/强度、公开落子说明、耗时及工具事件；ACP 协议日志中的图片 base64 替换为大小与散列，原图单独保留。规则核验覆盖合法动作、对方一步将死，以及原局关键后继着法是否仍合法；没有引擎局面评分，不把所有吃子机会都算成战术失误。

同目录再次执行会保留已完成或失败的结果；不自动重试失败请求。若上一次中断留下未完成的请求目录，应先检查日志，再使用新的输出目录重试，避免静默重复模型调用。脚本只重放独立单步决策，不续跑整局，不生成视频。

若明确希望取消时限后补跑，使用新输出目录与 `--reuse-successes`：它核对原局、模型、强度和局面集，复制已有成功样本及日志，保留旧目录里的截断记录，仅重新请求其余样本。`continuation.json` 和逐样本 `reused_from` 记录来源。不要把补跑成功率描述成第一次尝试就全部成功，也不要将不同并发条件的耗时视为严格速度基准。例如：

```powershell
.\.venv\Scripts\python.exe scripts\eval-vision.py --output output\evals\vision-replay-unlimited-01 --phase replay --reuse-successes output\evals\vision-replay-01 --parallel-cases
```

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖坐标与非法落子、将死结果、冒烟上限、独立启动/推理时限、失败输入保留、双边 ACP 连续会话的逐手图片字节与落子前局面一致性、纯文本兼容，以及 Codex 精确会话续接与首次/续接传图。真实模型验证记录另见 `VALIDATION.md`。

参考：[Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish)、[DSH ACP 0.2.1-rc.2](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/acp/acp/README.md)、[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。
