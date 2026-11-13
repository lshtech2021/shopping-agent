"""Shopping tools exposed to the generic runtime as typed ToolSpecs."""
from __future__ import annotations

from typing import Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app import requirement_review
from app.agent.tools import ToolResult, ToolSpec
from app.domains.shopping.adapters import get_adapter
from app.domains.shopping.prompt import blocked_text
from app.store import Session, store
from app.tools import BLOCKING, ToolBox
from app.trace import log_event


async def _review_requirements(
    requirements: dict[str, Any],
    session: Session | None = None,
):
    """Indirection point so tests can replace the LLM reviewer."""

    def on_event(stage: str, data: dict[str, Any]) -> None:
        if session is not None:
            log_event(
                session,
                (
                    "需求评审 LLM 调用"
                    if stage == "llm.call"
                    else "需求评审降级"
                ),
                stage=stage,
                source="llm",
                level="info" if stage == "llm.call" else "warning",
                duration_ms=data.get("duration_ms"),
                data=data,
            )

    return await requirement_review.review_requirements(
        requirements, on_event=on_event
    )


class _NoArgs(BaseModel):
    pass


class _UpdateReq(BaseModel):
    budget: Optional[float] = Field(
        None,
        description="预算上限（金额单位始终是会话 settings.currency）",
    )
    budget_min: Optional[float] = Field(
        None,
        description="预算下限（金额单位始终是会话 settings.currency）",
    )
    budget_unlimited: Optional[bool] = Field(None, description="是否不限预算")
    category: Optional[str] = Field(
        None, description="品类；使用用户自己的措辞，不预设类别"
    )
    use_case: Optional[str] = Field(None, description="用途；使用用户自己的措辞")
    must_have: Optional[list[str]] = Field(
        None,
        description=(
            "完整的硬性要求列表；提供时会替换旧列表。每项必须是商品页"
            "可核验的客观属性（材质、型号、容量、尺寸、可见特征等），"
            "不要放“性能足够高”“好用”等主观描述"
        ),
    )
    preferences: Optional[list[str]] = Field(
        None,
        description="偏好（非硬性）；内容由用户需求决定",
    )


class _QueryInv(BaseModel):
    limit: int = Field(10, ge=1, le=30)


class _Search(BaseModel):
    query: str = Field(
        description="当前会话站点的搜索词。遵循系统提示中的站点搜索规则。"
    )
    category_id: Optional[str] = Field(
        None,
        description="eBay 品类 ID；仅在当前需求对应某个站点品类时填写，不预设",
    )
    exclude_terms: list[str] = Field(
        default_factory=list,
        description="排除词；仅在用户要求排除时填写，不要自行添加",
    )
    force: bool = Field(
        False,
        description=(
            "仅在用户明确要求重新搜索，或计划/重规划明确要求新搜索时设为 true；"
            "recommend 步骤已开始后不要搜索"
        ),
    )
    sort: Optional[str] = Field(None, description="price_asc / price_desc")
    buy_it_now_only: bool = Field(
        True,
        description="eBay 硬预算搜索默认只看 Buy It Now，避免最终价未知的拍卖",
    )


class _Scrape(BaseModel):
    page: Optional[int] = Field(None, ge=1)


class _NextPage(BaseModel):
    force: bool = Field(
        False,
        description=(
            "仅在用户明确要求继续翻页，或计划/重规划明确要求时设为 true；"
            "recommend 步骤已开始后不要翻页"
        ),
    )


class _Detail(BaseModel):
    product_id: str = Field(description="query_inventory 返回的 product_id")
    force: bool = Field(
        False,
        description="已有详情缓存时是否强制重新打开商品页",
    )


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
        "cached",
        "accepted",
        "product_count",
        "page",
        "no_more",
        "no_results",
        "result_count",
        "page_item_count",
        "fallback_item_count",
        "has_next",
        "page_currency",
        "session_currency",
        "site",
        "query",
        "search_seq",
        "skipped",
        "reason",
        "active_query",
        "hint",
        "used",
        "limit",
        "currency_mismatch",
        "detail_currency",
        "session_currency",
        "extension_errors",
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


def _plan_step_status(state: dict[str, Any], role: str) -> str | None:
    plan = state.get("plan") if isinstance(state, dict) else None
    if not isinstance(plan, dict):
        return None
    metadata = plan.get("metadata")
    if not isinstance(metadata, dict):
        return None
    roles = metadata.get("step_roles")
    if not isinstance(roles, dict):
        return None
    step_id = roles.get(role)
    if not step_id:
        return None
    for step in plan.get("steps") or []:
        if isinstance(step, dict) and step.get("id") == step_id:
            return str(step.get("status") or "")
    return None


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
        previous_requirements = dict(box.session.requirements or {})
        updated = box.update_requirements(**explicit_fields)

        review = await _review_requirements(
            updated["requirements"], box.session
        )
        if review is not None:
            original = [
                str(item)
                for item in (updated["requirements"].get("must_have") or [])
            ]
            original_preferences = [
                str(item)
                for item in (
                    updated["requirements"].get("preferences") or []
                )
            ]
            allowed = set(original)
            allowed_preferences = set(original_preferences)
            hard = [item for item in review.hard_must_have if item in allowed]
            hard_set = set(hard)
            soft = [
                item
                for item in review.soft_must_have
                if item in allowed and item not in hard_set
            ]
            dropped = [
                item
                for item in review.dropped_must_have
                if item in allowed
                and item not in hard_set
                and item not in set(soft)
            ]
            dropped_preferences = [
                item
                for item in review.dropped_preferences
                if item in allowed_preferences
            ]
            handled = hard_set | set(soft) | set(dropped)
            # Keep anything the reviewer omitted as hard rather than losing a
            # user requirement.
            hard += [item for item in original if item not in handled]
            req = dict(updated["requirements"])
            req["must_have"] = hard
            remaining_preferences = [
                item
                for item in original_preferences
                if item not in set(dropped_preferences)
            ]
            if soft:
                remaining_preferences = list(
                    dict.fromkeys(remaining_preferences + soft)
                )
            req["preferences"] = remaining_preferences
            if req != updated["requirements"]:
                box.session.requirements = req
                store.touch(box.session)
                updated = {**updated, "requirements": req}
                notes = list(updated.get("parse_notes") or [])
                if soft:
                    notes.append(
                        "moved_to_preferences: " + ", ".join(soft)
                    )
                if dropped:
                    notes.append(
                        "dropped_unrestricted: " + ", ".join(dropped)
                    )
                if dropped_preferences:
                    notes.append(
                        "dropped_preferences: "
                        + ", ".join(dropped_preferences)
                    )
                updated["parse_notes"] = notes
            if review.clarify_question:
                updated["needs_clarify"] = True
                if not updated.get("clarify_question"):
                    updated["clarify_question"] = {
                        "field": "must_have" if original else "preferences",
                        "question": review.clarify_question,
                        "options": review.clarify_options[:4],
                    }
            elif soft:
                updated["needs_clarify"] = True

        changes: dict[str, Any] = {}
        for key in set(previous_requirements) | set(updated["requirements"]):
            if key == "raw":
                continue
            before = previous_requirements.get(key)
            after = updated["requirements"].get(key)
            if before != after:
                changes[key] = {"before": before, "after": after}
        if changes:
            updated["changes"] = changes

        # Keep the plan's requirement snapshot current; the replanner reads it
        # to choose the next search query.
        hard_changed = any(
            previous_requirements.get(field)
            != updated["requirements"].get(field)
            for field in (
                "budget",
                "budget_min",
                "budget_unlimited",
                "category",
                "use_case",
                "must_have",
            )
        )
        plan = state.get("plan") if isinstance(state, dict) else None
        if isinstance(plan, dict):
            metadata = plan.get("metadata")
            if isinstance(metadata, dict):
                metadata["requirements"] = updated["requirements"]
                if hard_changed:
                    # The old queries were derived from the previous hard
                    # requirements. Clearing them stops the model from
                    # following a stale plan; the next search should derive
                    # its query from the updated requirements.
                    metadata["search_queries"] = {}
                    metadata["queries_stale_from_requirements"] = True

        meta = _plan_meta(_context, "requirements_updated")
        question = updated.get("clarify_question")
        if (
            updated.get("needs_clarify")
            and isinstance(question, dict)
            and str(question.get("question") or "").strip()
        ):
            options = list(question.get("options") or [])
            clarify_question = {
                "id": str(
                    question.get("id")
                    or question.get("field")
                    or "clarify"
                ),
                "field": question.get("field"),
                "question": str(question["question"]),
                "options": options,
            }
            pending = {
                "question": clarify_question["question"],
                "reason": "clarify",
                "options": options,
                "metadata": {
                    "field": question.get("field"),
                    "source": "update_requirements",
                    # The side panel reads this list to render option buttons.
                    "questions": [clarify_question],
                },
            }
            return ToolResult(
                call_id="",
                tool="update_requirements",
                ok=True,
                data=updated,
                meta=meta,
                control="wait_user",
                output={
                    "question": pending["question"],
                    "reason": "clarify",
                    "pending_user": pending,
                },
            )
        return ToolResult(
            call_id="",
            tool="update_requirements",
            ok=True,
            data=updated,
            meta=meta,
        )

    async def query_inventory(
        args: _QueryInv, _context: Any
    ) -> ToolResult:
        # Judge only the requested candidate window. The old 60-item cap made
        # a 15-30 item query wait for four sequential LLM batches.
        judge_limit = min(30, max(args.limit, 10))
        judge_error = await box.ensure_fit(limit=judge_limit)
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
                # An empty inventory does not complete verification; the
                # agent may need to paginate or search again.
                done=bool(result.get("count")),
                step_id=step_for("query_inventory"),
            ),
        )

    async def search(args: _Search, _context: Any) -> ToolResult:
        if box.search_pending_scrape:
            return ToolResult(
                call_id="",
                tool="search",
                ok=True,
                data={
                    "status": "skipped",
                    "skipped": True,
                    "reason": "search_pending_scrape",
                    "active_query": box.active_query,
                    "search_seq": box.search_seq,
                    "hint": "先调用 scrape_listing 解析当前搜索页，再发起下一个 search。",
                },
            )
        if _plan_step_status(state, "recommend") == "in_progress" and not args.force:
            return ToolResult(
                call_id="",
                tool="search",
                ok=True,
                data={
                    "status": "skipped",
                    "skipped": True,
                    "reason": "recommend_step_active",
                    "hint": (
                        "当前计划的 recommend 步骤已开始。请先 query_inventory "
                        "并调用 recommend；只有用户明确要求重新搜索时才设置 "
                        "force=true。"
                    ),
                },
            )
        site = str(box.session.settings.get("site") or "")
        adapter = get_adapter(site)
        filters = adapter.build_search_filters(
            query=args.query,
            requirements=box.session.requirements,
            settings=box.session.settings,
            max_price=None,
            buy_it_now_only=args.buy_it_now_only,
        )
        if args.category_id:
            filters["categoryId"] = args.category_id
        if args.exclude_terms:
            filters["excludeTerms"] = args.exclude_terms
        if args.sort:
            filters["sort"] = args.sort
        return _browse_result(
            await box.search(site, args.query, **filters),
            "search",
            _context,
            step_id=step_for("search"),
        )

    async def scrape_listing(
        args: _Scrape, _context: Any
    ) -> ToolResult:
        site = str(box.session.settings.get("site") or "")
        return _browse_result(
            await box.scrape_listing(site, args.page),
            "scrape_listing",
            _context,
            step_id=step_for("scrape_listing"),
            done=True,
        )

    async def next_page(args: _NextPage, _context: Any) -> ToolResult:
        if _plan_step_status(state, "recommend") == "in_progress" and not args.force:
            return ToolResult(
                call_id="",
                tool="next_page",
                ok=True,
                data={
                    "status": "skipped",
                    "skipped": True,
                    "reason": "recommend_step_active",
                    "hint": (
                        "当前计划的 recommend 步骤已开始。请先 query_inventory "
                        "并调用 recommend；只有用户明确要求继续翻页时才设置 "
                        "force=true。"
                    ),
                },
            )
        site = str(box.session.settings.get("site") or "")
        return _browse_result(
            await box.next_page(site),
            "next_page",
            _context,
            step_id=step_for("next_page"),
        )

    async def fetch_detail(args: _Detail, _context: Any) -> ToolResult:
        return _browse_result(
            await box.fetch_detail(args.product_id, force=args.force),
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
                "不得自行添加用户没说过的条件。更新 must_have 时传完整列表，它会替换旧列表。"
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
            description=(
                "打开一个商品详情页，补全规格与价格（每站每轮最多 5 次）。"
                "已有详情缓存时直接返回缓存；只有需要刷新时才传 force=true。"
            ),
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
