"""Shopping tools exposed to the generic runtime as typed ToolSpecs."""
from __future__ import annotations

from typing import Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools import ToolResult, ToolSpec
from app.domains.shopping.prompt import blocked_text
from app.tools import BLOCKING, ToolBox


class _NoArgs(BaseModel):
    pass


class _UpdateReq(BaseModel):
    budget_cny: Optional[float] = Field(None, description="预算上限，人民币")
    budget_min_cny: Optional[float] = Field(None, description="预算下限，人民币")
    budget_unlimited: Optional[bool] = Field(None, description="是否不限预算")
    category: Optional[str] = Field(None, description="品类，如 手机 / 跑鞋 / 笔记本")
    use_case: Optional[str] = Field(None, description="用途，如 通勤 / 送礼")
    must_have: Optional[list[str]] = Field(
        None, description="硬性要求，如 ['防水']；若是内存请写成 '<数字>G内存'"
    )
    preferences: Optional[list[str]] = Field(None, description="偏好（非硬性）")


class _QueryInv(BaseModel):
    limit: int = Field(10, ge=1, le=30)


class _Search(BaseModel):
    site: str = Field(description="jd 或 ebay")
    query: str = Field(description="该站点的搜索词（eBay 用英文）")
    category_id: Optional[str] = Field(None, description="eBay 品类 ID，如 177")
    max_price: Optional[float] = Field(
        None, description="预算上限（目标站点币种）"
    )
    exclude_terms: list[str] = Field(
        default_factory=list, description="追加到搜索词的排除词，如 -book"
    )
    sort: Optional[str] = Field(None, description="price_asc / price_desc")


class _Scrape(BaseModel):
    site: Optional[str] = Field(None, description="默认当前搜索站点")
    page: Optional[int] = Field(None, ge=1)


class _NextPage(BaseModel):
    site: Optional[str] = None


class _Detail(BaseModel):
    product_id: str = Field(description="query_inventory 返回的 product_id")


class _Pick(BaseModel):
    product_id: str
    reason: str = Field(description="具体推荐理由，引用价格/规格")


class _Recommend(BaseModel):
    picks: list[_Pick] = Field(min_length=1, max_length=3)
    answer: str = Field(description="给用户的最终回复（中文，简洁）")


class _AskQuestion(BaseModel):
    id: str = Field(
        description="问题标识，如 budget / category / budget_ram_conflict"
    )
    field: Literal[
        "budget",
        "category",
        "use_case",
        "must_have",
        "preferences",
        "other",
    ] = "other"
    question: str
    options: list[str] = Field(
        default_factory=list, description="候选项；用户也可自由输入"
    )


class _AskUser(BaseModel):
    questions: list[_AskQuestion] = Field(min_length=1, max_length=5)
    reason: str = "need_user"


def _compact_browse(result: dict[str, Any]) -> dict[str, Any]:
    """What the model needs to see — not the raw page payload."""
    keep = (
        "status",
        "error",
        "accepted",
        "product_count",
        "page",
        "no_more",
        "no_results",
        "result_count",
        "has_next",
        "site",
        "reason",
        "used",
        "limit",
    )
    out = {k: result[k] for k in keep if k in result}
    if "product" in result:
        out["product"] = result["product"]
    return out


def _plan_meta(
    context: Any,
    *evidence: str,
    done: bool = False,
    step_id: str | None = None,
) -> dict[str, Any]:
    resources = getattr(context, "resources", None) or {}
    step_id = step_id or resources.get("plan_step_id")
    if not step_id:
        return {}
    meta: dict[str, Any] = {
        "plan_step_id": str(step_id),
        "evidence": [item for item in evidence if item],
    }
    if done:
        meta["plan_step_done"] = True
    return meta


def _browse_result(
    result: dict[str, Any],
    tool: str,
    context: Any = None,
    *,
    step_id: str | None = None,
    done: bool = False,
) -> ToolResult:
    data = _compact_browse(result)
    ok = result.get("status") == "ok"
    evidence = [f"{tool}:{result.get('status')}"] if ok else []
    if "result_count" in result:
        evidence.append(f"result_count={result.get('result_count')}")
    elif isinstance(result.get("items"), list):
        evidence.append(f"item_count={len(result['items'])}")
    if "accepted" in result:
        evidence.append(f"accepted={result.get('accepted')}")
    meta = _plan_meta(
        context, *evidence, done=done and ok, step_id=step_id
    )
    status = str(result.get("status") or "")
    if status in BLOCKING and result.get("error") != "quota_exceeded":
        site = str(result.get("site") or "")
        question = blocked_text(site, result, tool)
        pending = {
            "question": question,
            "reason": status,
            "options": [],
            "metadata": {},
        }
        return ToolResult(
            call_id="",
            tool=tool,
            ok=False,
            data=data,
            error=result.get("error"),
            meta=meta,
            control="wait_user",
            output={
                "question": question,
                "reason": status,
                "pending_user": pending,
            },
        )
    return ToolResult(
        call_id="",
        tool=tool,
        ok=status == "ok",
        data=data,
        error=result.get("error"),
        meta=meta,
    )


def _langchain_tool(spec: ToolSpec) -> StructuredTool:
    async def _stub(**_kwargs: Any) -> str:
        return "executed by AgentRuntime"

    return StructuredTool.from_function(
        coroutine=_stub,
        name=spec.name,
        description=spec.description,
        args_schema=spec.args_model,
    )


def build_shopping_tools(
    box: ToolBox, state: dict[str, Any]
) -> list[ToolSpec]:
    def step_for(tool: str) -> str | None:
        role = {
            "search": "search",
            "scrape_listing": "search",
            "next_page": "search",
            "fetch_detail": "verify",
            "query_inventory": "verify",
            "recommend": "recommend",
        }.get(tool)
        if role is None:
            return None
        plan = state.get("plan")
        if not isinstance(plan, dict):
            return None
        metadata = plan.get("metadata")
        if not isinstance(metadata, dict):
            return None
        roles = metadata.get("step_roles")
        if not isinstance(roles, dict):
            return None
        step_id = roles.get(role)
        return str(step_id) if step_id else None

    async def get_session(_args: _NoArgs, _context: Any) -> ToolResult:
        info = box.get_session()
        info.pop("recommendations", None)
        return ToolResult(
            call_id="", tool="get_session", ok=True, data=info
        )

    async def update_requirements(
        args: _UpdateReq, _context: Any
    ) -> ToolResult:
        explicit_fields = {
            field: getattr(args, field)
            for field in args.model_fields_set
            if getattr(args, field) is not None
        }
        return ToolResult(
            call_id="",
            tool="update_requirements",
            ok=True,
            data=box.update_requirements(**explicit_fields),
            meta=_plan_meta(_context, "requirements_updated"),
        )

    async def query_inventory(
        args: _QueryInv, _context: Any
    ) -> ToolResult:
        judge_error = await box.ensure_fit()
        result = box.query_inventory(limit=args.limit)
        if judge_error:
            result["judge_error"] = judge_error
        return ToolResult(
            call_id="",
            tool="query_inventory",
            ok=True,
            data=result,
            meta=_plan_meta(
                _context,
                f"count={result.get('count')}",
                done=True,
                step_id=step_for("query_inventory"),
            ),
        )

    async def search(args: _Search, _context: Any) -> ToolResult:
        filters: dict[str, Any] = {}
        if args.category_id:
            filters["categoryId"] = args.category_id
        if args.max_price is not None:
            filters["maxPrice"] = args.max_price
        if args.exclude_terms:
            filters["excludeTerms"] = args.exclude_terms
        if args.sort:
            filters["sort"] = args.sort
        return _browse_result(
            await box.search(args.site, args.query, **filters),
            "search",
            _context,
            step_id=step_for("search"),
        )

    async def scrape_listing(
        args: _Scrape, _context: Any
    ) -> ToolResult:
        return _browse_result(
            await box.scrape_listing(args.site, args.page),
            "scrape_listing",
            _context,
            step_id=step_for("scrape_listing"),
            done=True,
        )

    async def next_page(args: _NextPage, _context: Any) -> ToolResult:
        return _browse_result(
            await box.next_page(args.site),
            "next_page",
            _context,
            step_id=step_for("next_page"),
        )

    async def fetch_detail(args: _Detail, _context: Any) -> ToolResult:
        return _browse_result(
            await box.fetch_detail(args.product_id),
            "fetch_detail",
            _context,
            step_id=step_for("fetch_detail"),
            done=True,
        )

    async def recommend(args: _Recommend, _context: Any) -> ToolResult:
        picks = [pick.model_dump() for pick in args.picks]
        await box.ensure_fit([str(pick["product_id"]) for pick in picks])
        result = box.recommend(picks, args.answer, commit=False)
        if result.get("ok"):
            recommended_ids = [
                str(item.get("product_id") or "")
                for item in result.get("recommendations") or []
            ]
            return ToolResult(
                call_id="",
                tool="recommend",
                ok=True,
                data=result,
                meta=_plan_meta(
                    _context,
                    "recommended=" + ",".join(recommended_ids),
                    step_id=step_for("recommend"),
                ),
                control="finish",
                output={
                    "reply": result.get("answer") or args.answer,
                    "recommendations": result.get("recommendations") or [],
                    "picks": picks,
                },
            )
        return ToolResult(
            call_id="",
            tool="recommend",
            ok=False,
            data=result,
            error=result.get("error"),
        )

    async def ask_user(args: _AskUser, _context: Any) -> ToolResult:
        questions = [question.model_dump() for question in args.questions]
        result = box.ask_user(questions, args.reason)
        question = questions[0]["question"]
        pending = {
            "question": question,
            "reason": result["reason"],
            "options": [],
            "metadata": {"questions": questions},
        }
        return ToolResult(
            call_id="",
            tool="ask_user",
            ok=True,
            data=result,
            control="wait_user",
            output={
                "question": question,
                "reason": result["reason"],
                "pending_user": pending,
            },
        )

    return [
        ToolSpec(
            name="get_session",
            description="查看会话：需求、站点、库存摘要、配额。",
            args_model=_NoArgs,
            handler=get_session,
            read_only=True,
            parallel_safe=True,
        ),
        ToolSpec(
            name="update_requirements",
            description=(
                "仅写入用户明确陈述或已通过 ask_user 回复确认的结构化需求（预算、品类、用途、硬性要求、偏好）；"
                "不得自行添加用户没说过的条件。"
            ),
            args_model=_UpdateReq,
            handler=update_requirements,
        ),
        ToolSpec(
            name="query_inventory",
            description=(
                "按当前硬约束查询已缓存商品（不访问站点）。"
                "返回 product_id/标题/价格/是否已抓详情。"
            ),
            args_model=_QueryInv,
            handler=query_inventory,
            read_only=True,
            parallel_safe=True,
        ),
        ToolSpec(
            name="search",
            description="在一个站点打开一个搜索词的结果页。之后需 scrape_listing 才会入库。",
            args_model=_Search,
            handler=search,
            read_only=False,
            idempotent=False,
        ),
        ToolSpec(
            name="scrape_listing",
            description="解析当前搜索结果页并把商品写入缓存。",
            args_model=_Scrape,
            handler=scrape_listing,
            read_only=False,
            idempotent=True,
        ),
        ToolSpec(
            name="next_page",
            description="翻到下一页并入库（每站每轮最多 2 次）。",
            args_model=_NextPage,
            handler=next_page,
            read_only=False,
            idempotent=False,
        ),
        ToolSpec(
            name="fetch_detail",
            description="打开一个商品详情页，补全规格与价格（每站每轮最多 5 次）。",
            args_model=_Detail,
            handler=fetch_detail,
            read_only=False,
            idempotent=True,
            parallel_safe=True,
        ),
        ToolSpec(
            name="recommend",
            description="提交最终推荐（1-3 个已缓存商品）并结束本轮。",
            args_model=_Recommend,
            handler=recommend,
        ),
        ToolSpec(
            name="ask_user",
            description=(
                "需要补充需求、由用户做决定、或没有商品满足约束时使用。"
                "一次性列出全部结构化问题；每题可给候选项，用户也可自由输入。调用后结束本轮。"
            ),
            args_model=_AskUser,
            handler=ask_user,
        ),
    ]


def to_langchain_tools(specs: list[ToolSpec]) -> list[StructuredTool]:
    return [_langchain_tool(spec) for spec in specs]
