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
| Agent | LangGraph 工具循环（LLM）+ 规则策略（离线/基线） |
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

## 6. Browse 配额与风控（默认）

- 每轮每站：`search ≤ 2`，`next_page ≤ 2`，`fetch_detail ≤ 5`；**每个 Agent 回合开始时重置**  
- 同站调用间隔 ≥ 2s + 抖动（`SA_BROWSE_MIN_INTERVAL_S` / `SA_BROWSE_JITTER_S`，服务端节流，仅真实浏览模式）  
- 站点出现 `rate_limited / captcha / login_required`：写入 `session.blocked_sites`，该站后续一切 browse 调用被**守卫拒绝且不消耗配额**，直到用户 resume（resume 清除）  
- 任何非 `ok` 的浏览结果（含 Extension 离线、job 超时/断线）：本回合中断，禁止自动连刷  
- `quota_exceeded` 不是风控：LLM 策略把它回传给模型，让其用已有数据收尾  

这些约束全部在 `ToolBox`（Host 层），与具体策略（LLM / 规则）无关。

## 6.1 Agent 策略

同一套 9 个工具，两种可互换策略（`app/agent_runner.py` 分派）：

| 策略 | 触发 | 说明 |
|------|------|------|
| `llm_agent`（LangGraph） | 设置了 `SA_LLM_API_KEY` | `model ⇄ tools` 循环；模型决定做什么，`ToolBox` 决定允许什么；风控结果确定性中断；`agent_max_steps` 封顶；纯文本回复视为澄清提问 |
| 规则策略 | 默认（无 key） | 确定性流水线：作为离线演示、评测基线与 LLM 故障对照 |

LLM 调用失败不会 500：回合以 `agent_error` 中断，用户可重试。

`recommend` 只接受会话内已有的 `product_id`；全部无效时返回错误给模型，不结束回合。

## 6.2 安全与并发

- WebSocket `/ws/extension` 只接受无 Origin（非浏览器客户端）或 `chrome-extension://` Origin，防止任意网页冒充 Extension。  
- CORS 仅允许控制台同源与 `chrome-extension://`，不带凭据。  
- 同一会话同一时刻只允许一个回合；并发请求返回 `409`。  
- Extension 断线时，其在途 job 立即以 `extension_disconnected` 失败，不再傻等超时。  
- MV3 service worker 每 20s 发 ping 保活。  

## 6.3 需求澄清（interview gate）

开始浏览前，若**关键过滤条件缺失或互相冲突**，Agent 先一次性列出结构化澄清问题，收集齐答案再执行：

- 触发条件（规则策略）：即将发起搜索且 ① 无预算 ② 无品类 ③ 预算与内存硬约束冲突（如 ¥3000 配 32G）。已在缓存可重排（不搜索）时不打扰；用户明确说「跳过/直接开始」也不问。  
- 形态：`MessageResponse.clarify = [{id, question, options[]}]`，Side Panel 渲染成可点选项 + 自由输入，一次提交全部答案（也可跳过）。  
- 回传：`POST messages` 带 `answers: {question_id: 值}`，服务端并入需求后**本轮跳过文本重解析**（防止原始需求覆盖用户答案），继续执行。  
- LLM 策略同样有 `clarify` 工具（由模型决定何时问、问什么）。  

## 7. 非目标

- 不做固定「搜 3 页 → 选 5 候选 → 一次 LLM」死流水线当主路径  
- 不做通用 DOM click agent  
- 不绕过验证码 / 不打代理池硬刚风控  
- 可选前端不做第二套对话 Agent  

## 8. 仓库布局

```
shopping-agent/
  apps/api/
    app/                 main / agent_runner（分派+规则策略）/ llm_agent（LangGraph）
                         tools（ToolBox：9 工具 + 守卫/配额/节流）/ store / ws_hub / trace
    tests/  evals/       pytest 与冒烟评测
  apps/web/              只读控制台（静态页）
  packages/extension/    Chrome MV3（Side Panel / 后台 WS / JD·eBay 解析）
  docs/                  设计与计划
  docker-compose.yml     本地 PostgreSQL
```

> 早期规划里的 `packages/domain`、`packages/agent` 未单独拆包：领域规则与图都很小，放在 `apps/api/app` 内更直接。

## 9. 分阶段

| Phase | 内容 |
|-------|------|
| 0 | 骨架：API echo Agent、Extension WS 心跳、Side Panel 能对话 |
| 1 | 记忆工具 + mock 浏览闭环（fixture）+ recommend |
| 2 | 京东真浏览 + 配额 + 风控 interrupt |
| 3 | eBay + 多轮追问 |
| 4 | 持久化（PostgreSQL JSONB）、日志、只读 Web、评测集 |
| 5 | 加固：LangGraph 工具循环、风控守卫/节流、需求解析修正、并发/WS 安全 |

## 9.1 已知限制 / 后续

- 单 API 进程假设：会话读写走内存工作集，写穿 PostgreSQL；多进程需改为按行读取 + 行锁。  
- 整会话 JSONB 整行重写：会话很大时应拆表（products / messages / logs）。  
- 同步 psycopg 调用在事件循环内执行（本地毫秒级）；规模化需连接池 + 线程/异步驱动。  
- Job 不跨 WS 重连重投递（断线即失败，由用户 resume 重试）。  
- 未做 LangGraph checkpointer；中断恢复靠会话状态 + 重新规划。  
- LLM 策略尚未用真实模型做过评测，仅有假模型回归测试；上线前需用 `evals/` 扩充真实用例对比两种策略。  
- 控制台与 API 无鉴权，默认只监听本机。

## 10. Approaches（已选）

1. **Claude Code 当产品脑** — 质量好但不可产品化 → 否  
2. **自研完整通用 coding agent** — 过重 → 否  
3. **购物域 LangGraph Agent + Extension Tool Host**（已选）— 可产品化，工具面窄，可与 MCP 对照实验  

## 11. 成功标准（Phase 1）

- 用户一句话需求 → Agent 调用 mock search/scrape/detail → 写出 ≥1 条推荐并落库  
- Side Panel 能看到助手回复与推荐卡片数据  
- browse 工具失败（mock rate_limited）会 interrupt，resume 后可继续  
