# Next.js Review MCP Service

基于 FastAPI + MCP（Model Context Protocol）的 Next.js 代码审查服务。
通过 GitLab Merge Request webhook 触发审查，基于 Vercel React/Next.js 最佳实践对变更文件做静态检查，并将结果以行内评论和汇总评论的形式回写到 GitLab MR。

## 工作流程

1. **GitLab Webhook 触发**
   - 当 MR 发生 `open` / `reopen` / `update` 事件时，GitLab 向服务的 `/webhook/gitlab` 发送 POST 请求。
   - 服务校验 `X-Gitlab-Token` 头部（与 `GITLAB_WEBHOOK_SECRET` 一致），异步启动审查任务后立即返回 200，避免 webhook 超时。

2. **拉取变更与代码审查**
   - 通过 GitLab API 获取 MR 基本信息、diff 列表、变更文件内容。
   - 解析统一 diff 定位新增行，对 `.ts` / `.tsx` / `.js` / `.jsx` 等文件运行静态规则检查。
   - 仅保留本次 MR 新增行上的发现，使审查聚焦于本次改动。

3. **结果回写 GitLab MR**
   - 按严重度（critical > high > medium > low）在对应代码行发表行内 diff 评论（默认上限 20 条）。
   - 发表一条汇总评论，含统计信息和按文件分组的问题表格。

## 使用方式

### 环境准备

项目使用 [uv](https://docs.astral.sh/uv/) 管理依赖，要求 Python ≥ 3.10（推荐 3.11）。

```bash
# 安装依赖
uv sync
```

### 配置环境变量

复制模板并填写：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `GITLAB_URL` | 是 | GitLab 实例地址，如 `https://gitlab.com` |
| `GITLAB_TOKEN` | 是 | 具有 `api` 读权限与 MR 评论写权限的访问令牌 |
| `GITLAB_WEBHOOK_SECRET` | 推荐 | Webhook 校验 token，与 GitLab 中配置的 Secret token 一致 |
| `MAX_FILES_PER_REVIEW` | 否 | 单次 MR 最多审查的文件数，默认 50 |
| `MAX_INLINE_COMMENTS` | 否 | 单次 MR 最多发表的行内评论数，默认 20 |
| `MAX_FILE_SIZE_BYTES` | 否 | 超过此大小的文件跳过审查，默认 200000 |
| `HOST` | 否 | 服务监听地址，默认 `0.0.0.0` |
| `PORT` | 否 | 服务监听端口，默认 8000 |

### 本地启动

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

启动后可访问：
- `GET /` — 健康检查
- `GET /rules` — 列出所有审查规则
- `POST /webhook/gitlab` — GitLab webhook 入口
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

### MCP 客户端接入

服务提供 streamable HTTP 传输的 MCP 端点，地址为 `http://<host>:8000/mcp`。

暴露的 4 个工具：

| 工具 | 说明 |
| --- | --- |
| `list_review_rules` | 列出所有可用的 Next.js 审查规则及其说明 |
| `review_code` | 对一段代码内容执行静态审查（无副作用） |
| `review_merge_request` | 审查指定 GitLab MR 并把结果评论回 MR |
| `post_mr_comment` | 向指定 GitLab MR 发表一条普通评论 |

## 开发指引

### 项目结构

```
.
├── main.py            # FastAPI 入口：路由、webhook、MCP 挂载、lifespan
├── mcp_server.py      # FastMCP 服务：工具定义与 MR 审查编排
├── reviewer.py        # 静态审查规则与结果格式化
├── gitlab_client.py   # GitLab API v4 客户端
├── config.py          # 环境变量配置
├── pyproject.toml     # 项目与依赖声明（uv）
├── .env.example       # 环境变量模板
└── Dockerfile         # 容器镜像构建
```

### 添加新的审查规则

在 [reviewer.py](file:///Users/bean/Documents/Learning/python-learning/my-python-service/reviewer.py) 中：

1. 实现一个检查函数，签名为 `(content: str, file_path: str) -> list[tuple[int, str]]`，返回 `(行号, 说明)` 列表。
2. 在 `RULES` 列表中添加一条 `Rule(...)` 记录，指定 `rule_id`、标题、严重度、描述、建议与检查函数。
3. 重启服务后，`list_review_rules` 工具与 `GET /rules` 接口会自动包含新规则。

### 本地调试 webhook

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

- **Python 版本**：MCP SDK 要求 Python ≥ 3.10，本项目使用 Python 3.11。原项目的 Python 3.9 已升级。
- **Webhook 异步执行**：审查在后台任务中执行，webhook 立即返回 200。若 GitLab 令牌无效或 API 调用失败，错误会记录到日志中，不会回写 MR。
- **行内评论限量**：`MAX_INLINE_COMMENTS` 限制单次 MR 的行内评论数（默认 20），超出部分仍会出现在汇总评论里。按严重度优先级发送，优先报告 critical / high 问题。
- **静态检查的局限**：当前审查基于正则的轻量静态分析，不做 AST 解析与类型推导，存在一定的误报与漏报。规则覆盖的是 Vercel 最佳实践中最常见的模式（barrel 导入、await 瀑布、组件重渲染、SSR 模块状态等）。
- **MCP lifespan**：FastAPI 挂载的 MCP 子应用 lifespan 不会自动运行，需在 FastAPI 主应用的 lifespan 中通过 `mcp.session_manager.run()` 手动启动 session manager 的 task group，否则访问 `/mcp` 会报 `Task group is not initialized`。
- **Token 安全**：`GITLAB_TOKEN` 具有项目 API 写权限，请不要提交到仓库、不要在客户端侧暴露。生产环境建议通过环境变量或密钥管理服务注入。
- **文件大小限制**：大于 `MAX_FILE_SIZE_BYTES` 的文件会被跳过，避免审查大文件拖慢响应。
- **删除文件**：已删除的文件不参与审查。
