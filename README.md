# ReviewAgent

> 把 Homework 1 的 **CodeGuard** 接入工作流，产出**契约化**的审查结论与**可解释的门禁判定**。
>
> **源码仓库**：<https://github.com/0x697551b0/hw2-review-agent>　**作者**：刘浩天 / 2412190219

`v1.0.0` · Python `3.10+` · 本仓库自身 **0** 第三方依赖 · 测试 **41/41**

ReviewAgent 是「编码 → 审查 → 修复」工作流的**第二个节点**。它本身刻意做得很薄——审查能力全部
来自 Homework 1 的 codeguard，本仓库提供的是 codeguard 单独做不到的四件事：

| 能力 | 说明 |
| --- | --- |
| **可定位** | 不要求 `pip install`。四个仓库放同一目录即可自动发现 codeguard（另有 `--codeguard-path` 与 `HW2_PACKAGES_HOME` 两条显式通道） |
| **可契约化** | 把 codeguard 的 `ReviewReport` 归一化成工作流契约，并给每条发现分配**跨轮次稳定**的 `finding_id` |
| **可门禁** | "哪些级别算不合格"是显式配置项，输出 `passed` 与逐条判定明细，供编排器做条件分支 |
| **可并行** | `static` 模式下按文件 fan-out |

## 在工作流里的位置

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

上游是**编码 Agent**（或任何一段代码），下游是**修复 Agent 与编排器**。本 Agent 的关键产出不是
"报告好看"，而是那个 `passed` 布尔值和一组**能被下游精确引用**的 `finding_id`。

## 目录

- [快速开始](#快速开始) · [效果预览](#效果预览) · [命令行用法](#命令行用法) · [项目结构](#项目结构)
- [架构设计](#架构设计) · [与上下游 Agent 的契约](#与上下游-agent-的契约) · [测试与自检](#测试与自检)
- [配置项](#配置项) · [设计取舍](#设计取舍) · [常见问题](#常见问题) · [许可与相关仓库](#许可与相关仓库)

---

## 快速开始

- Python **3.10+**（开发与验证环境 3.14.7）
- 本仓库**不通过 pip 依赖** codeguard（`pyproject.toml` 里 `dependencies = []`）：靠"放在附近"自动发现

### 目录布局（关键）

```
软件工程实践/
├── codeguard/                 ← Homework 1 的仓库（含 codeguard/__init__.py）
└── homework2/
    ├── coding-agent/
    ├── review-agent/          ← 本仓库
    ├── fix-agent/
    └── code-review-workflow/
```

codeguard 只要在**当前工作目录 / 本仓库 / 本仓库上级目录及其直接子目录**的 5 层范围内就能找到。
本项目开发时的实际发现结果：

```
$ python -m review_agent doctor
【codeguard 定位】
  ✅ 可用    版本：1.0.0
     来源：D:\软件工程实践\codeguard\codeguard\__init__.py
```

### 30 秒上手（无需 API Key）

```bash
# 1) 隔壁仓库生成草案（离线模板引擎，不需要 Key）
cd ../coding-agent
python -m coding_agent generate examples/spec_user_store.md --out ./worktree --overwrite

# 2) 回到本仓库审查它
cd ../review-agent
python -m review_agent review ../coding-agent/worktree
```

需要安装（可选）：`pip install -e .`；不装也能用 `python -m review_agent`。

### LLM 模式（可选）

```bash
python -m review_agent review ../coding-agent/worktree --mode llm --mock   # 无 Key 走离线闭环
$env:CODEGUARD_API_KEY = "sk-xxxxxxxx"                                    # 沿用 Homework 1 的变量名
python -m review_agent review ../coding-agent/worktree --mode llm         # 真实模型
```

---

## 效果预览

### 审查 `realistic` 草案（37 行 `user_store.py`，真实输出，节选）

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

- `user_store.py:11` **缺少文档字符串：create_user（在 create_user 中）**：文档字符串必须描述真实意图，机器生成的注释没有价值
- `user_store.py:11` **缺少返回值类型注解：create_user（在 create_user 中）**：返回值类型需作者确认，猜错注解比没有注解更糟
- …

────────────────────────────────────────────────────────────────────
结论：审查 1 个文件，命中 15 条（1 critical、2 high、3 medium、5 low、4 info）；❌ 未通过门禁（3 条阻断级）；健康分 38
耗时：0.01s    可自动修 7 条 / 需人工 8 条
```

这 15 条正对应编码 Agent `realistic` 档位埋下的 7 类问题：`CG105`/`CG106`/`CG203`/`CG207`/`CG202`/
`CG206`/`CG403` 各 1 条，外加 4 处 `CG304`（缺 docstring）与 4 处 `CG307`（缺返回值注解）。

### 审查 `clean` 草案（46 行，门禁通过）

需要先让编码 Agent 产出干净版本：

```bash
cd ../coding-agent
python -m coding_agent generate examples/spec_user_store.md --out ./worktree-clean --profile clean --overwrite
cd ../review-agent
python -m review_agent review ../coding-agent/worktree-clean
```

```
- 问题总数：4
- 分布：critical 0 / high 0 / medium 0 / low 0 / info 4

结论：审查 1 个文件，命中 4 条（4 info）；✅ 通过门禁；健康分 100
```

剩下的 4 条全是 `CG307`（缺返回值类型注解），`info` 级不阻断——这正是 `--profile clean` 存在的
意义：验证"门禁能通过"这条分支不是永远为假。

### `rules`：可修复性对照表（节选）

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
CG101   —         -                           需要判断这段字符串的真实语义（是否必须动态执行），属业务决策
CG301   —         -                           函数过长需要重新划分职责，属设计决策
CG304   —         -                           文档字符串必须描述真实意图，机器生成的注释没有价值
…

共 27 条规则：9 条可自动修复。
```

---

## 命令行用法

| 子命令 | 作用 | 需要 API Key |
| --- | --- | --- |
| `review <目标>` | **核心功能**：审查目录或单个文件 | 仅 `--mode llm`（`--mock` 除外） |
| `doctor` | 诊断 codeguard 定位情况与当前配置 | 否 |
| `rules` | 打印 27 条规则的可修复性对照表 | 否 |
| `info` | 打印版本与配置 | 否 |

### `review` 参数

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

实测门禁口径的差异（同一份代码）：

| 口径 | 结论 |
| --- | --- |
| `--blocking critical,high`（默认） | ❌ 未通过门禁（3 条阻断级） |
| `--blocking critical` | ❌ 未通过门禁（1 条阻断级） |
| `--blocking critical --blocking high --blocking medium` | ❌ 未通过门禁（6 条阻断级） |

### 退出码约定

| 退出码 | 含义 |
| --- | --- |
| `0` | 审查完成且（若指定 `--fail-on-blocking`）通过门禁 |
| `1` | 指定 `--fail-on-blocking` 但存在阻断级问题；`doctor` 找不到 codeguard 时也返回 1 |
| `2` | 用法/配置错误（mode 非法、blocking 含未知级别、`--mode llm` 缺 Key、**目标路径不存在**） |

目标路径不存在时输出一行 `❌ 找不到路径：审查目标不存在：<路径>` 并返回 **2**：

```
$ python -m review_agent review ./definitely-not-here
❌ 找不到路径：审查目标不存在：D:\软件工程实践\homework2\review-agent\definitely-not-here
$ echo $?
2
```

这条分支曾经漏掉过（`FileNotFoundError` 是内置异常，不会被 `ReviewAgentError` 捕获，于是路径打错时
用户看到的是一整屏堆栈 + 退出码 1）。现已修复，并由回归测试
`test_cli_missing_path_returns_2` 守护 —— 详见 [`Design.md`](Design.md) §8.4。

---

## 项目结构

```
review-agent/
├── review_agent/
│   ├── agent.py           391 行  ★ 两种模式、门禁、Markdown 渲染、报告落盘、协议入口
│   ├── locate.py          202 行  ★ codeguard 自动发现（4 级优先级 + 可执行报错）
│   ├── normalize.py       244 行  ★ 归一化、稳定 finding_id、评分、目标枚举
│   ├── fixability.py       97 行  ★ 可修复性对照表（9 可修 / 18 需人工 = 27 条）
│   ├── config.py          166 行  配置加载（环境变量 / .env / 校验 / 门禁口径 / 沿用 CODEGUARD_* 变量）
│   ├── protocol.py        347 行  Agent 契约信封（四仓库等价副本）
│   ├── worker.py          100 行  子进程 Worker（stdin/stdout 单次 JSON 往返，含 UTF-8 重配）
│   ├── cli.py             205 行  命令行界面（review / doctor / rules / info）
│   ├── errors.py           24 行  异常体系（含 CodeGuardNotFound）
│   └── __init__.py / __main__.py  对外 API 与入口
├── tests/                 590 行  41 个用例，全部离线
│   ├── test_agent.py      227 行  19 例：定位、门禁、模式、协议入口、报告落盘、CLI 退出码
│   ├── test_normalize.py  165 行  15 例：字段映射、ID 稳定性、排序、评分、目标枚举
│   ├── test_fixability.py  66 行    7 例：表自洽性（两表不重叠、每行都有说明）
│   └── support.py         131 行  临时目录 / 样例源码（BUGGY / CLEAN）/ 断言工具
├── run_tests.py           136 行  零依赖测试运行器
├── pyproject.toml          31 行  dependencies = []（codeguard 靠附近发现）
├── .env.example            35 行  环境变量示例
├── .gitignore              53 行  .env / reports/ / job.json 在忽略列表内
├── LICENSE                        MIT
├── Design.md                      ★ 设计文档
└── README.md                      本文件
```

---

## 架构设计

```
接口层   cli.py（review/doctor/rules/info） · worker.py（子进程协议）
              ▼
编排层   agent.py :: ReviewAgent —— 目标枚举 → 审查 → 归一化 → 门禁 → 渲染 → 归档
              ▼                            ▼
适配层   locate.py（找 codeguard）    判定层  normalize.py（归一化 + 稳定 ID）
                                            fixability.py（可修 / 需人工）
              ▼
外部依赖  codeguard（Homework 1）
          static 模式：static_analysis.CodeAnalyzer（按文件并行）
          llm   模式：CodeReviewAgent + report.render_markdown
   横切：config.py（配置 + 门禁口径） · protocol.py（契约） · errors.py
```

| 模块 | 职责 | 明确不负责 |
| --- | --- | --- |
| `agent.py` | 目标枚举、模式分派、并行审查、门禁、渲染、归档、协议入口 | 不实现规则、不做字段映射细节 |
| `normalize.py` | `Finding` → `FindingContract`、稳定 ID、评分、目标枚举 | 不调用 codeguard |
| `fixability.py` | 规则 →（可修+策略名）/（需人工+原因） | 不执行修复 |
| `locate.py` | codeguard 的 4 级定位、可执行的失败提示、`doctor` 诊断 | 不 import 业务代码 |

关键设计点：

| 设计点 | 做法 | 解决了什么 |
| --- | --- | --- |
| **不装 codeguard，而是找它** | 按"已安装 → 环境变量 → 显式路径 → 附近目录"4 级发现，找到后只改 `sys.path` | 评审者不必先 `pip install -e ../codeguard`；不写 site-packages、退出即失效 |
| **稳定 finding_id** | `规则 + 文件 + 归一化标题` 的 SHA-1 前 8 位，**行号只是展示信息** | 修完代码行号会整体位移；用行号构造 ID 就永远对不上"这条到底修好没有" |
| **归一化而非透传** | codeguard 输出先翻译成 `FindingContract` | 下游只依赖六个字段，不被上游产品的字段演进牵着走 |
| **门禁口径是配置** | `blocking` 是 `ReviewConfig` 字段，可被 `--blocking` 覆盖 | "high 及以上算不合格"是团队策略，不是审查工具的固有属性 |
| **可修复性表放审查侧** | 同时给出"能不能修"和"为什么不能" | 报告一开始就能说"15 条里 7 条能自动修、8 条要人看"，而不是等修复 Agent 试完 |
| **两种模式分工** | `static` 快、零成本、可复现；`llm` 慢、要 Key、结论更深 | 循环里反复跑用 static，收敛前深度检查用 llm；两者共用同一套归一化与门禁口径 |

细节见 [`Design.md`](Design.md)。

---

## 与上下游 Agent 的契约

请求 `payload` 字段：

| 键 | 必填 | 说明 |
| --- | --- | --- |
| `target` | ✅（或 `workdir`） | 要审查的目录或文件 |
| `patterns` | — | 文件 glob 列表（也接受 `targets`；单个字符串会自动包成列表） |
| `blocking` | — | 阻断级别列表（也接受 `blocking_severities`） |
| `report_dir` | — | 给定则把 `review.json` / `review.md` 写进去并登记为 artifact |

响应 `payload` 字段：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `mode` | str | `static` / `llm` |
| `blocking_severities` | list | 本次生效的阻断级别 |
| `findings` | list | 每条含 `finding_id`/`rule_id`/`severity`/`file`/`line`/`title`/`evidence`/`explanation`/`suggestion`/`source`/`fixable`/`status`/`note` |
| `counts` | dict | 五个级别键始终存在（无问题时为 0） |
| `total` / `blocking` | int | 问题总数 / 阻断级条数 |
| `passed` | bool | **门禁结论**：`blocking == 0` |
| `fixable` / `needs_human` | int | 可自动修 / 需人工条数 |
| `score` | int | 0~100 健康分，**只用于趋势对比** |
| `overall_risk` | str | 最严重的级别，无问题时为 `clean` |
| `summary` / `files_reviewed` | str / list | 一句话结论 / 被审查的文件名 |
| `diagnostics` | dict | `engine`/`codeguard_version`/`files`/`parallel`/`mock` |

### 真实往返

`job.json`：

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

`out.json` 关键片段（真实输出，`findings` 只保留两条）：

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

> **`finding_id` 是可被下游引用的稳定键。** `F701770B6` 由 `CG105|user_store.py|疑似硬编码密钥auth_token`
> 哈希而来，行号变化不改变它——这是"发现 → 修复 → 复验"能闭环的前提。实测这 15 条的 ID 全部唯一。

---

## 测试与自检

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

**全部 41 个用例不访问网络、不需要 API Key、也不需要安装 codeguard**——它们靠 `locate.py` 在同一台
机器上发现隔壁的 codeguard 源码（`test_codeguard_is_locatable` 就是这个前提的显式断言；找不到时它
会给出可读的失败原因："请把 Homework 1 的 codeguard 仓库与本仓库放在同一目录下"）。

`test_agent.py` 里有两条**由真实缺陷逼出来的回归测试**：`test_worker_stdout_mode_survives_non_ascii`
（真的起子进程跑 Worker 的 stdout 模式，断言 stdout 是合法 UTF-8）与 `test_cli_missing_path_returns_2`
（直接调 `cli.main()`，断言路径不存在时返回 2 而不是抛 traceback）。详见 [`Design.md`](Design.md) §8。

`test_fixability.py` 的 7 个用例守的不是本仓库的行为，而是**这张表本身的自洽性**：两表不重叠
（同一规则不能既可修又不可修）、每行都有策略名或理由、未知规则保守判为不可修、策略名是 snake_case
（必须与修复 Agent 的策略注册表对得上）。因为它是审查与修复之间的共享词汇表，两边对不上就是静默失败。

---

## 配置项

优先级：**命令行参数 > 环境变量 > `.env` > 默认值**，前缀 `REVIEW_`。

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

`HW2_PACKAGES_HOME`（无 `REVIEW_` 前缀）用于给 `locate.py` 追加包搜索根目录，多个用 `os.pathsep`
分隔。完整示例见 [`.env.example`](.env.example)。

另外，**Homework 1 的三个变量可以直接复用**：当 `REVIEW_*` 没配时，`ReviewConfig.from_env()` 会回退
去读 `CODEGUARD_API_KEY` / `CODEGUARD_BASE_URL` / `CODEGUARD_MODEL`。这样同一个 `.env` 能同时给
codeguard 和本 Agent 使用 —— 不只是写在文档里，而是真的接上了（实测）：

```bash
# bash / zsh
export CODEGUARD_MODEL=qwen-plus
export CODEGUARD_API_KEY=sk-test-key-1234567890
python -m review_agent info
```

```powershell
# Windows PowerShell
$env:CODEGUARD_MODEL = "qwen-plus"
$env:CODEGUARD_API_KEY = "sk-test-key-1234567890"
python -m review_agent info
```

```
配置：mode=static  blocking=critical,high  patterns=*.py  parallel=4  model=qwen-plus  api_key=sk-test-...7890
```

---

## 设计取舍

| 决策 | 选择 | 理由，以及放弃了什么 |
| --- | --- | --- |
| 依赖 codeguard 的方式 | **自动发现**，不写进 `dependencies` | 评审者不必先装，不受虚拟环境/网络限制。代价是"能不能找到"成了运行期问题，所以必须配 `doctor` 与可执行报错 |
| 数据结构 | **归一化成自有契约** | 下游只依赖六个字段，上游改字段不波及工作流。代价是多一层转换，且需要测试守住映射 |
| `finding_id` | **哈希（规则+文件+归一化标题）**，不含行号 | 行号会位移，用它做 ID 就无法跨轮次闭环。代价是标题含函数名，重命名函数会让 ID 变 |
| 门禁口径 | **配置项** | 早期只挡 critical、发版前收紧到 high，这是策略不是属性。代价是必须显式配置（空值被拒绝） |
| 可修复性判定 | **审查侧给结论**，不让修复 Agent 试错 | 报告一开始就能说"7 条能修、8 条要人看"。代价是这张表要人工维护并与修复侧策略同步 |
| 报告渲染 | `static` 用**自有渲染**，`llm` 复用 codeguard 的 | 契约层应能独立表述结论；`llm` 模式则要保留模型的语义结论。代价是两种模式的报告样式不完全一致 |
| 并行 | `static` 按文件 `ThreadPoolExecutor` | 规则引擎是 CPU 轻量 + 纯函数，按文件并行简单有效。代价是没有进程级隔离 |

---

## 常见问题

| 问题 | 回答 |
| --- | --- |
| 为什么 `pip install -e .` 之后还要找 codeguard？ | 本仓库刻意不把 codeguard 写进依赖。把两个仓库放同一目录（推荐）、`--codeguard-path`、`HW2_PACKAGES_HOME`、或真去 `pip install -e ../codeguard`，四选一 |
| 报"找不到 codeguard 包"怎么办？ | 先跑 `python -m review_agent doctor`，它会打印搜索到的候选根目录数量；失败信息里也直接给了 4 种解决方案 |
| 没有 API Key 能用吗？ | 能。默认 `static` 模式完全不联网；`--mode llm --mock` 可离线走一遍 codeguard 的完整 Agent 闭环 |
| `static` 和 `llm` 该选哪个？ | 循环里反复跑、要可复现、要快 → `static`；收敛前做深度检查、想看语义级问题 → `llm`。两者共用同一套归一化与门禁计算，`passed` 口径一致 |
| 健康分 38 是什么意思？ | 按级别扣分（critical −25 / high −10 / medium −4 / low −1 / info 0），下限 0。**只用于趋势对比，不能当合格证明**——合格与否看 `passed` |
| 为什么 4 条 `CG307` 也算"通过门禁"？ | 因为 `blocking` 默认只有 `critical,high`。想让 info 也阻断，配 `--blocking info`（机制允许，但不推荐） |
| 目标里有很多非代码文件会怎样？ | 默认只扫 `*.py`，且自动排除 `.git`/`__pycache__`/`.venv`/`node_modules`/`build`/`dist` 等目录；换类型用 `--pattern` |
| 目录里文件太多会怎样？ | 超过 `max_files`（默认 200）直接报错"请缩小目标范围或调大 max_files"，而不是慢慢跑 |
| 修完之后怎么确认修好了？ | 再跑一次审查，用 `finding_id` 对比：上一轮 `open` 的 ID 在新结果里消失即视为已修。这就是稳定 ID 的用途 |

---

## 许可与相关仓库

MIT 许可，见 [`LICENSE`](LICENSE)。

| 仓库 | 说明 |
| --- | --- |
| <https://github.com/0x697551b0/hw2-coding-agent> | 编码 Agent |
| <https://github.com/0x697551b0/hw2-review-agent> | **本仓库**：审查 Agent（复用 Homework 1 的 codeguard） |
| <https://github.com/0x697551b0/hw2-fix-agent> | 修复 Agent |
| <https://github.com/0x697551b0/hw2-code-review-workflow> | 工作流编排器 |
| <https://github.com/0x697551b0/codeguard> | Homework 1：CodeGuard 代码审查 Agent（本 Agent 的审查能力来源） |

相关文档：[`Design.md`](Design.md) —— 定位策略、稳定 ID、可修复性判据、工程问题记录。
