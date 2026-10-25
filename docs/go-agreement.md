# 19 路规则验证与原生工具终局

旧配置继续使用 `area-positional-v1`，双停立即按盘面计分。新增可选的 `area-positional-agreement-v1`，入口是 `configs/go-19x19-agreement.json`；默认也只运行 4 步，不会自动启动完整对局。

## 赛事规则对照

选择《中国围棋规则》2002 版作为后续对齐目标。本轮落实双方确认死子、清死后面积计分、死活争议由主张死亡的一方先行续弈，对照第 8、9、21.4 条。[中国围棋协会规则页面](https://wqwh.weiqi.org.cn/rules/)；[协会审定的 2002 英文版存档](https://home.snafu.de/jasiek/c2002.pdf)（中文原文为最终依据）。

界面标注“协商规则 · 参照中规2002”，不将当前版本宣称为完整赛事裁判。以下差异明确保留：

- 全局同形仍使用原有严格的棋盘位置 superko；特殊循环、无胜负及裁判裁量尚未实现。
- 内部仍记录双方面积分与白贴 7.5，不给双方各加一半公气。双方同加公气不改变分差；展示的面积差除以二对应数子法的胜负子数。双方分数本身不是传统的单方数子结果。
- 让子、比赛计时、读秒、超时判负与赛场处罚尚未实现。
- 双方均接受错误死活判断时，程序照双方协议计分；工具不会冒充棋力引擎或自动裁判。

## 流程

1. 正常落子继续使用原有 `move` JSON；双停进入 `agreement`，棋盘和劫历史不变。
2. 轮到的一方提交完整死子提案，允许包含双方棋子。一个坐标自动扩展为整块棋，空列表表示全部存活。
3. 对手接受精确提案 ID，才产生胜负；接受前只显示待确认标记，不改棋盘或提子数。
4. 有分歧时指定争议棋子。若它已列在提案中，表示质疑方主张它活，由提案方先行；若未列出，表示质疑方主张它死，由质疑方先行。无提案时请求续弈，则由第一次停着方先行。
5. 续弈清零连续停着数，保留全局同形历史并作废旧提案。再双停会建立新修订号。

终局记录保留原物理棋盘，`result.dead` 单独保存确认移除计分的死子；回放用叉号标记。`captures` 只统计实际落子提子，协议移除不伪装为一次提子。计分采用去掉确认死子后的副本。

`ply` 是 Arena 决策步数，包含提案、确认和续弈操作；新快照的 `play_ply` 排除这三种操作。`max_plies` 仍限制总决策步数，协商中达到上限只会暂停，不产生胜负。

## 原生 MCP 工具

起始提示只说明必要棋规和进入协商的方式，具体操作参数与争议续弈顺序由工具描述承载。围棋 CLI 默认允许本局工作区内的辅助工具与脚本；禁止安装、部署、实现或使用围棋对弈软件 / 引擎，或借助围棋 AI / 外部 agent 获取着法或分析。`allow_auxiliary_tools` 只影响本局原生权限：DSH 单次许可、Codex 工作区可写；不是防引擎使用的强制安全隔离。

DSH 通过 ACP `session/new.mcpServers` 接入，Codex 通过本次调用的 `mcp_servers.arena_go` 配置接入，包括精确 session resume。不会写入用户的全局 MCP 配置。Codex 配置参数统一放在 `exec` 前，避免本机 CLI 的跨子命令 `-c` 列表覆盖问题。配置形式参考 [OpenAI MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

| 工具 | 作用 |
|---|---|
| `go_get_state` | 读取当前局面、己方身份、提案 |
| `go_preview_score` | 对假设死子列表试算，不确认死活或改变状态 |
| `go_propose_dead` | 提交完整死子提案，带修订号 |
| `go_accept_dead` | 接受当前提案 ID 与修订号 |
| `go_resume_play` | 指明争议棋子并恢复落子 |

每个 stdio MCP 子进程只持有所属席位的临时凭据，连接当前对局的本地回环端口。服务端校验当前行棋方、阶段、修订号、提案 ID 和坐标。每个回合最多暂存一个决策，完全相同的重试不会重复应用；模型返回 `{"move":"tool","explanation":"..."}` 后 Runner 才提交。没有成功工具调用的文字声明会报错，不能绕过双方确认。超时或协议异常不提交待定操作。

工具参数、返回值与实际动作保存在 `turn.tool_calls` 和协议日志；失败回合也留有工具记录。DSH 的工具前说明单独保存在 `*.transcript.txt`，工具后的最终回答仍严格解析 JSON，不从任意文本中猜测动作。

## 验证与复现

```powershell
# 全部本地测试，无模型请求
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 两组短的脚本化原生 MCP 样例，包含逐步图文与回放，无模型请求
.\.venv\Scripts\python.exe scripts\prepare-go-agreement-checks.py --output output\agreement-fixtures-new

# 单独核验记录、观察图和原生决策来源，无模型请求
.\.venv\Scripts\python.exe scripts\audit-go-match.py output\agreement-fixtures-new\agreement

# 显式调用真实 Harness，仅固定局面工具验收，共 4 次模型请求；不是完整对局
.\.venv\Scripts\python.exe scripts\check-go-tools.py --real --output output\agreement-native-new

# 可选协商规则下的普通开局短测，默认 4 步
.\.venv\Scripts\python.exe -m amphoreus_arena run --config configs\go-19x19-agreement.json --output output\agreement-short-new
```

2026-10-24 验证：70 项测试通过。新增 19 路双活、公气计分、打二还一、四边旋转的角劫/边劫，以及黑胜/白胜/和棋数子终局用例；协商测试覆盖双方确认、过期/非法操作原子性、双方不同死活主张的续弈顺序、劫历史保留、MCP 传输、Runner 与日志。

最终真实工具验收在 `output/go-agreement/native-04/verification.json`：DSH 与 Codex 各调用两轮，提交/接受和提交/争议两条路径通过，也验证了 Codex session resume。调试中发现并修复的 CLI 配置覆盖与 ACP 工具前说明混入问题保留在前序目录，不能把那些失败尝试计作通过。

脚本化样例在 `output/go-agreement/replay-fixtures/`：确认终局 11 个快照，争议后实际提子再确认 16 个快照，逐帧与输入审计通过。浏览器结果在 `output/go-agreement/browser/`；象棋及旧围棋回归样例在 `legacy-fixtures/`。本轮未运行完整模型对局，也未用工具替模型下棋或评价棋力。
