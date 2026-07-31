# Next.js Review MCP Service

基于 FastAPI + MCP（Model Context Protocol）的 Next.js 代码审查服务。
支持两种审查模式：

1. **GitLab MR 静态审查** — 通过 GitLab Merge Request webhook 触发，基于 Vercel React/Next.js 最佳实践对变更文件做静态规则检查，并将结果以行内评论和汇总评论的形式回写到 GitLab MR。
2. **GitHub PR LLM 审查** — 通过 GitHub Pull Request webhook 触发，使用 LLM（如 GLM-4.5-Air、GPT-4o）对变更文件进行智能代码审查，并将结果评论回写到 GitHub PR。

## 工作流程

### GitLab MR 静态审查

1. **Webhook 触发**
   - 当 MR 发生 `open` / `reopen` / `update` 事件时，GitLab 向服务的 `/webhook/gitlab` 发送 POST 请求。
   - 服务校验 `X-Gitlab-Token` 头部（与 `GITLAB_WEBHOOK_SECRET` 一致），异步启动审查任务后立即返回 200，避免 webhook 超时。

2. **拉取变更与代码审查**
   - 通过 GitLab API 获取 MR 基本信息、diff 列表、变更文件内容。
   - 解析统一 diff 定位新增行，对 `.ts` / `.tsx` / `.js` / `.jsx` 等文件运行静态规则检查。
   - 仅保留本次 MR 新增行上的发现，使审查聚焦于本次改动。

3. **结果回写 GitLab MR**
   - 按严重度（critical > high > medium > low）在对应代码行发表行内 diff 评论（默认上限 20 条）。
   - 发表一条汇总评论，含统计信息和按文件分组的问题表格。

### GitHub PR LLM 审查

1. **Webhook 触发**
   - 当 PR 发生 `opened` / `reopened` / `synchronize` 事件时，GitHub 向服务的 `/webhook/github` 发送 POST 请求。
   - 服务校验 `X-Hub-Signature-256` HMAC-SHA256 签名（与 `GITHUB_WEBHOOK_SECRET` 一致），异步启动审查任务后立即返回 200。

2. **拉取变更与 LLM 审查**
   - 通过 GitHub API 获取 PR 基本信息、变更文件列表和文件内容。
   - 对 `.ts` / `.tsx` / `.js` / `.jsx` 等文件，将 patch 和完整内容发送给 LLM 进行智能审查。
   - LLM 返回结构化的审查结果（行号、严重度、问题描述、修改建议），仅保留新增行上的发现。

3. **结果回写 GitHub PR**
   - 按严重度在对应代码行发表 review 评论（默认上限 20 条）。
   - 发表一条汇总评论，含统计信息和按文件分组的问题表格。

## 使用方式

### 环境准备

项目使用 [uv](https://docs.astral.sh/uv/) 管理依赖，要求 Python ≥ 3.10（推荐 3.11）。

```bash
# 安装依赖
uv sync
```

### 配置环境变量

配置使用 [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) 管理，自动从环境变量和 `.env` 文件读取。复制模板并填写：

```bash
cp .env.example .env
```

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `GITLAB_URL` | 否 | `https://gitlab.com` | GitLab 实例地址 |
| `GITLAB_TOKEN` | GitLab 审查必填 | | 具有 `api` 读权限与 MR 评论写权限的访问令牌 |
| `GITLAB_WEBHOOK_SECRET` | 推荐 | | GitLab Webhook 校验 token |
| `GITHUB_TOKEN` | GitHub 审查必填 | | GitHub Personal Access Token，需要 repo 读写权限 |
| `GITHUB_WEBHOOK_SECRET` | 推荐 | | GitHub Webhook 校验 secret |
| `OPENAI_API_KEY` | LLM 审查必填 | | LLM API Key（如智谱 API Key） |
| `OPENAI_BASE_URL` | 否 | | LLM API 地址，如 `https://open.bigmodel.cn/api/paas/v4` |
| `OPENAI_MODEL` | 否 | `GLM-4.5-Air` | 使用的模型名称 |
| `LLM_MAX_TOKENS` | 否 | `20480` | LLM 单次调用最大 token 数 |
| `LLM_TEMPERATURE` | 否 | `0.2` | LLM 温度参数 |
| `MAX_FILES_PER_REVIEW` | 否 | `50` | 单次 MR/PR 最多审查的文件数 |
| `MAX_INLINE_COMMENTS` | 否 | `20` | 单次 MR/PR 最多发表的行内评论数 |
| `MAX_FILE_SIZE_BYTES` | 否 | `200000` | 超过此大小的文件跳过审查 |
| `HOST` | 否 | `0.0.0.0` | 服务监听地址 |
| `PORT` | 否 | `8000` | 服务监听端口 |

### 本地启动

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

启动后可访问：
- `GET /` — 健康检查
- `GET /rules` — 列出所有审查规则
- `POST /webhook/gitlab` — GitLab webhook 入口
- `POST /webhook/github` — GitHub webhook 入口
- `POST /mcp` — MCP streamable HTTP 端点

### Docker 部署

```bash
docker build -t nextjs-review-mcp .
docker run -d -p 8000:8000 \
  --env-file .env \
  --name nextjs-review-mcp \
  nextjs-review-mcp
```

### 配置 GitLab Webhook

1. 进入目标项目 → **Settings** → **Webhooks**。
2. **URL** 填入 `http://<服务地址>:8000/webhook/gitlab`。
3. **Secret token** 填入与 `GITLAB_WEBHOOK_SECRET` 相同的值。
4. **Trigger** 勾选 **Merge request events**。
5. 点击 **Add webhook**，可用 **Test** → **Merge request events** 验证连通性。

### 配置 GitHub Webhook

1. 进入目标仓库 → **Settings** → **Webhooks** → **Add webhook**。
2. **Payload URL** 填入 `http://<服务地址>:8000/webhook/github`。
3. **Content type** 选择 `application/json`。
4. **Secret** 填入与 `GITHUB_WEBHOOK_SECRET` 相同的值。
5. **Trigger** 勾选 **Pull requests**。
6. 点击 **Add webhook**。

### 本地调试 GitHub Webhook

未配置 `GITHUB_WEBHOOK_SECRET` 时可跳过签名校验（仅限开发环境）：

```bash
curl -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -d '{
    "action": "opened",
    "number": 1,
    "repository": {
      "full_name": "owner/repo"
    }
  }'
```

### MCP 客户端接入

服务提供 streamable HTTP 传输的 MCP 端点，地址为 `http://<host>:8000/mcp`。

暴露的 5 个工具：

| 工具 | 说明 |
| --- | --- |
| `list_review_rules` | 列出所有可用的 Next.js 审查规则及其说明 |
| `review_code` | 对一段代码内容执行静态审查（无副作用） |
| `review_merge_request` | 审查指定 GitLab MR 并把结果评论回 MR |
| `review_github_pull_request` | 使用 LLM 审查指定 GitHub PR 并把结果评论回 PR |
| `post_mr_comment` | 向指定 GitLab MR 发表一条普通评论 |

## 开发指引

### 项目结构

```
.
├── main.py            # FastAPI 入口：路由、webhook、MCP 挂载、lifespan
├── mcp_server.py      # FastMCP 服务：工具定义与 MR/PR 审查编排
├── reviewer.py        # 静态审查规则与结果格式化
├── gitlab_client.py   # GitLab API v4 客户端
├── github_client.py   # GitHub API 客户端
├── llm_client.py      # LLM 客户端（基于 OpenAI 兼容 API）
├── config.py          # 配置管理（pydantic-settings）
├── pyproject.toml     # 项目与依赖声明（uv）
├── .env.example       # 环境变量模板
├── Dockerfile         # 容器镜像构建
└── tests/             # 单元测试
    ├── test_config.py
    ├── test_reviewer.py
    ├── test_gitlab_client.py
    ├── test_github_client.py
    ├── test_llm_client.py
    ├── test_mcp_server.py
    └── test_main.py
```

### 配置管理

配置基于 `pydantic-settings` 的 `BaseSettings`，具有以下特性：

- **自动读取**：从环境变量和 `.env` 文件自动读取配置，无需手动解析。
- **类型校验**：`int` / `float` 字段自动类型转换，非法值回退到默认值。
- **数据清洗**：字符串字段自动去除前后空白，`gitlab_url` 自动去除末尾斜杠。
- **不可变**：`frozen=True` 配置创建后不可修改。
- **单例模式**：通过 `get_settings()` 获取全局配置单例。

### 添加新的审查规则

在 [reviewer.py](file:///Users/bean/Documents/Learning/python-learning/my-python-service/reviewer.py) 中：

1. 实现一个检查函数，签名为 `(content: str, file_path: str) -> list[tuple[int, str]]`，返回 `(行号, 说明)` 列表。
2. 在 `RULES` 列表中添加一条 `Rule(...)` 记录，指定 `rule_id`、标题、严重度、描述、建议与检查函数。
3. 重启服务后，`list_review_rules` 工具与 `GET /rules` 接口会自动包含新规则。

### 运行测试

```bash
# 运行全部测试
uv run pytest -v

# 运行单个测试文件
uv run pytest tests/test_config.py -v
```

测试覆盖：
- `test_config.py` — 配置加载、环境变量覆盖、类型转换、不可变性
- `test_reviewer.py` — 12 条静态审查规则
- `test_gitlab_client.py` — GitLab API v4 客户端
- `test_github_client.py` — GitHub API 客户端
- `test_llm_client.py` — LLM 客户端（prompt 构建、响应解析、API 调用）
- `test_mcp_server.py` — MCP 工具与审查编排逻辑
- `test_main.py` — FastAPI 路由、webhook 处理、签名校验

### 本地调试 GitLab Webhook

可用 `curl` 模拟 GitLab MR 事件：

```bash
curl -X POST http://localhost:8000/webhook/gitlab \
  -H 'Content-Type: application/json' \
  -H 'X-Gitlab-Token: your-secret' \
  -d '{
    "object_kind": "merge_request",
    "object_attributes": {
      "iid": 123,
      "action": "open",
      "target_project_id": 456
    },
    "project": { "id": 456 }
  }'
```

### 验证 MCP 端点

```bash
curl -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## 注意事项

- **Python 版本**：MCP SDK 要求 Python ≥ 3.10，本项目使用 Python 3.11。
- **Webhook 异步执行**：审查在后台任务中执行，webhook 立即返回 200。若令牌无效或 API 调用失败，错误会记录到日志中，不会回写 MR/PR。
- **行内评论限量**：`MAX_INLINE_COMMENTS` 限制单次 MR/PR 的行内评论数（默认 20），超出部分仍会出现在汇总评论里。按严重度优先级发送，优先报告 critical / high 问题。
- **静态检查的局限**：GitLab MR 审查基于正则的轻量静态分析，不做 AST 解析与类型推导，存在一定的误报与漏报。GitHub PR 审查使用 LLM，审查质量取决于模型能力。
- **LLM 兼容性**：LLM 客户端基于 OpenAI 兼容 API，支持任何兼容 OpenAI API 格式的模型服务（如智谱 GLM、DeepSeek、Moonshot 等）。通过 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 配置。
- **MCP lifespan**：FastAPI 挂载的 MCP 子应用 lifespan 不会自动运行，需在 FastAPI 主应用的 lifespan 中通过 `mcp.session_manager.run()` 手动启动 session manager 的 task group，否则访问 `/mcp` 会报 `Task group is not initialized`。
- **Token 安全**：`GITLAB_TOKEN` / `GITHUB_TOKEN` / `OPENAI_API_KEY` 具有写权限，请不要提交到仓库、不要在客户端侧暴露。生产环境建议通过环境变量或密钥管理服务注入。
- **文件大小限制**：大于 `MAX_FILE_SIZE_BYTES` 的文件会被跳过，避免审查大文件拖慢响应。
- **删除文件**：已删除的文件不参与审查。
