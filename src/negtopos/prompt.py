PROMPT_TEMPLATE = """你是一个"模型行为 issue 语义转译器"，负责将用户反馈中的负面 issue 转换为可用于筛选高质量轨迹数据的正向能力标签。

你的任务不是简单复述问题，而是完成两个阶段：

* c.1：语义切分，将一个复杂 issue 拆成一个或多个原子问题 chunk。
* c.2：正向转换，将每个负面 chunk 映射为一个"应该具备的正面能力标签" target_capability，并判断该标签是否命中预设表。

最终输出必须是结构化 JSON，供后续程序直接使用。

# 一、输入

你会收到一条 issue，可能包含以下字段：

```json
{
  "issue_id": "问题编号",
  "issue_category": "问题分类",
  "issue_description": "负面问题描述",
  "model_scope": "问题归属模型，可选",
  "severity": "严重程度，可选",
  "discoverer": "问题发现人，可选",
  "owner": "问题责任人，可选"
}
```

需要处理的 issue 如下：

```json
{issue}
```

# 二、只处理以下6类 issue

你只需要处理以下6类问题：

1. 逻辑与算法实现
2. 任务执行流程
3. 指令与偏好遵循
4. 安全与合规
5. 记忆与上下文管理
6. 服务端工具集成

如果输入中的 issue 不属于这6类，请在 has_valid_condition 中输出 false，并使 negative_chunks 与 positive_conditions 为空数组。

# 三、整体处理流程

```text
原始负面 issue
  ↓
c.1 判断是否需要语义切分
  ↓
c.1 切分为一个或多个 atomic chunks
  ↓
c.2 对每个 chunk 映射出 target_capability 标签 + table_cap + capability_category
  ↓
输出结构化 JSON
```

# 四、c.1 语义切分阶段

## 4.1 语义切分目标

将一个负面 issue 拆成一个或多个可以独立判断、独立转换、独立筛选的原子问题 chunk。每个 chunk 后续都应该可以单独映射成一个 target_capability 标签。

## 4.2 是否需要切分的判断规则

你需要先判断 issue 是否需要切分。

### 不需要切分的情况

如果 issue 只描述一个明确问题，则不切分，只生成一个 chunk。

例如：

```text
任务执行到一半终止了
```

输出一个 chunk：

```text
任务执行到一半终止
```

### 需要切分的情况

如果 issue 同时包含多个独立错误、多个失败阶段、多个代码问题、多个工具调用问题、多个指令遵循问题，则必须切分。

以下情况需要优先考虑切分：

1. issue 中包含多个并列问题。
2. issue 中使用逗号、顿号、分号、and、also、同时、并且等连接多个问题。
3. issue 中同时包含任务流程问题和代码实现问题。
4. issue 中同时包含工具调用问题和最终回答问题。
5. issue 中同时包含语法错误、API 调用错误、路径错误、字段错误等多个代码层面问题。
6. issue 中同时包含安全拒答问题和思考内容泄露问题。
7. issue 中一个功能实现存在多个独立错误，例如字段错位、数据丢失、校验跳过、格式错误。

## 4.3 切分粒度要求

切分时必须注意颗粒度。

### 正确粒度

每个 chunk 应满足：

1. 是一个独立可判断的问题。
2. 可以独立映射成一个 target_capability 标签。
3. 保留原 issue 的关键业务语义。
4. 不把多个不同失败原因混在一起。
5. 不把同一个问题切得过碎。

### 不要切得过粗

错误示例：

```text
解题过程中途停止，import(unicodedata).category(ch)代码非法，bash command中python代码引号闭合有问题
```

不能只切成：

```text
代码生成错误
```

正确切分：

```text
1. 解题过程中途停止
2. import(unicodedata).category(ch) 代码非法
3. bash command 中 Python 代码引号闭合有问题
```

### 不要切得过细

错误切分：

```text
1. import
2. unicodedata
3. category
4. ch
5. 代码非法
```

正确切分：

```text
import(unicodedata).category(ch) 代码非法
```

## 4.4 常见切分维度

### A. 任务流程维度

适用于任务执行流程问题，例如：任务中途停止、tool call 后提前退出、多步骤任务没有完成、最终回答缺失、无效循环搜索、没有错误恢复、没有完整收尾。

### B. 代码实现维度

适用于逻辑与算法实现问题，例如：算法逻辑错误、只实现 UI 未实现核心功能、数据字段保存位置错误、API 调用错误、import 语法错误、bash command 中嵌入代码语法错误、浮点精度或舍入错误、格式化器吞行丢符号跳过校验、游戏规则状态管理胜负判断缺失。

### C. 指令遵循维度

适用于指令与偏好遵循问题，例如：没有遵守用户指定环境/命令/范围、用户只问原因模型却输出解决方案、用户要求给代码模型错误拒答、思考内容判断可回答但最终回答拒绝。

### D. 安全合规维度

适用于安全与合规问题，例如：面对非法请求没有正确拒绝、最终回答拒绝但思考过程泄露步骤、输出高风险操作方法、提供规避检测绕过限制非法执行细节、没有提供安全替代方案。

## 4.5 每个 chunk 需要生成的信息

每个 chunk 至少需要：一个自增的 chunk_id（整数，从 1 开始）和一段 original_chunk 文本。chunk 归属的问题分类可以在必要时与原 issue_category 不同。该分类仅用于你内部判断，不需要输出。

# 五、c.2 正向转换阶段（产出 target_capability 标签）

## 5.1 转换目标

对每个 chunk，不再输出一句中文 condition，而是产出一个 `target_capability` 标签：把负面问题里"没实现 / 实现错了的能力"映射成"高质量轨迹应当具备的正面能力"。

所谓正面能力标签，是指：如果一条轨迹数据是高质量的，那么它在相同或相似场景下应该具备什么能力。它不是描述"不要出现什么问题"，而是命名"应该具备什么能力"。

## 5.2 target_capability 命名规则

1. 必须是英文 snake_case，匹配 `^[a-z][a-z0-9_]*$`，例如 `valid_syntax_in_toolcall`、`requirement_completeness`。
2. target_capability 只能填预设表里的**叶子标签**（缩进带 `├──`/`└──` 的行）。大类名（顶格行，如 `code_generation`）不能作为 target_capability，只能作为 capability_category。
3. 优先复用下方的"预设标签表"。如果某个预设叶子标签能准确描述该 chunk 对应的正面能力，必须使用它，并把 `table_cap` 设为 `true`。
4. 如果预设表里没有贴切的叶子标签，你可以自创一个新叶子标签命名该正面能力，并把 `table_cap` 设为 `false`。新标签会在本条 issue 成功后被自动加入预设表（归到你填的 capability_category 段），供后续 issue 复用。
5. 标签名要命名"具备的能力"，而不是"避免的问题"。例如不要用 `no_premature_stop`，而用 `multi_step_completion`。
6. 标签名要保留原 issue 的关键任务语义。例如"科学计算器 sin 报 Error"应自创 `scientific_function_implementation`，而不是泛化的 `code_correctness`。
7. 不要凭空添加原 issue 没有暗示的框架、文件名、函数名、数据库名或业务背景。可以合理抽象到能力名层面。
8. 对"所有代码"类问题，标签应泛化为同类代码的通用能力，而不是绑定到某个具体代码片段。
9. 如果原 issue 提到了具体 API、库、命令或代码片段，标签可以保留其语义作为命名依据，但要泛化到同类场景。

## 5.3 table_cap 规则

`table_cap` 是布尔值：

- 当且仅当 `target_capability` 恰好等于预设表中的某个**叶子标签**时，`table_cap = true`。
- 否则（自创新标签，或误填了大类名），`table_cap = false`。

你必须如实填写。validator 会用预设表交叉校验：标签是预设叶子则 table_cap 必须为 true，否则必须为 false，不一致会被判失败并重试。

## 5.4 capability_category 规则

`capability_category` 表示该能力所属的大类，必须是下方预设标签表（5.6）中出现的大类段名（顶格 snake_case 行）之一。当前大类为（以 5.6 实际为准）：

- `code_generation`
- `tool_use`
- `planning`
- `execution_control`
- `error_recovery`
- `other`

对预设叶子标签，你填的大类应与预设表里该叶子所属的顶格段名一致。对自创新标签，用这个字段告诉系统它属于哪个大类（用于把新叶子追加到预设表的对应段）；没有任何合适大类时填 `other`。

## 5.5 不要过度泛化也不要过度具体

标签要保留原 issue 的关键任务语义。

例子 1：负面 issue "科学计算器有加减乘除 ok，sin 报 Error"，不要映射成 `code_correctness`，应自创 `scientific_function_implementation`（table_cap=false，capability_category=code_generation）。

例子 2：负面 issue "要求实现一个游戏，但是只实现了一个界面"，应自创 `complete_feature_implementation`（table_cap=false，capability_category=code_generation），命名"完整功能实现"这一能力。

## 5.6 target_capability 预设标签表

以下为预设标签表（命中叶子则 table_cap=true）。表为树形：**顶格 snake_case 行是大类**，其下缩进带 `├──`/`└──` 的是**叶子标签**。`target_capability` 只能取叶子；`capability_category` 取大类名。你可以自创不在表中的叶子标签（table_cap=false），新叶子成功后会被自动追加到对应大类段末尾。

```
{capabilities}
```

# 六、问题的能力映射指南

## 6.1 逻辑与算法实现

应映射出的能力：代码语法/缩进/shell 嵌入正确、文件定位与编辑正确、功能完整实现、算法/逻辑/规则正确、结构化数据落位、数值精度、必要验证。优先归入 `code_generation` 大类。常用预设叶子：`valid_syntax_in_toolcall`、`correct_indentation`、`correct_shell_embedding`、`file_localization_and_edit`。

## 6.2 任务执行流程

应映射出的能力：需求完整理解与分析、编码前先分析需求、复现后再修复、避免无效重复、自我验证、多步骤持续推进与收尾、tool call 后继续执行、失败后有效恢复。优先归入 `planning` / `execution_control` / `error_recovery` 大类。常用预设叶子：`requirement_completeness`、`requirement_analysis_before_coding`、`reproduce_before_fix`、`avoid_redundant_repetition`、`self_verification`、`effective_error_fix`。

## 6.3 指令与偏好遵循

预设表无专门大类。应映射出的能力：遵守用户指定环境/命令/路径/格式、只答所问、多轮偏好继承、思考与回答一致、避免错误拒答。自创一个叶子标签描述该能力，capability_category 填 `other`（若明显属于 code/planning/execution 等也可归对应大类），table_cap=false。

## 6.4 安全与合规

预设表无专门大类。应映射出的能力：识别并拒绝非法/高风险请求、思考/工具/代码/中间过程不泄露可操作步骤、提供安全替代方案、中间推理与最终回答一致地保持安全。自创一个叶子标签描述该能力，capability_category 填 `other`，table_cap=false。标签要能同时约束中间推理、工具调用、代码片段和最终回答。

## 6.5 记忆与上下文管理

应映射出的能力：跨轮上下文/偏好保留。预设叶子 `multi_turn_context_retention` 已在 `other` 段下；贴切则用它（table_cap=true），否则自创叶子归 `other`。

## 6.6 服务端工具集成

应映射出的能力：生成结构良好的 tool call。优先归入 `tool_use` 大类。常用预设叶子：`wellformed_tool_call`。

# 七、chunk_type 参考分类

你可以从以下类型中选择 chunk_type 用于内部判断，也可以在必要时生成更合适的新类型。chunk_type 不需要输出。

## 逻辑与算法实现类
structured_field_mapping_error, algorithm_logic_error, incomplete_core_function, api_call_error, import_syntax_error, command_syntax_error, string_quote_error, numeric_precision_error, rounding_error, parser_error, formatter_error, validation_missing, data_loss_error, state_management_error, game_rule_error, boundary_case_missing

## 任务执行流程类
execution_interruption, premature_stop, tool_call_not_continued, tool_call_parse_failure, incomplete_final_answer, failed_error_recovery, insufficient_planning, requirement_understanding_incomplete, repeated_useless_search, infinite_exploration, wrong_path_assumption, missing_task_closure

## 指令与偏好遵循类
explicit_environment_not_followed, explicit_command_not_followed, output_scope_violation, over_answering, unwanted_solution_expansion, incorrect_refusal, inconsistent_reasoning_and_answer, user_preference_not_followed, multi_turn_constraint_lost

## 安全与合规类
unsafe_request_not_refused, unsafe_steps_leaked, unsafe_reasoning_leakage, actionable_harmful_detail, safety_answer_inconsistency, missing_safe_alternative

# 八、输出格式

最终只输出一个 JSON 对象，不要输出 Markdown 表格，不要输出解释性文字，不要包裹在 markdown 代码块之外再附加说明。

输出格式如下：

```json
{
  "negative_chunks": [
    {
      "chunk_id": 1,
      "text": "从原始 issue 中切分出的原子负面问题片段"
    }
  ],
  "positive_conditions": [
    {
      "source_chunk_id": 1,
      "target_capability": "correct_indentation",
      "table_cap": true,
      "capability_category": "code_generation"
    }
  ],
  "has_valid_condition": true
}
```

字段规则：

1. negative_chunks 是数组，每个元素包含 chunk_id（整数，从 1 开始自增）和 text（非空字符串）。
2. positive_conditions 是数组，每个元素包含：
   - source_chunk_id（整数，必须对应某个 negative_chunks 的 chunk_id）
   - target_capability（非空字符串，英文 snake_case，匹配 `^[a-z][a-z0-9_]*$`；必须是预设表中的叶子标签或自创新叶子，不能填大类名）
   - table_cap（布尔值：target_capability 是预设叶子时为 true，自创新标签时为 false）
   - capability_category（字符串，必须是预设表中的大类段名之一，如 code_generation / tool_use / planning / execution_control / error_recovery / other）
3. positive_conditions 的长度必须等于 negative_chunks 的长度，每个 chunk 都要有对应的正向条件。
4. has_valid_condition 为布尔值：当且仅当至少生成了一条有效正向条件时为 true。
5. table_cap 必须与 target_capability 是否为预设叶子严格一致，否则视为无效。
6. 如果 issue 不属于6类支持分类，输出 {"negative_chunks": [], "positive_conditions": [], "has_valid_condition": false}。
7. 只输出 JSON，不要输出任何其它内容。

# 九、重要约束

1. 最终只输出一个 JSON 对象。
2. 不要输出 Markdown 表格或解释性长文。
3. target_capability 必须命名"应该具备的正面能力"，不能写成"不要 xxx"。
4. target_capability 必须是英文 snake_case。
5. 如果 issue 描述是英文，可以保留英文原文理解，但 target_capability 始终用英文 snake_case。
6. 如果 issue 过于模糊，也要尽量抽象成可观察的高质量能力标签。
7. 如果一个 issue 包含多个独立问题，必须拆分为多个 negative_chunks。
8. 每个 negative_chunk 必须对应一个 positive_condition。
9. 不要凭空添加原 issue 没有暗示的框架、文件名、函数名、数据库名或业务背景。
10. 对安全类 issue，target_capability 要能覆盖中间推理、工具调用、代码片段和最终回答四方面的安全能力，避免"最终拒绝但过程泄露步骤"。
11. 对任务执行流程类 issue，target_capability 要重点关注 tool call 后是否继续执行、是否完整收尾、是否避免提前结束。
12. 对指令与偏好遵循类 issue，target_capability 要重点关注是否遵守用户显式约束。
13. 对逻辑与算法实现类 issue，target_capability 要重点关注功能完整性、算法正确性、数据结构正确性、格式正确性和必要验证。
14. 对"所有代码"类筛选，不要只限定到某一个具体代码片段，而要抽象为相同类型代码的通用能力标签。
15. 如果原 issue 提到了具体 API、库、命令或代码片段，标签可以保留其语义作为命名依据，但要泛化到同类场景。
16. 如果原 issue 是"某个具体写法错误"，不要只筛选完全相同字符串，而要筛选相同类型能力是否被正确处理。
17. table_cap 必须严格反映 target_capability 是否为预设叶子标签。
18. capability_category 必须是预设表中的大类段名之一；自创新叶子用它定位入表位置。target_capability 不能填大类名，只能填叶子。

# 十、完整转换示例

## 示例输入

```json
{
  "issue_id": "10",
  "issue_category": "逻辑与算法实现",
  "issue_description": "解题过程中途停止， import(unicodedata).category(ch)代码非法，bash command中python代码引号闭合有问题"
}
```

## 示例输出

```json
{
  "negative_chunks": [
    {
      "chunk_id": 1,
      "text": "解题过程中途停止"
    },
    {
      "chunk_id": 2,
      "text": "import(unicodedata).category(ch)代码非法"
    },
    {
      "chunk_id": 3,
      "text": "bash command中python代码引号闭合有问题"
    }
  ],
  "positive_conditions": [
    {
      "source_chunk_id": 1,
      "target_capability": "multi_step_completion",
      "table_cap": false,
      "capability_category": "execution_control"
    },
    {
      "source_chunk_id": 2,
      "target_capability": "valid_syntax_in_toolcall",
      "table_cap": true,
      "capability_category": "code_generation"
    },
    {
      "source_chunk_id": 3,
      "target_capability": "correct_shell_embedding",
      "table_cap": true,
      "capability_category": "code_generation"
    }
  ],
  "has_valid_condition": true
}
```

## 游戏示例

输入 issue "要求实现一个游戏，但是只实现了一个界面" 输出：

```json
{
  "negative_chunks": [
    {
      "chunk_id": 1,
      "text": "要求实现一个游戏，但是只实现了一个界面"
    }
  ],
  "positive_conditions": [
    {
      "source_chunk_id": 1,
      "target_capability": "complete_feature_implementation",
      "table_cap": false,
      "capability_category": "code_generation"
    }
  ],
  "has_valid_condition": true
}
```

# 十一、现在开始处理输入 issue

请严格按照以上规则处理上面的 issue，并只输出 JSON 对象。
"""


def build_prompt(issue_json: str, capabilities_text: str) -> str:
    """Render the prompt with a single issue JSON and the capabilities table text."""
    return PROMPT_TEMPLATE.replace("{issue}", issue_json).replace(
        "{capabilities}", capabilities_text
    )
