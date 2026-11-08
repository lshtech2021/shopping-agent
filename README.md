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
| 2 京东真浏览 | 待办 |
| 3 eBay / 多轮 | 待办 |
| 4 控制台 / 评测 | 待办 |

## 测试

```powershell
cd apps/api
$env:PYTHONPATH = (Get-Location).Path
python -m pytest -q
```
