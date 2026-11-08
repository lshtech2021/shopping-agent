# Shopping Agent

浏览器插件 + 后端 Agent + 可选只读前端的智能选品助手。

Agent（LangGraph）在服务端规划；Chrome Extension 通过 WebSocket 执行浏览类工具；会话与商品缓存在数据库，可在控制台查看。

## 文档

- 设计：`docs/superpowers/specs/2026-11-08-shopping-agent-design.md`
- 计划：`docs/superpowers/plans/2026-11-08-shopping-agent-plan.md`

## 快速开始（Phase 0+）

```powershell
cd apps/api
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8100
```

加载扩展：Chrome → 开发者模式 → 加载 `packages/extension`。

## 阶段

| Phase | 状态 |
|-------|------|
| 0 骨架（echo + WS） | 完成 |
| 1 mock 闭环 | 完成 |
| 2 京东真浏览 | 完成 |
| 3 eBay / 多轮 | 完成 |
| 4 控制台 / 评测 | 完成 |

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

只读查看会话列表、推荐、对话与商品缓存。

## 京东/eBay 真浏览

1. 启动 API：`uvicorn app.main:app --reload --port 8100`
2. 设置 `SA_MOCK_BROWSER=false`（或在 `.env` 中配置）
3. Chrome 加载 `packages/extension`，确认 Side Panel 显示 `WS: connected`
4. 在 Side Panel 发起选品；extension 会打开站点标签页执行 search/scrape/detail
5. 若出现验证码/空白页，会话会 interrupt，处理后点「继续」
