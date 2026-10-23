# 1600×900 对局转播

本轮使用 `output/matches/dsh-codex-long-01` 的现有 59 手真实棋谱验证。没有启动新的 Harness 对局，没有导出视频。

## 使用

```powershell
.\.venv\Scripts\python.exe -m amphoreus_arena replay output\matches\dsh-codex-long-01
.\.venv\Scripts\python.exe -m amphoreus_arena serve output\matches\dsh-codex-long-01 --port 8766
```

| 页面 | 用途 |
| --- | --- |
| `/replay.html` | 回放控制台。默认开局；方向键跳步、空格播放/暂停；每手 1.2、3 或 6 秒。 |
| `/replay.html?ply=24` | 从指定快照开始查看。这里的 ply 参数是快照索引，当前棋谱与半回合编号一致。 |
| `/broadcast.html?follow=replay` | 独立纯净展示窗口，跟随同源、同目录下的控制台。控制台按钮直接打开此页面。 |
| `/broadcast.html` | 独立实时展示；每秒读取 match.json，适合 OBS 浏览器源。 |
| `/broadcast.html?ply=24` | 固定历史局面，适合截图。 |

采集源设为 **1600×900**。其他尺寸自动等比缩放并留边，不改变布局。纯净展示页没有播放控件、网页滚动条或鼠标悬停工具栏。回放控制台的独立窗口用同源 `BroadcastChannel` 同步，所以保留一个控制台即可；跨浏览器或 OBS 的独立浏览器进程不共享该通道，直播应使用直接读取 match.json 的实时入口。

时间显示真实推理累计耗时与最近一手耗时，不将压缩后的播放时间当作模型用时。累计时间不包含启动 Harness 等开局准备时间。实时数据出现 `active_turn` 时增加当前选手计时；暂停回放时数字固定。

气泡显示 `turn.explanation`（对观众的简短落子说明），不是拼接原生思考日志。长说明自动缩小字号，控制台原始记录区可查看全文。每方只显示所选帧之前的最近说明和统计。结果只在终局帧出现。

## Token 数据

- DSH：`usage.kind = context`，来自 ACP `usage_update` 的 `used` / `size`，表示上下文占用 / 容量，不能当作累计消耗或费用。
- Codex：`usage.kind = session`，来自已验证的 0.162.0 exec/resume `turn.completed.usage` 会话累计计数；展示输入 + 输出，输入包含缓存命中。缓存和推理输出等原生字段也保留在 JSON，未重复相加。更换会改变计数口径的 CLI 版本时需复核适配器。
- 不从当前玩家的最终统计倒填历史。未记录、未提供的值为 —；旧棋谱的 `arena replay` 只从本局目录内原始日志补齐逐手数据。

## 扩展位置

- `web/broadcast.js`：通用双方选手、局面状态、时钟、气泡、用量和实时源。
- `web/replay.js`：控制器，向 iframe / 独立窗口发送 `arena:state`。
- `web/xiangqi.js`：仅象棋棋盘、棋子、着法格式和说明；从 FEN 绘制 SVG，不读取原有 PNG 中的旧侧栏。
- `web/presentation.js`：默认角色映射，按 Harness 种类选择形象，换边时随玩家移动。
- `telemetry.py`：原生协议到显式统计口径的适配与旧局恢复。

新游戏注册 `window.ArenaRenderers[game_id] = {title, subtitle, note, render(container, snapshot), formatAction(turn)}`。转播外壳仍消费 `players`、`turns`、`snapshots`、`active_turn`、`result`。游戏未注册专用浏览器渲染器时，直接展示 `snapshot.frame`。

在运行配置或现有 match.json 中添加可选展示设置：

```json
{
  "presentation": {
    "title": "中国象棋 · 双人邀请赛",
    "players": {
      "red": {"name": "DeepSeek", "side_name": "红方", "avatar": "web/avatars/deepseek.png", "accent": "#68caff", "harness": "DSH DESKTOP"},
      "black": {"name": "GPT", "side_name": "黑方", "avatar": "web/avatars/gpt.png", "accent": "#c5a5ff", "harness": "CODEX"}
    }
  }
}
```

显示页还提供 `window.arena.setFrame(index, {live:false})`、`setRecord(record, {live:false})`、`getState()` 和 `ready`，可供后续确定性截图或录制程序使用。当前 `arena export` 仍是旧 PNG 帧导出，尚未接上此浏览器画面。

## 素材与验证

两张透明半身像使用内置 image_gen，参考 `E:/github/harness-scope/assets/skins/deepseek.png` 和 `gpt.png`。原设定文件保持原样。项目素材为 `src/amphoreus_arena/web/avatars/deepseek.png`、`gpt.png`；完整生成提示词保存在 [avatar-prompts.json](avatar-prompts.json)。界面为 HTML/CSS/SVG，不依赖图像生成来画 UI。

浏览器验证脚本为 `scripts/check-broadcast.cjs`，使用 Playwright 和 Chrome，可用 `ARENA_PLAYWRIGHT` / `ARENA_BROWSER` 指定本机路径。它只加载现有回放，并通过内存 fixture 模拟进行中状态。覆盖全部 60 个局面的 FEN / 棋子位置、逐步说明与 token、结局时机、气泡溢出、1600×900 及缩放、实时计时和轮询、双窗口同步与播放暂停。结果、截图写入 `output/broadcast-validation/`。
