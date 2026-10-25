# 围棋与游戏插件

象棋和围棋使用相同的 CLI、MatchRunner、Harness、日志、图文输入和回放控制台。游戏注册表位于 `src/amphoreus_arena/games.py`；棋盘规则和显示由各游戏提供。

## 运行短局

在项目根目录执行，输出目录必须尚不存在：

```powershell
# 本地 4 手管线检查，不调用模型；默认生成逐手观察图
.\.venv\Scripts\python.exe -m amphoreus_arena run --game go --random --max-plies 4 --output output\matches\go-local

# DSH 执黑、Codex 执白，各走一手；使用已有登录状态
.\.venv\Scripts\python.exe -m amphoreus_arena run --config configs\go-9x9.json --max-plies 2 --output output\matches\go-models

# 9 路完整对局：取消步数上限，直到认输或双停终局
.\.venv\Scripts\python.exe -m amphoreus_arena run --config configs\go-9x9.json --until-end --output output\matches\go-9x9-full

# 19×19 实机短局：同样使用图文输入，默认 4 手
.\.venv\Scripts\python.exe -m amphoreus_arena run --config configs\go-19x19.json --output output\matches\go-19x19-short

# 查看回放；浏览器访问 http://127.0.0.1:8765/replay.html
.\.venv\Scripts\python.exe -m amphoreus_arena serve output\matches\go-models
```

`configs/go-9x9.json` 和 `configs/go-19x19.json` 默认上限均为 4 手，`observation_mode` 为 `text_image`。原生 DSH / Codex 会话默认增量输入，仅每方首次附完整局面及观察图，后续只发变化和上次用时，超过 120 秒附软提醒；`--prompt-mode full` 恢复每手完整输入。`--observation-mode text` 可改为纯文本，`--until-end` 取消步数上限。推理默认不限时，启动仍有独立时限。达到上限记录为 `stopped / max_plies`，不产生胜负。`replay` 和 PNG/GIF/MP4 导出继续共用原有命令。

## 通用配置

```json
{
  "game": "go",
  "game_options": {"board_size": 9, "komi": 7.5, "rules": "area-positional-v1"},
  "players": {
    "black": {"kind": "random", "seed": 7},
    "white": {"kind": "random", "seed": 8}
  },
  "max_plies": 4,
  "turn_timeout": null,
  "startup_timeout": 60,
  "observation_mode": "text_image"
}
```

- 象棋身份为 `red / black`，围棋为 `black / white`，注册顺序即展示座位顺序，与 JSON 键顺序无关。
- 新象棋配置使用 `game_options.initial_fen`、`players.red` 和 `players.black`。旧的顶层 `initial_fen / red / black` 继续兼容；新旧玩家配置混用或 FEN 冲突会明确报错。
- 游戏参数、规则、贴目写入 `match.json.game_options`，围棋快照也包含规则和贴目。用户的 `presentation.players` 仍可覆盖阵营名称、角色和配色。
- 未知游戏、未知游戏参数、缺失/多余阵营在建立输出目录、启动模型之前报错。随机模式会用注册的双方身份创建随机玩家。
- 9 路坐标为 `A–H、J` / `1–9`，19 路为 `A–H、J–T` / `1–19`，均跳过字母 I；数字由下向上。快照 `board` 是从上到下的字符串列表，`B/W/.` 表示黑子、白子、空点。

## 首版围棋规则

规则 ID 固定为 `area-positional-v1`，版本名会写入棋谱。它是明确的机器对弈规则，不宣称覆盖中国或日本赛事的所有裁决。

另有独立的可选 `area-positional-agreement-v1`，增加双方原生 MCP 死子确认和争议续弈，参照中国 2002 规则；旧配置行为不变。接口、差异和验证见 [19 路规则与工具终局](go-agreement.md)。

1. 黑先白后；先提掉邻接的无气对方棋块，再检查己方是否无气。禁止自杀落子。
2. 棋盘局面不得与历史中任何棋盘局面相同（positional superko）。比较只看棋子分布，保留全部历史；包括多劫循环。
3. `pass` 停一手不受重复局面限制；正常落子清零连续停着数。连续两次 `pass` 终局。
4. `resign` 立即认输，不能在终局后继续落子。
5. 面积计分：盘上己方棋子数，加仅接壤己方棋子的空区域大小。双方都接壤的空区域以及无人接壤的空盘不计分；白方加贴目。提子数仅用于显示，不额外加分。
6. 终局前须通过实际落子提净死子并收完边界。程序按停着时的盘面计分，不自动判死、不协商死子、不使用模型或棋力引擎猜测结果。过早双停也按这一约定计分。

默认 9 路、白贴 7.5；尺寸可设为 9、13、19，贴目可设为有限整数或半整数。已验证 9 路真实完整对局和 19 路真实 4 手短局；13 路尚未进行真实模型对局或浏览器验收。单盘运行不用于判断模型棋力，19 路增量输入完整长局已于 2026-10-25 验证，186 手白方认输；真实死子协商终局分支未在该局覆盖，详见 [验证记录](../VALIDATION.md)。

动作保持 `{"move":"D4","explanation":"简短中文说明"}` 格式，特殊动作是 `pass` 与 `resign`。围棋动作 schema 列举当前合法动作，裁判仍独立验证动作。随机测试玩家会从合法动作中抽取，因此可能停着或认输。

## 新游戏注册

`GamePlugin` 包含 `game_id`、`player_ids`、`side_names`、`create_game`、`create_renderer`、`observation_renderer`、`browser_scripts` 和 `default_options`。在启动前调用 `register_game`，CLI 即可按 `game` 选择。重复 ID 会报错。注册来自受信任代码，不从 JSON 动态导入模块。

游戏实现 `Game` 的 `current_player / observation / action_schema / prompt / step / snapshot / outcome`。快照需要 `current_player`，结果使用 `winner`（玩家 ID 或 null）和 `reason`，可附加分数等字段。Runner 会把 `explanation` 从动作中取出单独保存。图文模式需要单独的观察渲染器；缺失时启动前报错。

浏览器脚本注册 `window.ArenaRenderers[game_id]`，包含 `title / subtitle / note / render / formatAction`，可选 `sideNames / describe(snapshot) / formatResult(result, snapshot)`。回放生成器只加载该插件在代码中注册的本地脚本；未知游戏保留保存帧回退。`go.js` 独立绘制棋盘、提子信息、最后落点、停着与结果，不修改象棋渲染器。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 生成本地短局和固定提子/计分样例；无模型请求
.\.venv\Scripts\python.exe scripts\prepare-game-checks.py --output output\game-checks-new
.\.venv\Scripts\python.exe -m http.server 8877 --bind 127.0.0.1 --directory output\game-checks-new
# 另开终端，确保 playwright 可导入；可用 ARENA_PLAYWRIGHT 指向本机包目录
node scripts\check-games.cjs http://127.0.0.1:8877 output\game-checks-new\browser

# 离线重建实际棋谱，逐手核对合法动作、文本、观察图、提子和结果
.\.venv\Scripts\python.exe scripts\audit-go-match.py output\matches\go-9x9-full
```

规则测试覆盖单块/多块提子、共享气、自杀禁入、立即劫与三劫循环、非法动作原子性、停着、认输、面积/公气/贴目、终局不自动清除死子，以及 19 路边界坐标、边角提子、计分和观察图。集成测试覆盖旧象棋配置、游戏注册、短局上限、图文输入与快照一致性、脚本选择。浏览器检查逐帧核对棋子、提子消失、最后落点、阵营名称、结果出现时机、控制台、双窗口同步、播放暂停和缩放。

首轮验证产物位于 `output/go-implementation/`：当时 51 项单元/集成测试全部通过，4 组本地回放样例及真实 2 手短局通过浏览器检查，已有象棋长局的 60 个快照通过兼容性验证。象棋与围棋本地冒烟各 4 手，计分终局另有固定样例。已有象棋棋谱只复制到独立验证目录，没有改写原件。

2026-10-24 补充验证后，共 54 项单元/集成测试通过。双方为 DSH `deepseek-flash / low`（黑）与 Codex `gpt-6-astra / low`（白），采用逐手图文输入：

| 对局目录（`output/matches/`） | 实际运行 | 结果 | 浏览器检查 |
|---|---|---|---|
| `go-9x9-full-01` | 71 手，41 分 22 秒 | 黑方认输，白方胜；白提 11 子 | 全部 72 个快照通过 |
| `go-19x19-real-01` | 4 手，约 41 秒 | `Q16 → D4 → D16 → Q4`；达到上限停止，无胜负 | 全部 5 个快照通过 |

两盘均用 `scripts/audit-go-match.py` 离线重建，每手合法动作、状态、提示词与观察图哈希、提子和最终结果全部一致；报告保存在各对局目录的 `audit-go-match.json`。该审计不调用模型。9 路结果来自认输，盘面面积分仅供检查，不作为胜负分差。19 路还完成了独立的本地 4 手管线检查；完整对局留待后续验证。浏览器报告分别在 `output/go-implementation/go9-full-browser/` 和 `go19-real-browser/`。
