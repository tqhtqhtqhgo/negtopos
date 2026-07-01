# negtopos

将用户反馈中的**负面 issue** 转换为可用于筛选高质量轨迹数据的**正向筛选条件**。

读取 jsonl（一行一个 issue），调用 OpenAI 兼容的 LLM 完成 c.1 语义切分 + c.2 正向转换，校验输出 JSON 格式，失败重试（默认 5 次），成功后把结构化记录写入输出 jsonl。

## 环境与依赖

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) 管理虚拟环境与依赖
- 唯一运行时依赖：`httpx`（配置读取用标准库 `tomllib`）

```bash
uv sync
```

## 配置

所有可配置项在 `config.toml`：

```toml
[api]
url = "http://1.95.77.23:3000"   # OpenAI 兼容网关地址
key = "sk-1234"                  # 替换为真实 key
model = "glm5.2"
json_mode = true                # 请求 response_format=json_object；模型不支持时自动关闭重试
temperature = 0.2
timeout = 120

[processing]
max_retries = 5                 # 首次失败后的重试次数（最多 max_retries+1 次调用）
backoff_base = 2.0              # 指数退避基数（秒），0 = 不退避

[io]
input_path = "input/issues.jsonl"
output_path = "output/results.jsonl"
```

## 输入格式

`input/issues.jsonl`，每行一个 JSON 对象：

```json
{"issue_id": "10", "issue_category": "逻辑与算法实现", "issue_description": "..."}
```

只处理四类：`逻辑与算法实现` / `任务执行流程` / `指令与偏好遵循` / `安全与合规`。其它分类会被 Step a 过滤（不调用 LLM）。

## 运行

```bash
uv run negtopos
# 或覆盖参数
uv run negtopos --input x.jsonl --output y.jsonl --max-retries 3
uv run python -m negtopos --config config.toml
```

进度输出在 stderr，结果写 stdout 之外的 `output/results.jsonl`。

## 输出格式

每行一个记录：

```json
{
  "record_id": "fb_000001",
  "raw_feedback": "解题过程中途停止，...",
  "is_valid_feedback": true,
  "invalid_reason": null,
  "topic": "解题过程中途停止_import_unicodedata_category_ch_代码非法_bash_command...",
  "cluster_id": null,
  "llm_prompt": "...",
  "llm_response": "...",
  "negative_chunks": [
    {"chunk_id": 1, "text": "解题过程中途停止"}
  ],
  "positive_conditions": [
    {"source_chunk_id": 1, "target_capability": "multi_step_completion", "table_cap": false, "capability_category": "execution_control"}
  ],
  "has_valid_condition": true
}
```

字段说明：

| 字段 | 来源 |
|---|---|
| `record_id` | 顺序生成 `fb_000001`... |
| `raw_feedback` | 原始 `issue_description` |
| `is_valid_feedback` / `invalid_reason` | Step a 本地分类过滤 |
| `topic` | 从 issue 关键词派生的 snake_case slug（占位归一化） |
| `cluster_id` | 占位 `null`（真实聚类属 Step b，未实现） |
| `llm_prompt` | 实际下发的完整 prompt（含拼接的预设标签表） |
| `llm_response` | LLM 原始输出文本（最后一次尝试） |
| `negative_chunks` | 校验后的 Step c 切分结果（`chunk_id` + `text`） |
| `positive_conditions` | 每项含 `source_chunk_id` / `target_capability`（英文 snake_case **叶子**能力标签）/ `table_cap`（是否命中预设表叶子）/ `capability_category`（预设表中的大类段名之一） |
| `has_valid_condition` | 校验后的 Step c 结果 |

### target_capability 与预设标签表

`target_capability` 是从负面"没实现对的能力"映射出的"应该具备的正面能力"标签。预设标签表存放在 `src/negtopos/capabilities.txt`（单一事实来源），为**树形**格式：

- 顶格 snake_case 行 = **大类**（如 `code_generation` / `tool_use` / `planning` / `execution_control` / `error_recovery` / `other`）。
- 缩进带 `├──`/`└──` 的行 = **叶子标签**。

规则：

- `target_capability` 只能填叶子标签（或自创新叶子）；大类名只能作 `capability_category`，不能作 `target_capability`。
- 标签是预设叶子 → `table_cap=true`；自创新叶子 → `table_cap=false`。
- 大类集合从 txt **动态读取**（手改大类无需改代码）；`capability_category` 必须是其中之一。
- validator 严格校验 `table_cap` 与标签是否为预设叶子一致、`capability_category` 是否为合法大类，不一致会触发重试。
- 每条 issue 成功后，自创新叶子会按其 `capability_category` 自动追加到 `capabilities.txt` 对应大类段末尾（树形 `└──`），供后续 issue 复用——表随运行自增长。

## 流程

每条 issue：Step a（分类过滤）→ Step b（topic slug，cluster_id=null）→ Step c（LLM 切分+正向转换，校验 JSON，失败重试）。

- 重试触发：网络/HTTP 错误、JSON 解析失败、schema 校验失败。
- 重试时会把上次的校验错误作为提示回传给模型，帮助其自我修正。
- 重试耗尽后仍写入记录，`has_valid_condition=false`，保留最后一次 `llm_response` 便于排查（附加 `_last_error` / `_attempts` 调试字段）。

## 项目结构

```
negtopos/
├── pyproject.toml
├── config.toml
├── input/issues.jsonl          # 示例输入
├── output/results.jsonl        # 运行时生成
└── src/negtopos/
    ├── __init__.py
    ├── __main__.py             # python -m negtopos
    ├── config.py               # config.toml 加载
    ├── capabilities.py         # capabilities.txt 读写（预设标签表，单一来源）
    ├── capabilities.txt        # target_capability 预设标签表（随运行自增长）
    ├── prompt.py               # prompt 模板（拼接 capabilities.txt）
    ├── llm.py                  # httpx chat completion
    ├── validate.py             # JSON 解析 + schema 校验（含 table_cap 一致性）
    ├── pipeline.py             # 单 issue 编排（a/b/c + 重试 + 新标签入表）
    └── main.py                 # CLI 入口
```
