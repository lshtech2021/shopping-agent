# Shopping Agent

浏览器插件 + 后端 Agent + 可选只读前端的智能选品助手。

Agent（LangGraph）在服务端规划；Chrome Extension 通过 WebSocket 执行浏览类工具；会话与商品缓存在数据库，可在控制台查看。

## 文档

- 设计：`docs/superpowers/specs/2026-11-08-shopping-agent-design.md`
- 计划：`docs/superpowers/plans/2026-11-08-shopping-agent-plan.md`

## 快速开始（Phase 0+）

```powershell
# 1) PostgreSQL（Docker）
docker compose up -d postgres

# 2) API
cd apps/api
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8100
```

默认数据库：`postgresql://shopping:shopping@localhost:5432/shopping_agent`  
单测使用内存库；持久化测试需设置 `SA_TEST_DATABASE_URL`。
没有 Postgres 时可临时 `SA_DATABASE_URL=memory` 启动（重启即丢会话）。

加载扩展：Chrome → 开发者模式 → 加载 `packages/extension`。

## 阶段

| Phase | 状态 |
|-------|------|
| 0 骨架（echo + WS） | 完成 |
| 1 mock 闭环 | 完成 |
| 2 京东真浏览 | 完成 |
| 3 eBay / 多轮 | 完成 |
| 4 控制台 / 评测 | 完成 |
| 5 加固（LLM 工具循环、风控守卫、并发/安全） | 完成 |

## Agent 策略

- 未设置 `SA_LLM_API_KEY`：规则策略（确定性流水线，离线可跑、作评测基线）
- 设置后：LangGraph 工具循环，模型决策、`ToolBox` 兜底（配额 / 风控锁定 / 推荐校验）
- 两者共用同一套 9 个工具与同一份执行日志；`GET /health` 的 `llm_enabled` 可确认当前策略

## 需求澄清

开始搜索前，若缺预算 / 缺品类、或预算与内存硬约束冲突，Agent 会一次性返回结构化澄清问题（`clarify` 字段），Side Panel 渲染成可点选项 + 自由输入；提交答案后继续执行，也可「跳过，直接开始」。已有缓存可重排时不打扰。

## 测试

```powershell
# API
cd apps/api
$env:PYTHONPATH = (Get-Location).Path
python -m pytest -q

# Smoke evals (mock browser)
python evals/run_smoke.py

# Extension parsers
cd ..\..\packages\extension
npm install
npm test
```

## 控制台

启动 API 后打开：http://127.0.0.1:8100/console/

只读查看会话列表、执行日志、推荐、对话与商品缓存。

日志：API 进程 stdout（`SA_LOG_LEVEL`）+ 会话 `execution_logs`（控制台 / Side Panel 可见）。Extension 浏览步骤会通过 `extension_logs` 回传并合并进会话时间线。

## 京东/eBay 真浏览

1. 启动 API：`uvicorn app.main:app --reload --port 8100`
2. 设置 `SA_MOCK_BROWSER=false`（或在 `.env` 中配置）
3. Chrome 加载 `packages/extension`，确认 Side Panel 显示 `WS: connected`
4. 在 Side Panel 发起选品；extension 会打开站点标签页执行 search/scrape/detail
5. 若出现验证码/空白页，会话会 interrupt，处理后点「继续」
