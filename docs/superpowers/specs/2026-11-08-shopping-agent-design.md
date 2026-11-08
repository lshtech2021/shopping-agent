# Shopping Agent Design

日期：2026-11-08  
状态：已确认（按会话方案开工）  
形态：浏览器插件 + 后端 Agent + 可选只读前端

## 1. 目标

构建可产品化的智能选品助手：用户在 Chrome Side Panel 用自然语言描述需求，后端 Agent 通过窄域工具闭环完成搜索、抓取、比较与推荐；可选 Web 控制台仅用于查看会话、商品缓存与推荐结果。

对比「Claude Code + browse use」：效果来自 **Agent 脑 + 浏览器手眼**；本项目自研脑（LangGraph），手眼由 Extension 提供，数据真相在后端。

## 2. 架构

```
用户（Side Panel 对话）
        │
        ▼
┌───────────────────┐
│  Backend Agent     │  LangGraph：规划 / 工具循环 / 中断恢复
└─────────┬─────────┘
          ├──── PostgreSQL（会话 / 商品 / 推荐 / jobs）
          └──── WebSocket ──► Extension Tool Host
                                   │
                                   ▼
                              京东 / eBay 标签页

可选 Web 前端 ◄── 只读查看
```

- **脑在服务端**：不把编排逻辑写死在 Extension 状态机里。
- **手在用户浏览器**：服务端不能直接打开京东页；browse 工具经 WS 下发为 BrowserJob，由 Extension 执行后回传。
- **库是真相**：会话、商品缓存、推荐快照均落库；Agent 与前端都读同一数据。

## 3. 技术选型

| 层 | 选择 |
|----|------|
| Agent | LangGraph（Python） |
| API | FastAPI + WebSocket |
| DB | PostgreSQL（单测可用 memory；Docker Compose 提供本地实例） |
| Extension | Chrome MV3 + Side Panel |
| 可选前端 | 轻量静态/React 只读页（后期） |
| LLM | OpenAI 兼容接口（可配置） |

## 4. 设计模式

1. **ReAct / tool-calling**：`reason → tool → reason → …`  
2. **Remote Tool Host**：browse 工具异步交给 Extension  
3. **Human-in-the-loop**：需求澄清、验证码、限流时 interrupt，用户处理后 resume  
4. **配额在 Host，不在 Prompt**：browse 调用硬限制 + 间隔抖动  

## 5. 最小 Tool 面（9）

### 记忆类
1. `get_session` — 需求、站点、库存摘要、状态  
2. `update_requirements` — 合并预算 / must / prefer / 站点  
3. `query_inventory` — 查已缓存商品（少回站点）  

### 浏览类（Extension）
4. `search` — 一站一词打开搜索页  
5. `scrape_listing` — 解析当前列表并入库  
6. `next_page` — 翻一页  
7. `fetch_detail` — 打开一个详情并入库  

浏览统一返回：`status: ok | rate_limited | captcha | login_required | parse_error | blocked`

### 收口类
8. `recommend` — 写入推荐快照与助手回复  
9. `ask_user` — 提问或等待用户处理风控后继续  

## 6. Browse 配额（默认）

- 每轮每站：`search ≤ 2`，`next_page ≤ 2`，`fetch_detail ≤ 5`  
- 调用间隔 ≥ 2s（可抖动）  
- 非 `ok`：中断，禁止自动连刷  

## 7. 非目标

- 不做固定「搜 3 页 → 选 5 候选 → 一次 LLM」死流水线当主路径  
- 不做通用 DOM click agent  
- 不绕过验证码 / 不打代理池硬刚风控  
- 可选前端不做第二套对话 Agent  

## 8. 仓库布局

```
shopping-agent/
  apps/api/              FastAPI + LangGraph
  apps/web/              可选只读控制台
  packages/domain/       领域模型与规则
  packages/agent/        Graph、prompts、tools
  packages/extension/    Chrome MV3
  docs/                  设计与计划
  tests/
```

## 9. 分阶段

| Phase | 内容 |
|-------|------|
| 0 | 骨架：API echo Agent、Extension WS 心跳、Side Panel 能对话 |
| 1 | 记忆工具 + mock 浏览闭环（fixture）+ recommend |
| 2 | 京东真浏览 + 配额 + 风控 interrupt |
| 3 | eBay + 多轮追问 |
| 4 | Checkpointer、日志、只读 Web、评测集 |

## 10. Approaches（已选）

1. **Claude Code 当产品脑** — 质量好但不可产品化 → 否  
2. **自研完整通用 coding agent** — 过重 → 否  
3. **购物域 LangGraph Agent + Extension Tool Host**（已选）— 可产品化，工具面窄，可与 MCP 对照实验  

## 11. 成功标准（Phase 1）

- 用户一句话需求 → Agent 调用 mock search/scrape/detail → 写出 ≥1 条推荐并落库  
- Side Panel 能看到助手回复与推荐卡片数据  
- browse 工具失败（mock rate_limited）会 interrupt，resume 后可继续  
