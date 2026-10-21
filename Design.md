# ReviewAgent 设计文档

> 软件工程实践 Homework 2 · 代码审查工作流 · 节点 ②「审查」

写这份文档是为了把 ReviewAgent 的边界、几个关键决定背后的理由，以及开发中真碰到的问题记下来。
文里的数字和输出都来自代码和实机运行。

## 目录

[1. 需求与目标](#1-需求与目标) · [2. 总体结构](#2-总体结构) · [3. 核心机制](#3-核心机制) ·
[4. 数据结构与契约](#4-数据结构与契约) · [5. 失败处理](#5-失败处理) · [6. 安全](#6-安全) ·
[7. 测试](#7-测试) · [8. 开发中碰到的工程问题](#8-开发中碰到的工程问题) · [9. 局限与改进](#9-局限与改进)

## 1. 需求与目标

Homework 1 已经交了一个能独立工作的审查 Agent（CodeGuard，27 条规则、110 个测试）。Homework 2 要
把它变成多 Agent 工作流里的一个节点。重点不在重写，而在换一套运行契约：从"一个命令行工具"变成
"一个能被编排、被引用、被复验的处理单元"。

要做的：

| 目标 | 具体含义 |
| --- | --- |
| 复用而不重写 | 审查能力全部来自 codeguard，本仓库不加任何检测规则 |
| 可定位 | 不要求预先安装 codeguard；四个仓库放同一目录就能跑 |
| 可契约化 | 输出归一化成工作流契约，每条发现拿到跨轮次稳定的 `finding_id` |
| 可门禁 | 输出 `passed` 和逐条判定明细，口径由配置决定 |
| 可闭环 | 下游修复 Agent 按 `finding_id` 引用"这条我修了"，下一轮复审验证它是否消失 |
| 可并行 | `static` 模式按文件 fan-out |

不做的：

| 不做 | 为什么 |
| --- | --- |
| 不新增审查规则 | 规则是 codeguard 的资产。在适配层加规则会把审查能力拆到两个仓库，两边的规则集慢慢就对不上了 |
| 不执行修复 | 修复是 ③ 的职责，审查侧只判断"可不可自动修"，不碰代码 |
| 不修改被审查文件 | 除 `--report-dir` 外只读。审查过程改代码，"审查"和"修复"的边界就没了 |
| 不把 codeguard 写进 `dependencies` | 见 §3.1 |
| 不硬编码门禁口径 | 见 §3.5 |
| 不留审查历史 | 每轮独立；"哪条修好了"由编排器按 `finding_id` 比对，本 Agent 不存状态 |
| 不编造缺失信息 | `static` 模式没有语义结论，报告就写"结果完全可复现，不含语义推断"，不冒充深度分析 |

成功标准：

```bash
python -m review_agent review ../coding-agent/worktree        # 15 条，门禁不通过，健康分 38
python -m review_agent review ../coding-agent/worktree-clean  # 4 条 info，门禁通过，健康分 100
python -m review_agent doctor                                 # codeguard 可定位
python run_tests.py                                           # 41/41 通过
```

前三条都不需要 API Key，也不需要装过 codeguard。

## 2. 总体结构

```
┌──────────────────────────────────────────────────────────────────────┐
│ 接口层  cli.py（review/doctor/rules/info）  worker.py（子进程协议）    │
└─────────────────────────────────┬────────────────────────────────────┘
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 编排层  agent.py :: ReviewAgent                                       │
│   目标枚举 → static 并行 / llm 闭环 → 归一化 → 门禁 → 渲染 → 归档      │
│   handle()：协议入口，任何异常 → status=failed 信封                    │
└────────────┬─────────────────────────────────────┬───────────────────┘
             ▼                                     ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│ 适配层  locate.py（找 codeguard）│  │ 判定层  normalize.py（归一化+ID） │
│                                │  │         fixability.py（可修复性）│
└────────────┬───────────────────┘  └──────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 外部依赖  codeguard（Homework 1，非 pip 安装）                         │
│   static 模式：codeguard.static_analysis.CodeAnalyzer（按文件并行）    │
│   llm   模式：codeguard.CodeReviewAgent + report.render_markdown       │
└──────────────────────────────────────────────────────────────────────┘

  横切：config.py（配置 + 门禁口径） · protocol.py（契约，四仓库等价副本） · errors.py
```

依赖方向：

```
cli.py ──▶ agent.py ──▶ locate.py ──▶ (sys.path 注入) ──▶ codeguard 包
worker.py ─┘        ├──▶ normalize.py ──▶ fixability.py
                    └──▶ protocol.py
```

- 对 codeguard 的所有依赖都收在 `locate.py` 里，其余模块只拿到一个 `ModuleType`，不关心它从哪来；
- `agent.py` 是唯一碰 codeguard API 的地方（`static_analysis.CodeAnalyzer`、`CodeReviewAgent`、
  `report.render_markdown`、`Config`），它改 API 时只用改这一处；
- `normalize.py` 和 `fixability.py` 不认识 codeguard，只处理 dict 或带 `to_dict()` 的对象，所以能
  完全离线地做单元测试。

| 模块 | 行数 | 职责 | 不负责 |
| --- | ---: | --- | --- |
| `agent.py` | 379 | 目标枚举、模式分派、并行审查、门禁、渲染、归档、协议入口 | 不实现规则、不做字段映射 |
| `normalize.py` | 241 | `Finding` → `FindingContract`、稳定 ID、评分、目标枚举 | 不调用 codeguard |
| `fixability.py` | 87 | 规则 →（可修+策略名）/（需人工+原因） | 不执行修复 |
| `locate.py` | 190 | 4 级定位、可执行的失败提示、`doctor` 诊断数据 | 不 import 业务代码 |
| `config.py` | 160 | 环境变量/`.env`、门禁口径、校验、沿用 `CODEGUARD_*` 变量 | 不做审查逻辑 |
| `protocol.py` | 333 | 四仓库共享的契约信封、严重级工具、`FindingContract` | 无业务语义 |
| `cli.py` | 201 | 4 个子命令、退出码、UTF-8 控制台修复 | 不含业务逻辑 |
| `worker.py` | 100 | 一次 JSON 往返、退出码语义、stdout UTF-8 重配 | 不重复实现业务 |
| `errors.py` | 23 | 异常体系（`CodeGuardNotFound` 单独成类） | — |

## 3. 核心机制

### 3.1 为什么不装 codeguard，而是去找它

Homework 2 是 4 个独立的 Git 仓库。评审者的操作路径大概就是"clone 四个仓库到一个目录，看一眼
README，跑一条命令"。

| 做法 | 后果 |
| --- | --- |
| 写进 `dependencies = ["codeguard"]` | 这个包不在 PyPI 上，`pip install` 直接失败 |
| 文档里写"请先 `pip install -e ../codeguard`" | 多一步；网络不通、虚拟环境不对、系统 Python 被锁，哪一样都会让"一条命令跑起来"变成奢望 |
| 自动发现（本项目） | 四个仓库放同一目录即可；找到后只把那一层插进 `sys.path`，不写 site-packages、不改文件系统、退出即失效 |

`_candidate_roots()` 的优先顺序：

```
1. 已装好的包            → importlib.import_module("codeguard") 成功就用它
2. --codeguard-path 显式 → 并把它的父目录也作为候选（允许传"包目录本身"）
3. HW2_PACKAGES_HOME     → os.pathsep 分隔的额外搜索根
4. 附近目录              → 从 cwd 与本仓库上溯最多 5 级，每级本身 + 它的直接子目录
```

第 4 条覆盖面最广。开发时的实际布局是 `软件工程实践/codeguard` 和
`软件工程实践/homework2/review-agent`，从后者上溯 2 级到达 `软件工程实践`，直接子目录里就有
`codeguard`，所以从哪个仓库执行命令都能找到隔壁。实测来源：
`D:\软件工程实践\codeguard\codeguard\__init__.py`。

这么做的代价也实打实存在。一，"能不能找到"从安装期问题变成了运行期问题，所以必须有诊断入口，这就是
`doctor` 存在的原因；`CodeGuardNotFound` 的消息里也把四种解法列出来，一段写着"任选一种方式解决：
1) … 2) … 3) … 4) …"的报错，比一句 `ModuleNotFoundError: No module named 'codeguard'` 有用得多。
二，搜索有代价：每次启动要枚举几层目录的直接子目录，候选数量在 `doctor` 的 `search_roots` 里能
看到。对这个规模的目录可以接受，但确实是个折中。三，会有"魔法感"：为什么突然就能 import 隔壁的
包？缓解办法是把策略全写在一个 190 行的文件里，模块 docstring 里解释清楚。藏着不说的魔法是坏事，
说清楚了就是工程折中。

### 3.2 为什么必须归一化，不能直接透传

codeguard 的 `Finding` 是为它自己的报告服务的，考虑的是展示：中文按钮、折行、规则手册附录。工作流
要的是另一组东西：

| 需求 | codeguard 原生 | 本项目的 `FindingContract` |
| --- | --- | --- |
| 跨轮次引用同一条问题 | 无 | `finding_id`（稳定哈希） |
| 下游判断能不能自动修 | 无 | `fixable` + `note`（不可修时的原因） |
| 修复状态回写 | 无 | `status`（`open`/`fixed`/`deferred`/`rejected`） |
| 统一严重级排序与统计 | 内部枚举 | `severity` 字符串 + `SEVERITY_ORDER` |
| 相对路径 | 已是相对形式 | 再归一化成 posix 分隔、相对审查根 |

选这个做法有几个考虑。一是不要绑死在上游演进上：codeguard 会继续变，工作流契约不该被它的字段重命名牵着
走。二是下游其实只依赖六个字段（文件、行号、规则、级别、是否可修、稳定 ID），把契约收窄到"下游真
正需要的"，三个仓库才能并行开发。三是转换层很薄、能完整测试：`normalize.py` 是纯函数，
`test_normalize.py` 用 15 个用例把映射、排序、ID、路径钉住，不需要 codeguard 在场。

`normalize_findings()` 同时接受 dataclass 实例和 dict：

```python
def _as_mapping(raw):
    if isinstance(raw, dict):
        return dict(raw)
    to_dict = getattr(raw, "to_dict", None)     # codeguard 的 Finding.to_dict()
    if callable(to_dict): ...
    for key in ("rule_id", "severity", "category", "file", "line", "title", ...):
        if hasattr(raw, key): data[key] = getattr(raw, key)   # 退化路径
```

这样将来 codeguard 要是先序列化成 JSON 再交过来，这里也不用动。

### 3.3 稳定 `finding_id`：跨轮次闭环的前提

工作流是多轮的。

```
第 1 轮：审查 → 15 条问题（CG106 在 user_store.py:13）
        修复 → 插入 4 行参数化代码
第 2 轮：审查 → 同一个 CG106 现在在 user_store.py:17
```

ID 要是用行号构造，"上一轮那条 CG106 修好没有"这个问题就永远没人能自动回答：13 行那条"消失了"
（代码整体下移），17 行"出现了一个新问题"（其实就是原来那条）。

解决：

```python
def stable_finding_id(rule_id, file, title, *, occurrence=1) -> str:
    basis = f"{rule_id}|{_normalize_file(file)}|{_normalize_title(title)}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:8].upper()
    suffix = "" if occurrence <= 1 else f"-{occurrence}"
    return f"F{digest}{suffix}"
```

行号完全不参与，只出现在 `line` 字段里当展示信息；标题要先归一化（去空白和中英文标点、转小写、
截断 40 字符），因为 codeguard 的标题里带函数名；`occurrence` 用来区分同文件同规则同标题的重复命中，
共享哈希，用序号区分。

跨轮次闭环具体是这样走的：

| 步骤 | 谁做 | 依据 |
| --- | --- | --- |
| 审查产出 `finding_id` | 本 Agent | 上式 |
| 修复 Agent 把 `status` 改成 `fixed`/`deferred`/`rejected` | ③ | `FindingContract.status` |
| 下一轮复审产出新一批 `finding_id` | 本 Agent | 同一套哈希 |
| 判定"修好了" | ④ 编排器 | 上一轮 `open` 的 ID 在新结果里不存在，就是消失了；还在但 `line` 变了，只是位移，不算修 |

这个稳定性建立在"标题不变"的假设上。修复动作要是让 codeguard 生成不同措辞的标题，ID 就会变。
目前的实测是：对同一段未改动的代码，ID 完全稳定（`test_finding_id_stable_across_line_change` 断言的
就是这个），而且这 15 条的 ID 全部唯一。

### 3.4 可修复性判据

`fixability.py` 是审查 Agent 和修复 Agent 共用的词汇表，放在审查侧，因为审查侧最清楚"这条是哪个
规则命中的"，也因为报告得在一开始就告诉人"15 条里 7 条能自动修、8 条要人看"。

判可修要同时满足三条：

| 标准 | 含义 | 反例 |
| --- | --- | --- |
| 改写是局部的 | 不改函数签名、不改对外契约 | `CG303` 参数过多，要引入聚合对象，签名就变了 |
| 改写是确定的 | 同一处代码永远得到同一个正确结果，不需要业务判断 | `CG107` 弱随机数，用途是令牌、盐还是采样，只有作者知道 |
| 改写可验证 | 改完重扫能证明问题消失，且没引入新的阻断级问题 | `CG301` 函数过长，拆分方式无穷多，没法验证"拆对了" |

9 条规则过了这三条：`CG105`→`move_secret_to_env`、`CG106`→`parameterize_sql`、
`CG202`→`log_broad_except`、`CG203`→`sentinel_default`、`CG204`→`identity_comparison`、
`CG206`→`context_manager`、`CG207`→`explicit_validation`、`CG402`→`strip_trailing_whitespace`、
`CG403`→`drop_unused_import`。

另外 18 条判为不可修，每条都写了理由。下面是几条例子：

```
CG305  常量提出来叫什么名字、放哪儿，需要领域知识
CG307  返回类型得作者确认，注解猜错比不写更糟
CG405  语法错误只能作者自己修，机器猜不出原本想写什么
CG000  元信息提示，不是代码问题
```

未知规则一律 `fixable=False`，理由是 `"未知规则，没有对应的自动改写策略，转人工确认"`。让 Agent
假装能修，比明说"这需要人"危害大得多。

`test_fixability.py` 那 7 个用例守的是这张表的自洽性，不是本仓库的行为：

```python
def test_tables_are_disjoint():            # 同一规则不能既可修又不可修
def test_every_row_is_documented():        # 每一行都得说清策略名或理由
def test_unknown_rule_defaults_to_unfixable()
def test_strategy_names_are_snake_case()   # 策略名要与 fix-agent 的注册表对得上
```

最后一条尤其要紧：`CG106 → "parameterize_sql"` 必须和修复 Agent 的策略注册表一一对应，两边对不上
就会出现"审查说能自动修、修复却没有策略"的静默失败。

### 3.5 门禁口径是配置项

"`high` 及以上算不合格"是个团队策略，不是审查工具的固有属性。同一个项目在不同阶段需要的口径也
不一样：

| 阶段 | 合理口径 |
| --- | --- |
| 原型期 | 只挡 `critical`（先让流水线跑起来） |
| 日常开发 | `critical` + `high`（默认） |
| 发版前 | `critical` + `high` + `medium` |

所以 `blocking` 是 `ReviewConfig` 的字段（`("critical", "high")`），外面还包着几层保护。校验：含未知级别就
抛 `ReviewError` 并列出可选值，为空直接拒绝（"若想不阻断，请显式配置为 `('critical',)`"），避免
"空列表等于什么都不挡"的静默失效。可覆盖：`--blocking` 可重复传，走同一条校验。可解释：
`gate_detail()` 逐条给出 `blocks` 布尔值。

实测同一份代码，口径不同结论就不同：

```
--blocking critical,high（默认）              → ❌ 未通过门禁（3 条阻断级）
--blocking critical                          → ❌ 未通过门禁（1 条阻断级）
--blocking critical --blocking high --blocking medium → ❌ 未通过门禁（6 条阻断级）
```

`passed` 的定义就一句：`blocking_count == 0`。

健康分是另一套东西，`normalize.py` 的注释里写得很清楚，打分权重和阻断级别的权重明显分开：

| 指标 | 用途 | 不能用来做什么 |
| --- | --- | --- |
| `passed` | 门禁结论（布尔） | — |
| `score`（0~100） | 只用于趋势对比，看这轮比上轮好还是差 | 不能当合格证明：4 条 info 也是 100 分，1 条 critical 就掉到 75 |
| `overall_risk` | 展示最严重的级别 | 不参与门禁判定 |

扣分表：`critical −25 / high −10 / medium −4 / low −1 / info 0`，下限 0。实测 15 条（1/2/3/5/4）
就是 100 − 25 − 20 − 12 − 5 − 0 = 38。`test_score_decreases_with_severity` 只断言"轻的比重的
分高"，不断言具体数值，不然调整一次权重就得改一次测试。

### 3.6 两种审查模式

| 维度 | `static`（默认） | `llm` |
| --- | --- | --- |
| 实现 | `codeguard.static_analysis.CodeAnalyzer` | `codeguard.CodeReviewAgent`（完整 Agent 闭环） |
| 能力 | 27 条确定性规则 | 规则 + 模型语义推断（逻辑缺陷、业务边界） |
| 速度 | 实测 0.01s / 单文件 | 取决于模型，通常数十秒 |
| 成本 / 可复现 | 0，完全可复现 | token，不可复现 |
| 并行 | 按文件 `ThreadPoolExecutor` | 不并行（一次闭环处理整个目标） |
| 无 Key 可用 | ✅ | ❌（`--mock` 除外） |
| Markdown 渲染 | 本仓库自有渲染 | 复用 `codeguard.report.render_markdown` |

怎么选很简单：循环里反复跑用 `static`，收敛前做深度检查用 `llm`。两者共用同一套归一化和门禁计算，
所以 `passed` 口径一致，切换模式才有意义；两种模式对同一份代码给出不同门禁结论的话，门禁就等于
摆设。

`llm` 模式还有几处实现细节值得一提。一是不额外合并静态结果：codeguard 已经把静态命中并进它自己的
报告了，再合一次会重复计数。二是 Markdown 渲染失败不致命：降级成一行"（Markdown 渲染失败：…）"，
审查结论照常返回，报告是展示层，不该让整个审查失败。

## 4. 数据结构与契约

`FindingContract`：

```
finding_id   稳定哈希 ID（F + 8 位十六进制，重复命中带 -2/-3 序号）
rule_id      CG1xx/CG2xx/CG3xx/CG4xx；llm 模式的语义问题可能为空
severity     critical|high|medium|low|info（非法值降级为 medium）
category     security|reliability|maintainability|style|general
file         相对审查根目录的 posix 路径
line         行号或 None（只用于展示）
title / evidence / explanation / suggestion
source       static（规则引擎）/ llm（语义判断），如实标注来源
fixable      可自动修否
confidence   模型可给（static 模式下为 null）
status       open|fixed|deferred|rejected，下游回写
note         不可修时的原因
```

`source` 字段是为了让报告能区分"规则引擎的客观命中"和"模型的语义判断"，否则读者没法判断结论可信
多少。`__post_init__` 干两件事：`severity` 归一化成小写，`line` 安全转 int（失败或 ≤0 就置
`None`）。上游给什么都炸不了。

`ReviewOutcome` 是一次审查的完整结果：

| 字段/属性 | 说明 |
| --- | --- |
| `findings` | `FindingContract` 列表，按严重级 → 文件 → 行号排好 |
| `files_reviewed` | 被审查的文件名 |
| `mode` / `blocking` | 本次生效的模式与门禁口径 |
| `summary_text` / `markdown` | 结论文本与人类可读报告 |
| `elapsed_seconds` / `diagnostics` / `raw_meta` | 耗时与诊断；`llm` 模式下 `raw_meta` 里有迭代轮数、工具调用数、token 用量 |
| `counts` / `metrics` / `passed` | 派生属性，全部由 `findings` + `blocking` 算出来 |

`metrics` 每次访问都重新跑一遍 `summarize(...)`。结果对象是值对象，不做状态缓存，就不会出现"改了
findings 但 metrics 还是旧的"这种 bug。

契约信封在 `protocol.py` 里：`AgentRequest` / `AgentResponse` / `Artifact` / `FindingContract`
加上严重级工具（`severity_rank` / `is_blocking` / `count_by_severity` / `count_blocking`），四个
仓库各持一份等价副本。字段含义见 README 的契约一节。

有一处值得单独说：`count_by_severity()` 永远返回五个键，没出现过的级别给 0，不返回"只出现过的那
几个级别"。这样报告渲染和前端展示都不用写 `dict.get(key, 0)` 兜底，
`test_counts_always_have_all_three_plus_keys` 钉住了这个约定。

## 5. 失败处理

| 失败形态 | 处置 | 位置 | 为什么 |
| --- | --- | --- | --- |
| codeguard 找不到 | `CodeGuardNotFound` + 4 种解决方案；`doctor` 返回退出码 1 | `locate._not_found_message` | 这是最常见的环境问题，一句 `ModuleNotFoundError` 帮不上忙 |
| codeguard 版本过低 | `CodeGuardNotFound`（"找到的版本为 X，低于需要的 Y"） | `locate.ensure_codeguard` | 版本不符和找不到的修法不一样 |
| 目标目录不存在 | `FileNotFoundError` → CLI 一行 `❌ 找不到路径：…` + 退出码 2 | `resolve_targets` / `cli.main` | 调用方的错，给清楚的错误，别打堆栈（见 §8.4） |
| 目标里没有匹配文件 | `NoTargetsError`（带"匹配 X 的文件"提示） | `agent.review` | 让人能分清是路径错了还是 glob 错了 |
| 文件数超上限 | `ReviewError`（"请缩小目标范围或调大 max_files"） | `agent.review` | 防止误指到超大目录后跑很久 |
| 单个文件读不了 | `ReviewError`（带路径） | `agent._review_static` | 静默跳过会让人以为这个文件没问题 |
| 某文件语法错误 | 由 `CG405` 报出，该文件其他规则跳过 | codeguard | 语法错误会让后续语义分析不可信（Homework 1 的设计） |
| severity 值非法 | 降级为 `medium` | `normalize` | 上游给错值不该让整批归一化失败 |
| 未知规则 | `fixable=False` + 明确理由 | `fixability.classify` | 让 Agent 假装能修更糟 |
| 结果被截断（`CG000`） | 归入"需人工"，理由写明 | `fixability` | 截断不是缺陷，但得让读者看见 |
| llm 模式模型调用失败 | codeguard 自己的重试/兜底生效；仍失败则异常向上 | codeguard → `agent` | 本 Agent 不重复实现重试 |
| llm 模式缺 Key | `ReviewError` + 4 种解决方式 | `config.validate` | 第 4 种就是"用 `--mode static`" |
| Markdown 渲染失败 | 降级成一行说明，结论照常返回 | `agent._review_with_llm` | 展示层失败不该让审查失败 |
| 报告目录写不进去 | 协议路径静默返回；CLI 的 `--report-dir` 直接抛 `OSError` | `agent._persist` / `cli._cmd_review` | 报告是辅助产物 |
| 协议请求不合法 / 未预期异常 | `status=failed` + 可读 `error` | `agent.handle` | 编排器不该看到裸 traceback |

原则和编码 Agent 一样：该降级的降级，该失败的抛得清楚，并告诉人怎么修。`static` 模式下没有任何
一条路会导致"部分文件审了、部分被静默跳过"，读不了就报错。

配置层还做了一件事让对方少配一次：`ReviewConfig.from_env()` 在 `REVIEW_*` 都没提供时，回退去读
Homework 1 的 `CODEGUARD_API_KEY` / `CODEGUARD_BASE_URL` / `CODEGUARD_MODEL`。同一份 `.env` 两个
项目都能用，错误提示里承诺的"沿用 Homework 1 的变量名"在代码里确实成立，不是只写在文案里。

## 6. 安全

| 威胁 | 防护 | 位置 |
| --- | --- | --- |
| 误审查到仓库敏感目录 | 默认只扫 `*.py`，排除 `.git`/`__pycache__`/`.venv`/`venv`/`node_modules`/`.mypy_cache`/`.pytest_cache`/`build`/`dist`/`.idea` | `resolve_targets` |
| 误指超大目录把机器跑死 | `max_files`（默认 200）硬上限，超过直接报错 | `agent.review` |
| 报告泄露本机绝对路径 | 统一 `relative_to(审查根)` 再转 posix，越界就原样返回，不崩 | `normalize._relative` |
| 跨盘符导致异常 | `relative_to` 失败时捕获 `ValueError`/`OSError` 回退 | `normalize._relative` |
| 密钥进日志/报告 | `describe()` 只显示前 8 位和后 4 位；长度 ≤16 显示 `***`；`--mock` 显示"离线脚本化模型" | `config.py` |
| 密钥进源码 | `.env*` 在 `.gitignore` 里（`.env.example` 例外）；测试夹具的假密钥用字符串拼接构造 | `.gitignore` / `tests/support.py` |
| 审查过程改动被审查代码 | 只读目标目录，唯一写入是 `--report-dir` | `agent._persist` / `cli._cmd_review` |
| `sys.path` 污染 | 只在进程内插入并 `importlib.invalidate_caches()`，不写 site-packages、不落盘 | `locate._prepend_sys_path` |
| 编码崩溃 | CLI 入口把 stdout/stderr 重配成 UTF-8（`errors="replace"`） | `cli._ensure_utf8_stdout` |

`_prepend_sys_path()` 会先 `sys.modules.pop("codeguard", None)`，原因在 §8.3：半加载的模块对象会让
后续属性访问变成 `AttributeError`，那种错误看起来像 codeguard 的问题，其实是定位逻辑没清干净。

本 Agent 没有对被审查代码做恶意代码防护。它是只读的，不执行被审查的代码，`static` 模式只做 AST
分析，风险面很小；但 `llm` 模式会把代码内容送进模型，审查不可信的第三方代码时存在 Prompt 注入面。
这一点继承自 codeguard，Homework 1 的设计文档里也标了。

## 7. 测试

```
        ▲  端到端（test_agent.py，19 例）
       ╱ ╲  真实跑 codeguard 规则引擎：检出、门禁、模式、协议入口、报告落盘
      ╱   ╲  + 两条回归：worker stdout 合法 UTF-8、CLI 路径不存在返回 2
     ╱─────╲  单元（test_normalize.py 15 例 + test_fixability.py 7 例）
    ╱       ╲  字段映射 / ID 稳定性 / 排序 / 评分 / 目标枚举 / 表自洽性
   ╱─────────╲
```

一共 41 个用例，实测 41/41 通过。离线是第一原则：

| 依赖 | 替身 / 前提 |
| --- | --- |
| 网络与 API Key | 全部用例走 `static` 模式，完全不联网 |
| pip 安装的 codeguard | 靠 `locate.py` 自动发现隔壁源码，`test_codeguard_is_locatable` 显式断言这个前提 |
| 真实项目目录 | `tempdir()`（多候选 + 回落 `.testtmp`） |
| 外部示例文件 | `BUGGY_SOURCE` / `CLEAN_SOURCE` 内联在 `support.py` 里 |

`BUGGY_SOURCE` 是 36 行样例源码，覆盖 `CG106`/`CG203`/`CG207`/`CG202`/`CG206`/`CG403` 加 `CG105`。
里面的密钥也是用字符串拼接构造的（`"sk" + "-" + "live-…"`），理由和编码 Agent 仓库一样：测试夹具
不该让工具自己报警。

几个用例设计上的考虑：

（1）稳定这件事单独测。

```python
def test_finding_id_stable_across_line_change():
    """行号变不影响 ID，这是跨轮次闭环的前提。"""
    assert stable_finding_id("CG106", "a.py", "SQL 语句由拼接构造") == \
           stable_finding_id("CG106", "a.py", "SQL 语句由拼接构造")
    assert stable_finding_id("CG106", "a.py", "SQL 语句由拼接构造") != \
           stable_finding_id("CG106", "a.py", "完全不同的标题")
```

第二个断言不能省，没它的话，一个永远返回常量的实现也能通过。

（2）正例和负例成对。`test_gate_fails_on_blocking` 和 `test_clean_code_passes_gate` 是一对，
只有前者的话，分不清"门禁正常工作"和"门禁永远返回假"。

（3）断言形状，不只断言数值。

```python
def test_payload_shape_is_contract_complete():
    for key in ("findings", "counts", "passed", "blocking", "score", "files_reviewed", "summary"):
        assert key in payload, key
```

这是契约测试，编排器依赖这些键，缺一个下游就崩。它不关心数值，只关心该在的都在。

（4）把表本身当成被测对象。`test_fixability.py` 一半用例在测结构一致性，共享词汇表这类资产必须
有这种自检。

## 8. 开发中碰到的工程问题

这一节记的是真碰到、真修过的问题，都能从代码注释、测试用例或实机运行复现。每条按现象、根因、修法、
回归测试的顺序写。

| # | 问题 | 修法 | 回归测试 |
| --- | --- | --- | --- |
| 8.1 | Worker 的 stdout 模式在 GBK 控制台下直接崩（summary 里的 `❌`） | `worker.py` 的 `main()` 首行调用 `_ensure_utf8_stdout()` | `test_worker_stdout_mode_survives_non_ascii` |
| 8.2 | 子进程多传一个配置键，调用方只看到"没有产出响应" | `_filter_known()` 过滤配置类不认识的键；所有失败包装成 `AgentResponse.failure` | `test_handle_roundtrip` / `test_handle_rejects_wrong_agent` |
| 8.3 | 改 `sys.path` 后拿到半加载的模块，或新路径不生效 | `sys.modules.pop` + `importlib.invalidate_caches()` | `test_codeguard_is_locatable` |
| 8.4 | 目标路径不存在时打出 traceback、退出码 1 | `cli.main()` 补 `except FileNotFoundError`，一行错误 + 退出码 2 | `test_cli_missing_path_returns_2` |

### 8.1 Worker 的 stdout 模式在 GBK 控制台下直接崩

`cli.py` 里有这么一段：

```python
def _ensure_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，遇到中文/emoji 会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try: reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError): pass
```

但 `worker.py` 当时没调它。本机 `python -c "import sys; print(sys.stdout.encoding)"` 输出的是 `gbk`，
于是子进程模式下：

| Worker | 当时的实测结果 |
| --- | --- |
| `coding_agent.worker`（只读对照） | 退出码 0，stdout 是 GBK 字节：`b'{\r\n  "agent": "coding",…\xb0\xb4'`，按 UTF-8 解码在 position 58 失败 |
| `review_agent.worker`（本仓库） | 退出码 1，stdout 为空，stderr 是 `UnicodeEncodeError: 'gbk' codec can't encode character '\u274c' in position 116` |

本仓库是崩，不是乱码，因为 `summary()` 里有 `❌`/`✅`，GBK 里没有 U+274C；编码 Agent 的 summary
全是中文，GBK 能编码，所以它只是静默产出非 UTF-8 字节。后者更阴险，因为它看起来是成功的。

根因是重配 stdout 的逻辑被复制到了 `cli.py` 和 `run_tests.py`，`worker.py` 漏了。之所以一直没暴露，
是因为编排器调用 Worker 时用 `--request-file`/`--response-file`，文件写入显式指定了
`encoding="utf-8"`，从来不读 stdout。

修法是给 `worker.py` 加上 `_ensure_utf8_stdout()`，在 `main()` 第一行调用；`coding_agent` 和
`fix_agent` 的 Worker 也同步补了。注释里写清了它为什么容易被忘：编排器默认走 `--response-file`，
这条路径平时跑不到，所以更得显式测。

回归测试 `test_worker_stdout_mode_survives_non_ascii` 真的
`subprocess.run([sys.executable, "-m", "review_agent.worker", "--request-file", ...])`，然后断言
退出码 0、`stdout.decode("utf-8")` 成功、`json.loads` 出来 `status == "ok"` 且 `findings` 非空。
这条路径只有起子进程才测得到：在进程内调 `main()` 会被当前进程已经重配过的 stdout 掩盖。

修完实测 stdout 模式：退出码 0，`json.loads(stdout.decode("utf-8"))` 成功，`status=ok`。

教训：横切的入口逻辑一旦复制到多个入口，就一定会漏。正确做法是放进一个 `compat.py` 之类的共享模块，
在每个 `main()` 的第一行调用；另外修完必须有一条真的起子进程的测试，不然它还会再漏一次。

### 8.2 子进程里多传一个配置键，结果"什么都没有发生"

`worker.py` 里有个看着多余的工具函数：

```python
overrides = _filter_known(ReviewConfig, overrides)   # 只把配置类真正认识的键传进去
```

代码里留了原因注释：多传一个未知键会直接 TypeError，而子进程里的 TypeError 对调用方只表现为"没有
产出响应"，非常难查。

这事真发生过。`ReviewConfig.from_env(**overrides)` 收到不认识的键会抛 `TypeError`，如果发生在
`_emit()` 之前，进程直接退出，没有响应文件。编排器那边只看到"子进程未产出响应（退出码 1）"加一段
stderr 尾巴，而 stderr 里是一行泛泛的 traceback。

教训：跨进程协议的错误得结构化成响应，不能靠退出码加 stderr 传递。所以 `worker.py` 把"读请求失败"
"请求格式错误""初始化失败"三种情况都包成 `AgentResponse.failure(...)`，让每种失败都沿同一条通道回到
调用方。另外一条更普遍的观察：子进程协议的 stdout 只能有一个 JSON 对象，多一行 print 就让调用方的
JSON 解析失败。

### 8.3 半加载的模块与 importlib 的路径缓存

`locate.py` 注入 `sys.path` 的函数做了两件看着多余的事：

```python
def _prepend_sys_path(root: Path) -> None:
    entry = str(root.resolve())
    # 先清掉 sys.modules 里可能残留的"半加载"状态，避免拿到空模块
    sys.modules.pop(PACKAGE_NAME, None)
    if entry in sys.path: sys.path.remove(entry)
    sys.path.insert(0, entry)
    # 让后续 import 找到新路径上的包（importlib 缓存了路径查找器）
    importlib.invalidate_caches()
```

两个坑。一是半加载的模块：如果之前有一次 `import codeguard` 失败（路径上有个空 `codeguard/` 目录，
或者导入过程中抛了异常），`sys.modules` 里可能残留一个残缺条目，之后再 `import` 拿到的就是它，访问
`.static_analysis` 直接 `AttributeError`，而错误信息指向 `agent.py`，看起来像"codeguard 的 API 变
了"。二是路径查找器缓存：改了 `sys.path` 之后 `PathFinder` 可能还缓存着旧目录列表，不调
`invalidate_caches()`，"看起来加了但没生效"。

教训：动态改 `sys.path` 是全局状态的副作用，改完必须清理可能残留的模块条目，并让查找器缓存失效。
少哪一步，症状都会出现在很远的地方。

### 8.4 目标不存在时打出 traceback、退出码 1

修复前实测：

```
$ python -m review_agent review ./definitely-not-here
  File ".../review_agent/normalize.py", line 180, in resolve_targets
    raise FileNotFoundError(f"审查目标不存在：{base}")
FileNotFoundError: 审查目标不存在：D:\软件工程实践\homework2\review-agent\definitely-not-here
$ echo $?
1
```

根因：`resolve_targets()` 抛的是内置 `FileNotFoundError`，而 `cli.main()` 只捕获
`ReviewAgentError`，于是最常见的用户错误（路径敲错）得到的是一份 traceback 加退出码 1，本来应该是
"一行错误 + 退出码 2"。对比编码 Agent 的 `cli.py`，它早就额外捕获了 `FileNotFoundError`。

没被测试发现，是因为 `test_missing_target_raises` 断言的是 `expect_raises(FileNotFoundError, ...)`，
验证"会抛异常"这件事。库层面这是正确设计，库不该把用户输入错误包成自定义异常，但它没覆盖"CLI 层
这个异常怎么呈现"。这是分层测试的典型盲区：库层的正确行为不等于 CLI 层的正确体验。

修法是 `cli.main()` 在 `except ReviewAgentError` 后面补一条 `except FileNotFoundError`，打印一行
`❌ 找不到路径：<原始异常信息>` 并返回 2。源码注释里写明了这条分支曾经漏掉的原因。

回归测试 `test_cli_missing_path_returns_2` 直接调 `from review_agent.cli import main`，断言
`main(["review", "/definitely/not/here/at/all"]) == 2`。它测的是返回值，不是"有没有抛异常"，因为
用户看到的就是退出码。修完实测：

```
$ python -m review_agent review ./definitely-not-here
❌ 找不到路径：审查目标不存在：D:\软件工程实践\homework2\review-agent\definitely-not-here
$ echo $?
2
```

教训：退出码约定是接口，必须被测试钉住。只测"函数抛不抛异常"不够，用户看到的是进程的退出码和
stderr。

## 9. 局限与改进

局限：

| 局限 | 说明 |
| --- | --- |
| `finding_id` 的稳定性依赖标题 | 标题含函数名，重命名函数会让 ID 变；行号位移不影响 |
| 同一文件同类问题的序号可能漂移 | `-2`/`-3` 按遍历顺序分配，极端情况下可能互换 |
| `static` 能力上限就是 codeguard 的 27 条规则 | 语义层问题只能靠 `llm` 模式，`static` 报告自己也写了"不含语义推断" |
| 跨文件数据流、类型推断都没有 | 继承自 codeguard 的局限 |
| 无增量审查 | 每轮全量重扫，不利用 `git diff` |
| 可修复性表要人工维护 | 9 + 18 = 27 行硬编码，codeguard 加规则时必须同步，否则新规则会掉进"未知规则 → 转人工" |
| 无审查历史 | 每轮独立，"哪条修好了"靠编排器按 `finding_id` 比对 |
| `llm` 模式不并行 | 一次 Agent 闭环处理整个目标，多文件时墙钟时间线性增长 |

改进方向，按性价比排：

1. `finding_id` 改成"规则 + 文件 + 规则在文件内的第 n 次出现"做基，把标题从哈希输入里去掉（标题只
   做展示），这样函数重命名也不破坏闭环；
2. 可修复性表从 codeguard 的规则元数据生成，做到"文档即代码"，省掉手工同步；
3. 增量审查：接 `git diff`，只审改动的文件或行区间；
4. 审查历史：存历次 findings，直接输出"本轮修好 N 条、新引入 M 条"；
5. `static` + `llm` 混合：静态命中先定论，只把需要语义判断的文件送进 `llm`。

如果重来一次，会先做这几件事：

- 把"找 codeguard"和"兼容 codeguard 的 API"写成显式契约测试。现在 `test_agent.py` 依赖真实
  codeguard 在场，既是优点（真的在测集成）也是风险（codeguard 改 API 时才报错）。更好的做法是加
  一组假 codeguard 模块（构造函数已经支持 `codeguard=` 注入）覆盖适配层逻辑，真实集成测试单列。
- `_render_markdown` 和 codeguard 的渲染只留一条路径。现在 `static` 用自有渲染、`llm` 复用
  codeguard 的，两份报告的样式和信息密度不一致。当时选自有渲染是为了契约层能独立表述结论，代价是
  用户看到两种不同格式的报告。
- 退出码约定一开始就配子进程测试（§8.4）。这是最典型的"测了库没测接口"，修完之后
  `test_cli_missing_path_returns_2` 直接断言 `main()` 的返回值，不看有没有抛异常。
- `_ensure_utf8_stdout()` 一开始就放进共享模块（§8.1）。现在它在 `cli.py`、`run_tests.py`、
  `worker.py` 里各有一份等价实现，靠"改一处必须同步多处"维持一致。

## 附录：关键文件速查

| 想了解… | 看这个 |
| --- | --- |
| codeguard 怎么被找到 | `review_agent/locate.py::_candidate_roots` / `_prepend_sys_path` |
| 找不到时给什么提示 | `review_agent/locate.py::_not_found_message` |
| 两种模式怎么分派 | `review_agent/agent.py::ReviewAgent.review` |
| `static` 模式怎么并行 | `review_agent/agent.py::_review_static` |
| 字段怎么映射 | `review_agent/normalize.py::normalize_findings` |
| `finding_id` 怎么算 | `review_agent/normalize.py::stable_finding_id` |
| 门禁怎么判定 | `review_agent/normalize.py::summarize` + `ReviewConfig.blocking` |
| 健康分怎么算 | `review_agent/normalize.py::score`（扣分表 `_SCORE_PENALTY`） |
| 哪些规则能自动修 | `review_agent/fixability.py::FIXABLE_STRATEGIES` / `NOT_AUTOFIXABLE` |
| 契约字段 | `review_agent/protocol.py` + README 契约一节 |
| 子进程入口 | `review_agent/worker.py` |
| 怎么诊断环境 | `python -m review_agent doctor` |
| 怎么跑测试 | `run_tests.py` + `tests/` |
