# ReviewAgent

审查 Agent，Homework 2「编码 → 审查 → 修复」工作流里的第二个节点。审查能力全部来自 Homework 1 的
codeguard，这个仓库负责把它包装成一个能被编排器调用的工作流节点，产出契约化的 finding 清单和门禁
结论。

> 源码仓库：<https://github.com/0x697551b0/hw2-review-agent>　作者：刘浩天 / 2412190219

`v1.0.0` · Python `3.10+`（开发环境 3.14.7）· 0 第三方依赖 · 测试 41/41

## 它是干什么的

```
  ① coding-agent → worktree/user_store.py（代码草案）
        │
        ▼
  ╔══════════════════════════════════════════════════════╗
  ║ ② review-agent  ← 本仓库                             ║
  ║   locate.py     找到隔壁的 codeguard                  ║
  ║   static 模式   跑确定性规则引擎（按文件并行）          ║
  ║   llm   模式    跑 codeguard 的完整 Agent 闭环         ║
  ║   normalize.py  归一化 + 稳定 finding_id              ║
  ║   fixability    标记"能自动修 / 需人工"                ║
  ║   产出 findings[] + passed + score + review.md        ║
  ╚═══════════════════════┬══════════════════════════════╝
                          ▼
  ③ fix-agent（按 finding_id 自动修复）
                          ▼
  ④ workflow 编排器（复验 → 回到②、门禁、报告）
```

上游是编码 Agent 或任何一段代码，下游是修复 Agent 和编排器。本 Agent 的关键产出不是一份好看的报告，
而是 passed 这个布尔值，以及一组能被下游精确引用的 finding_id。

它自己其实很薄，主要做这么几件事：

- 找到隔壁的 codeguard。不要求 pip install，放同一目录就行，另外还有 --codeguard-path 和
  HW2_PACKAGES_HOME 两条显式通道。
- 把 codeguard 的 ReviewReport 归一化成工作流契约，每条问题分配一个跨轮次稳定的 finding_id。
- 按显式配置的 blocking 口径算 passed，并给出逐条判定明细，供编排器做条件分支。
- static 模式下按文件并行审查。多文件时墙钟时间基本随文件数线性缩短。

## 快速开始

需要 Python 3.10+。本仓库不通过 pip 依赖 codeguard（pyproject.toml 里 dependencies = []），
靠"放在附近"自动发现。开发时的目录布局是这样：

```
软件工程实践/
├── codeguard/                 ← Homework 1 的仓库（含 codeguard/__init__.py）
└── homework2/
    ├── coding-agent/
    ├── review-agent/          ← 本仓库
    ├── fix-agent/
    └── code-review-workflow/
```

codeguard 在当前工作目录、本仓库、本仓库上级目录及其直接子目录的范围内都能被找到。开发时实际
定位到的结果：

```
$ python -m review_agent doctor
【codeguard 定位】
  ✅ 可用    版本：1.0.0
     来源：D:\软件工程实践\codeguard\codeguard\__init__.py
```

不用 API Key 就能跑一遍完整流程（隔壁仓库离线生成草案，再拿回来审查）：

```bash
cd ../coding-agent
python -m coding_agent generate examples/spec_user_store.md --out ./worktree --overwrite

cd ../review-agent
python -m review_agent review ../coding-agent/worktree
```

装不装都行。`pip install -e .` 可选，不装也能直接用 `python -m review_agent`。

LLM 模式要 Key；没 Key 也能用 `--mock` 走一遍 codeguard 的离线闭环，先看流程跑不跑得通：

```bash
python -m review_agent review ../coding-agent/worktree --mode llm --mock
$env:CODEGUARD_API_KEY = "sk-xxxxxxxx"
python -m review_agent review ../coding-agent/worktree --mode llm
```

## 命令行

四个子命令：

| 子命令 | 作用 | 需要 Key |
| --- | --- | --- |
| `review <目标>` | 审查目录或单个文件 | 只有 `--mode llm`（`--mock` 除外） |
| `doctor` | 诊断 codeguard 定位情况与当前配置 | 否 |
| `rules` | 打印 27 条规则的可修复性对照表 | 否 |
| `info` | 打印版本与配置 | 否 |

`review` 的参数：

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| `--mode` | `static` / `llm` | 审查模式，默认 `static` |
| `--mock` | 开关 | `--mode llm` 时用 codeguard 的离线脚本化模型 |
| `--pattern` | glob，可重复 | 文件匹配，默认 `*.py` |
| `--blocking` | 级别，可重复 | 阻断级别，默认 `critical` + `high` |
| `--rules` | 规则 ID，可重复 | 只跑指定规则（仅 `static`） |
| `--parallel` | 整数 | 并行审查文件数，默认 4 |
| `--codeguard-path` | 路径 | 显式指定 codeguard 源码根目录 |
| `--api-key` / `--model` | 字符串 | 透传给 codeguard（`--mode llm`） |
| `--report-dir` | 路径 | 把 `review.json` / `review.md` 写到该目录 |
| `--json` / `--quiet` | 开关 | 契约 JSON 输出 / 只输出一行结论 |
| `--fail-on-blocking` | 开关 | 存在阻断级问题时返回退出码 1 |

```bash
python -m review_agent review ../coding-agent/worktree                                  # 人类可读报告
python -m review_agent review ../coding-agent/worktree --quiet                           # 一行结论
python -m review_agent review ../coding-agent/worktree --json --report-dir ./reports/r1 --fail-on-blocking  # CI 门禁
python -m review_agent review ../coding-agent/worktree --blocking critical --fail-on-blocking  # 放宽口径
python -m review_agent review ../coding-agent/worktree --rules CG105 --quiet             # 只看一条规则
python -m review_agent review . --pattern 'test_*.py' --quiet                            # 换文件范围（本例：42 条，门禁通过，健康分 52）
python -m review_agent review ../coding-agent/worktree --mode llm --mock --quiet         # 无 Key 走 LLM 闭环
python -m review_agent review ../coding-agent/worktree --codeguard-path ../../codeguard  # 显式指定
python -m review_agent.worker --request-file job.json --response-file out.json           # 子进程模式
```

同一份代码，blocking 口径不同结论就不同：

| 口径 | 结论 |
| --- | --- |
| `--blocking critical,high`（默认） | ❌ 未通过门禁（3 条阻断级） |
| `--blocking critical` | ❌ 未通过门禁（1 条阻断级） |
| `--blocking critical --blocking high --blocking medium` | ❌ 未通过门禁（6 条阻断级） |

退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 审查完成且（若指定 `--fail-on-blocking`）通过门禁 |
| `1` | 指定了 `--fail-on-blocking` 但存在阻断级问题；`doctor` 找不到 codeguard 时也返回 1 |
| `2` | 用法/配置错误（mode 非法、blocking 含未知级别、`--mode llm` 缺 Key、目标路径不存在） |

目标路径不存在时只输出一行错误并返回 2：

```
$ python -m review_agent review ./definitely-not-here
❌ 找不到路径：审查目标不存在：D:\软件工程实践\homework2\review-agent\definitely-not-here
$ echo $?
2
```

这条分支以前漏过：FileNotFoundError 是内置异常，不会被 ReviewAgentError 接住，路径打错时用户看到的
是一整屏堆栈加退出码 1。现在由回归测试 `test_cli_missing_path_returns_2` 守着，详见 Design.md §8.4。

## 输出与契约

审查 `realistic` 草案时（37 行 `user_store.py`，真实输出，节选）：

```
$ python -m review_agent review ../coding-agent/worktree
# 代码审查报告

- 模式：`static`
- 问题总数：15
- 分布：critical 1 / high 2 / medium 3 / low 5 / info 4

| # | 级别 | 规则 | 位置 | 问题 | 可自动修 |
| --- | --- | --- | --- | --- | --- |
| 1 | critical | CG105 | `user_store.py:8` | 疑似硬编码密钥：AUTH_TOKEN | ✅ |
| 2 | high | CG106 | `user_store.py:13` | SQL 语句由拼接/格式化构造（在 create_user 中） | ✅ |
| 3 | high | CG203 | `user_store.py:26` | 可变默认参数：find_users()（在 find_users 中） | ✅ |
| 4 | medium | CG207 | `user_store.py:18` | 使用 assert 校验外部输入 | ✅ |
| 5 | medium | CG202 | `user_store.py:22` | 捕获 `Exception` 后静默忽略 | ✅ |
| 6 | medium | CG206 | `user_store.py:34` | open() 未使用 with 上下文管理器 | ✅ |
| 7 | low | CG403 | `user_store.py:4` | 未使用的导入：hashlib | ✅ |
| 8 | low | CG304 | `user_store.py:11` | 缺少文档字符串：create_user（在 create_user 中） | — |
| … （共 15 条） | | | | | |

## 需要人工处理

- `user_store.py:11` **缺少文档字符串：create_user（在 create_user 中）**：docstring 得写作者的真实意图，机器编出来的注释没意义
- `user_store.py:11` **缺少返回值类型注解：create_user（在 create_user 中）**：返回类型得作者确认，注解猜错比不写更糟
- …

────────────────────────────────────────────────────────────────────
结论：审查 1 个文件，命中 15 条（1 critical、2 high、3 medium、5 low、4 info）；❌ 未通过门禁（3 条阻断级）；健康分 38
耗时：0.01s    可自动修 7 条 / 需人工 8 条
```

这 15 条对应编码 Agent `realistic` 档位埋下的 7 类问题：CG105、CG106、CG203、CG207、CG202、CG206、
CG403 各 1 条，外加 4 处 CG304（缺 docstring）和 4 处 CG307（缺返回值注解）。

用 `--profile clean` 生成的干净版本是另一种结果：46 行，4 条 finding，全是 info 级的 CG307，
门禁通过，健康分 100。这个 profile 存在的意义就是验证"门禁能通过"这条分支不是永远为假。

可修复性用 `rules` 看：

```
$ python -m review_agent rules
规则      可自动修      策略                          说明
CG105   ✅         move_secret_to_env
CG106   ✅         parameterize_sql
CG202   ✅         log_broad_except
CG203   ✅         sentinel_default
CG204   ✅         identity_comparison
CG206   ✅         context_manager
CG207   ✅         explicit_validation
CG402   ✅         strip_trailing_whitespace
CG403   ✅         drop_unused_import
CG101   —         -                           动态执行是不是业务上真需要，只能人判断
CG301   —         -                           函数太长要重新划分职责，这是设计决策
CG304   —         -                           docstring 得写作者的真实意图，机器编出来的注释没意义
…

共 27 条规则：9 条可自动修复。
```

和编排器、修复 Agent 之间走的是 JSON 契约。请求 payload：

| 键 | 必填 | 说明 |
| --- | --- | --- |
| `target` | 是（或 `workdir`） | 要审查的目录或文件 |
| `patterns` | 否 | 文件 glob 列表（也接受 `targets`；单个字符串会自动包成列表） |
| `blocking` | 否 | 阻断级别列表（也接受 `blocking_severities`） |
| `report_dir` | 否 | 给定则把 `review.json` / `review.md` 写进去并登记为 artifact |

响应 payload：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `mode` | str | `static` / `llm` |
| `blocking_severities` | list | 本次生效的阻断级别 |
| `findings` | list | 每条含 `finding_id`/`rule_id`/`severity`/`file`/`line`/`title`/`evidence`/`explanation`/`suggestion`/`source`/`fixable`/`status`/`note` |
| `counts` | dict | 五个级别键始终存在（无问题时为 0） |
| `total` / `blocking` | int | 问题总数 / 阻断级条数 |
| `passed` | bool | 门禁结论，等价于 `blocking == 0` |
| `fixable` / `needs_human` | int | 可自动修 / 需人工条数 |
| `score` | int | 0~100 健康分，只用于趋势对比 |
| `overall_risk` | str | 最严重的级别，无问题时为 `clean` |
| `summary` / `files_reviewed` | str / list | 结论摘要 / 被审查的文件名 |
| `diagnostics` | dict | `engine`/`codeguard_version`/`files`/`parallel`/`mock` |

一次真实往返。`job.json`：

```json
{
  "contract_version": "1.0", "agent": "review", "task_id": "review-r1",
  "run_id": "demo", "round": 1, "objective": "审查编码 Agent 产出的草案",
  "payload": { "target": "../coding-agent/worktree",
               "blocking": ["critical", "high"], "report_dir": "./reports/r1" }
}
```

```bash
python -m review_agent.worker --request-file job.json --response-file out.json   # 退出码 0
```

`out.json` 的关键片段（真实输出，findings 只留了两条）：

```json
{
  "agent": "review", "status": "ok",
  "summary": "审查 1 个文件，命中 15 条（1 critical、2 high、3 medium、5 low、4 info）；❌ 未通过门禁（3 条阻断级）；健康分 38",
  "payload": {
    "mode": "static", "blocking_severities": ["critical", "high"],
    "findings": [
      { "finding_id": "F701770B6", "rule_id": "CG105", "severity": "critical",
        "category": "security", "file": "user_store.py", "line": 8,
        "title": "疑似硬编码密钥：AUTH_TOKEN",
        "evidence": "AUTH_TOKEN = 'sk-l********'（已脱敏）",
        "source": "static", "fixable": true, "status": "open", "note": "" },
      { "finding_id": "FF737229D", "rule_id": "CG106", "severity": "high",
        "category": "security", "file": "user_store.py", "line": 13,
        "title": "SQL 语句由拼接/格式化构造（在 create_user 中）",
        "source": "static", "fixable": true, "status": "open", "note": "" }
    ],
    "counts": { "critical": 1, "high": 2, "medium": 3, "low": 5, "info": 4 },
    "total": 15, "blocking": 3, "passed": false,
    "fixable": 7, "needs_human": 8, "score": 38, "overall_risk": "critical",
    "files_reviewed": ["user_store.py"],
    "summary": "确定性规则引擎审查 1 个文件，命中 15 条问题；结果完全可复现，不含语义推断"
  },
  "artifacts": [
    { "kind": "review_report",   "path": "review.json", "media_type": "application/json", "description": "15 条发现" },
    { "kind": "review_markdown", "path": "review.md",   "media_type": "text/markdown",    "description": "人类可读审查报告" }
  ],
  "metrics": { "elapsed_seconds": 0.005, "findings": 15, "blocking": 3 },
  "diagnostics": { "engine": "codeguard.static_analysis", "parallel": 4, "mode": "static",
                   "codeguard_version": "1.0.0", "files": 1, "patterns": ["*.py"], "mock": false },
  "error": ""
}
```

F701770B6 是 CG105、user_store.py、归一化标题这三样做哈希的结果，行号不参与。行号在修复后会整体
位移，用它构造 ID 就永远对不上"这条到底修好没有"。这 15 条实测 ID 全部唯一。

## 项目结构

```
review-agent/
├── review_agent/
│   ├── agent.py           379 行  两种模式、门禁、Markdown 渲染、报告落盘、协议入口
│   ├── locate.py          190 行  codeguard 自动发现（4 级优先级 + 可执行报错）
│   ├── normalize.py       241 行  归一化、稳定 finding_id、评分、目标枚举
│   ├── fixability.py       87 行  可修复性对照表（9 可修 / 18 需人工 = 27 条）
│   ├── config.py          160 行  配置加载（环境变量 / .env / 校验 / 门禁口径 / 沿用 CODEGUARD_* 变量）
│   ├── protocol.py        333 行  契约信封（四仓库等价副本）
│   ├── worker.py          100 行  子进程 Worker（stdin/stdout 单次 JSON 往返，含 UTF-8 重配）
│   ├── cli.py             201 行  命令行界面（review / doctor / rules / info）
│   ├── errors.py           23 行  异常体系（含 CodeGuardNotFound）
│   └── __init__.py / __main__.py  对外 API 与入口
├── tests/                 585 行  41 个用例，全部离线
│   ├── test_agent.py      227 行  19 例：定位、门禁、模式、协议入口、报告落盘、CLI 退出码
│   ├── test_normalize.py  163 行  15 例：字段映射、ID 稳定性、排序、评分、目标枚举
│   ├── test_fixability.py  65 行    7 例：表自洽性（两表不重叠、每行都有说明）
│   └── support.py         129 行  临时目录 / 样例源码（BUGGY / CLEAN）/ 断言工具
├── run_tests.py           136 行  零依赖测试运行器
├── pyproject.toml          31 行  dependencies = []（codeguard 靠附近发现）
├── .env.example            35 行  环境变量示例
├── .gitignore              56 行  .env / reports/ / job.json 在忽略列表内
├── LICENSE                        MIT
├── Design.md                      设计文档
└── README.md                      本文件
```

分层大致是：cli.py 和 worker.py 是接口层，agent.py 是编排层，locate.py 找 codeguard，
normalize.py 和 fixability.py 在判定层，config.py、protocol.py、errors.py 是横切的支撑。

## 设计取舍

| 决策 | 选择 | 理由，以及放弃了什么 |
| --- | --- | --- |
| 依赖 codeguard 的方式 | 自动发现，不写进 dependencies | 评审者不必先装，不受虚拟环境和网络限制。代价是"能不能找到"成了运行期问题，所以得配 doctor 和可执行的报错 |
| 数据结构 | 归一化成自有契约 | 下游只依赖几个字段，上游改字段不波及工作流。代价是多一层转换，还要测试守住映射 |
| finding_id | 哈希（规则+文件+归一化标题），不含行号 | 行号会位移，用它做 ID 就无法跨轮次闭环。代价是标题含函数名，重命名函数会让 ID 变 |
| 门禁口径 | 配置项 | 早期只挡 critical、发版前收紧到 high，这是策略不是属性。代价是必须显式配置（空值被拒绝） |
| 可修复性判定 | 审查侧给结论，不让修复 Agent 试错 | 报告一开始就能说"7 条能修、8 条要人看"。代价是这张表要人工维护，还得和修复侧策略同步 |
| 报告渲染 | static 用自有渲染，llm 复用 codeguard 的 | 契约层要能独立表述结论，llm 模式则要保留模型的语义判断。代价是两种模式报告样式不完全一致 |
| 并行 | static 按文件 ThreadPoolExecutor | 规则引擎是 CPU 轻量的纯函数，按文件并行简单有效。代价是没有进程级隔离 |

## 测试与配置

```bash
python run_tests.py            # 41/41
python run_tests.py -v         # 逐个列出用例名
python run_tests.py normalize  # 只跑文件名含 normalize 的用例（15/15）
```

```
测试套件  review-agent  (Python 3.14.7)
目录：D:\软件工程实践\homework2\review-agent\tests
──────────────────────────────────────────────────────────────────────

结果：41/41 通过，全部通过
```

41 个用例都不访问网络、不需要 API Key，也不需要安装 codeguard。它们靠 locate.py 在本机找到隔壁的
codeguard 源码，`test_codeguard_is_locatable` 就是这个前提的显式断言，找不到时会提示把两个仓库放在
同一目录下。

test_agent.py 里有两条是真实缺陷逼出来的回归测试：`test_worker_stdout_mode_survives_non_ascii`
真的起子进程跑 Worker 的 stdout 模式，断言 stdout 是合法 UTF-8；`test_cli_missing_path_returns_2`
直接调 `cli.main()`，断言路径不存在时返回 2，不会抛 traceback。详见 Design.md §8。

test_fixability.py 那 7 个用例守的是表本身的自洽性：两表不重叠，每行都有策略名或理由，未知规则判不可
修，策略名是 snake_case（要和修复 Agent 的策略注册表对得上）。这张表是审查和修复共用的，两边对不上
就是静默失败。

配置的优先级是命令行参数 > 环境变量 > `.env` > 默认值，前缀 `REVIEW_`：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REVIEW_MODE` | `static` | `static` / `llm` |
| `REVIEW_BLOCKING` | `critical,high` | 阻断级别，逗号分隔；含未知级别会被校验拒绝 |
| `REVIEW_PATTERNS` | `*.py` | 待审查文件 glob，逗号分隔 |
| `REVIEW_PARALLEL` | `4` | static 模式并行文件数（≥1） |
| `REVIEW_MAX_FILES` | `200` | 单次最多处理多少文件，防止误指到超大目录 |
| `REVIEW_RULES` | 空 | static 模式只跑指定规则 |
| `REVIEW_CODEGUARD_PATH` | 空 | codeguard 源码根目录（一般不需要填） |
| `REVIEW_API_KEY` | 空 | `mode=llm` 时的 Key；也可用 Homework 1 的 `CODEGUARD_API_KEY` |
| `REVIEW_BASE_URL` / `REVIEW_MODEL` | `https://api.deepseek.com` / `deepseek-chat` | 透传给 codeguard |
| `REVIEW_TEMPERATURE` | `0.2` | 透传给 codeguard |
| `REVIEW_MAX_ITERATIONS` | `10` | codeguard Agent 闭环的最大迭代轮数 |
| `REVIEW_TIMEOUT` | `120` | 单次请求超时（秒） |
| `REVIEW_MOCK` | `0` | 置 `1` 用离线脚本化模型 |

`HW2_PACKAGES_HOME` 没有前缀，用来给 locate.py 追加包搜索根目录，多个用 `os.pathsep` 分隔。
完整示例见 `.env.example`。

Homework 1 的三个变量也能直接用：`REVIEW_*` 没配时，`ReviewConfig.from_env()` 会回退去读
`CODEGUARD_API_KEY` / `CODEGUARD_BASE_URL` / `CODEGUARD_MODEL`。这样同一个 `.env` 两个项目都能用，
不只写在文档里，是真的接上了：

```powershell
$env:CODEGUARD_MODEL = "qwen-plus"
$env:CODEGUARD_API_KEY = "sk-test-key-1234567890"
python -m review_agent info
```

```
配置：mode=static  blocking=critical,high  patterns=*.py  parallel=4  model=qwen-plus  api_key=sk-test-...7890
```

## 常见问题

为什么装完还要找 codeguard？因为这个仓库有意不把 codeguard 写进依赖。放在同一目录（推荐）、
`--codeguard-path`、`HW2_PACKAGES_HOME`、或者真去 `pip install -e ../codeguard`，四选一。

报"找不到 codeguard 包"？先跑 `python -m review_agent doctor`，它会打印搜索到的候选根目录数量，
失败信息里也直接给了四种解决方案。

没有 API Key 能用吗？能。默认 static 模式完全不联网，`--mode llm --mock` 可以离线走一遍完整的
Agent 闭环。

static 和 llm 怎么选？循环里反复跑、要可复现、要快用 static；收敛前做深度检查、想找语义级问题用
llm。两者共用同一套归一化和门禁计算，passed 口径一致。

健康分 38 是什么意思？按级别扣分（critical −25 / high −10 / medium −4 / low −1 / info 0），下限 0。
只能看趋势，不能当合格证明，合不合格看 passed。

为什么 4 条 CG307 也算通过门禁？因为 blocking 默认只有 critical 和 high。想让 info 也阻断，
配 `--blocking info`，机制允许但不推荐。

目标里有很多非代码文件会怎样？默认只扫 `*.py`，自动排除 `.git`、`__pycache__`、`.venv`、
`node_modules`、`build`、`dist` 这些目录，换类型用 `--pattern`。

目录里文件太多会怎样？超过 max_files（默认 200）直接报错"请缩小目标范围或调大 max_files"，
不会慢慢跑。

修完怎么确认修好了？再跑一次审查，用 finding_id 对比：上一轮 open 的 ID 在新结果里消失就算修好了。
这也是稳定 ID 的用处。

别的仓库都在这里：

| 仓库 | 说明 |
| --- | --- |
| <https://github.com/0x697551b0/hw2-coding-agent> | 编码 Agent |
| <https://github.com/0x697551b0/hw2-review-agent> | 本仓库：审查 Agent |
| <https://github.com/0x697551b0/hw2-fix-agent> | 修复 Agent |
| <https://github.com/0x697551b0/hw2-code-review-workflow> | 工作流编排器 |
| <https://github.com/0x697551b0/codeguard> | Homework 1：CodeGuard |

MIT 许可，见 LICENSE。设计细节（定位策略、稳定 ID、可修复性判据、踩过的工程问题）在 Design.md。
