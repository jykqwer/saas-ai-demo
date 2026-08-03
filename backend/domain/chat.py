"""聊天领域的核心模型与 SaaS 售前/客服系统提示词。

系统提示词与产品知识库集中在这里维护，方便产品同学调整文案而不触碰协议层。
"""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChatRole = Literal["user", "assistant"]

# 客户端只允许提交 user/assistant 消息；system 提示词由服务端注入，不可被调用方伪造。
ALLOWED_CLIENT_ROLES: frozenset[str] = frozenset({"user", "assistant"})
MAX_CLIENT_MESSAGES = 40
MAX_MESSAGE_CHARS = 8000


class ChatMessage(BaseModel):
    """单条聊天消息；对长度做边界约束，避免无界输入。"""

    model_config = ConfigDict(extra="forbid")

    role: str
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in ALLOWED_CLIENT_ROLES:
            raise ValueError("role must be 'user' or 'assistant'")
        return value

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must contain non-whitespace characters")
        return normalized


class ChatTurn(BaseModel):
    """LLM 上下文中的一条消息（包含系统消息）。"""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class QuickQuestion:
    """预设快捷问题：前端展示，也可作为演示模式的兜底触发词。"""

    label: str
    question: str


@dataclass(frozen=True, slots=True)
class AssistantProfile:
    """前端引导与状态展示所需的产品信息。"""

    product_name: str
    company_name: str
    assistant_name: str = "小枢"
    greeting: str = ""
    quick_questions: tuple[QuickQuestion, ...] = ()


def build_quick_questions() -> tuple[QuickQuestion, ...]:
    """SaaS 售前最常见的四类咨询。"""

    return (
        QuickQuestion("价格与套餐", "你们有哪些套餐？价格是怎么收费的？"),
        QuickQuestion("如何试用", "可以免费试用吗？怎么开始？"),
        QuickQuestion("私有化部署", "支持私有化部署到我们自己的 Kubernetes 集群吗？"),
        QuickQuestion("数据安全", "数据安全和隐私是怎么保障的？"),
        QuickQuestion("API 与集成", "支持 API 集成和现有系统对接吗？"),
    )


def build_system_prompt(*, product_name: str, company_name: str) -> str:
    """构建售前/客服助手的系统提示词，注入产品知识库。"""

    return f"""你是「小枢」，{company_name} 推出的企业级 SaaS 产品「{product_name}」的 AI 售前与客服助手。

你的职责：
1. 热情专业地向访客介绍产品功能、价格、试用、部署与集成方式；
2. 解答售前咨询与功能疑问，帮助访客判断产品是否适合他们的场景；
3. 处理常见售后问题（登录、权限、账单、数据导出等），给出可操作的解决步骤；
4. 涉及具体账号、订单、退款等必须人工处理的事务，礼貌说明已转人工并引导留下联系方式。

产品知识库（仅依据以下事实作答，不要编造）：
- {product_name} 采用多租户架构，支持私有化部署（Kubernetes/Docker）与 SaaS 托管两种模式。
- 标准版按席位按月订阅，企业版支持按量计费与定制 SLA，具体报价需联系销售获取。
- 提供免费试用（标准版 14 天），无需绑定银行卡即可开通。
- 数据默认加密存储，支持等保三级，可私有化后完全掌控数据；支持 SSO（SAML/OIDC）与审计日志。
- 提供开放 REST API 与 Webhook，可对接企业微信、钉钉、飞书及主流 BI 工具。
- 常见售后：忘记密码可通过管理员重置；账单可在控制台下载；数据导出支持 CSV 与 API。

约束：
- 只基于知识库作答；不确定的信息不要编造，建议联系销售或查看官方文档。
- 回答简洁有条理，可用短段落与要点，默认使用简体中文。
- 不承诺未授权的折扣或功能；涉及商务细节（报价、合同、私有化成本）引导联系销售。
- 语气友好专业，像真实的人工客服。
"""


def format_rag_context(chunks) -> str:
    """把检索到的知识库分块格式化为系统提示词的参考资料段落。"""

    if not chunks:
        return ""

    parts = [
        "\n\n【知识库参考资料 · 回答用户问题时请优先依据这些资料，资料不足时如实说明】"
    ]
    for i, chunk in enumerate(chunks, 1):
        parts.append(
            f"\n[资料{i} · 来源 {chunk.source}/{chunk.heading}]\n{chunk.content.strip()}"
        )
    return "".join(parts)


WEB_SEARCH_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "在互联网上搜索实时、时效性或超出产品知识库的通用信息。"
            "当用户询问新闻、天气、人物、事件、外部产品对比等知识库无法回答的问题时使用。"
            "参数 query 是简洁的搜索关键词（通常可直接使用用户的提问）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"],
        },
    },
}


def format_web_results(results) -> str:
    """把网络搜索结果格式化为工具返回给模型的 JSON 字符串。"""

    payload = [
        {"title": r.title, "url": r.url, "snippet": r.snippet} for r in results
    ]
    return json.dumps(payload, ensure_ascii=False)


def format_web_context(results) -> str:
    """把网络搜索结果格式化为注入系统提示词的参考资料段落（始终联网模式用）。"""

    parts = ["\n\n【网络搜索结果 · 回答时请优先依据以下资料并标注来源】"]
    for i, item in enumerate(results, 1):
        parts.append(f"\n[{i}] {item.title}（{item.url}）\n{item.snippet}")
    return "".join(parts)


def build_mock_reply(question: str, profile: AssistantProfile, rag_chunks=None) -> str:
    """演示模式回复：优先用知识库检索结果，否则基于关键词匹配产品知识库。"""

    if rag_chunks:
        sections = []
        for chunk in rag_chunks[:2]:
            sections.append(f"**{chunk.heading}**（来自 {chunk.source}）\n{chunk.content.strip()}")
        return (
            "我查阅了产品知识库，为你整理如下：\n\n"
            + "\n\n".join(sections)
            + "\n\n以上信息来自官方资料。如果你想了解更多细节，可以继续提问，或转人工客服获取专属方案。"
        )

    q = question.lower()
    product = profile.product_name

    if any(k in q for k in ("价格", "套餐", "收费", "多少钱", "费用", "报价")):
        return (
            f"关于「{product}」的套餐，我们有这样几个选择：\n\n"
            "· **标准版**：按席位按月订阅，适合小团队，包含核心功能与基础支持\n"
            "· **企业版**：支持按量计费、定制 SLA 与专属客户成功，适合中大型组织\n"
            "· **私有化部署**：按项目报价，包含实施与驻场支持\n\n"
            "具体报价因席位和部署方式而异，我可以帮你转接销售同事，获取一份定制报价单。"
        )

    if any(k in q for k in ("试用", "免费")):
        return (
            f"可以的！「{product}」提供 **14 天免费试用**（标准版），不需要绑定银行卡。\n\n"
            "开通步骤很简单：\n"
            "1. 在官网点击「开始试用」并注册\n"
            "2. 选择标准版试用，团队会自动开通\n"
            "3. 我们会安排客户成功顾问跟进，帮你快速上手\n\n"
            "要不要我帮你预约一次产品演示？"
        )

    if any(k in q for k in ("私有化", "k8s", "kubernetes", "部署", "docker")):
        return (
            f"支持！「{product}」提供完整的**私有化部署**方案：\n\n"
            "· 基于 **Kubernetes 与 Docker** 交付，兼容主流云厂商与自建机房\n"
            "· 提供 Helm Chart、镜像仓库与一键部署脚本\n"
            "· 数据完全留在你的环境内，支持等保三级合规审计\n"
            "· 包含实施、迁移与驻场支持，可按项目定制 SLA\n\n"
            "私有化涉及环境评估和报价，建议联系销售获取详细方案。"
        )

    if any(k in q for k in ("安全", "数据", "隐私", "合规")):
        return (
            "数据安全是「{product}」的重中之重，我们做了这些保障：\n\n"
            "· 传输与存储**全程加密**，敏感字段单独脱敏\n"
            "· 支持 **SSO（SAML/OIDC）** 与细粒度权限、完整审计日志\n"
            "· 私有化部署时数据**完全由你掌控**，可满足等保三级等合规要求\n"
            "· 通过主流安全认证，并有年度渗透测试报告可查阅\n\n"
            "如果你们有更严格的合规要求，可以联系安全团队做专项对接。"
        ).replace("「{product}」", f"「{product}」")

    if any(k in q for k in ("api", "集成", "对接", "接口", "webhook")):
        return (
            f"「{product}」提供开放的 **REST API 与 Webhook**，可以轻松融入你的技术栈：\n\n"
            "· 完整的 OpenAPI 文档，支持主流语言 SDK\n"
            "· Webhook 事件订阅，实时同步业务数据\n"
            "· 官方对接 **企业微信、钉钉、飞书** 与主流 BI 工具\n"
            "· 提供沙箱环境，方便你安全联调\n\n"
            "开发同学可以直接看文档，或者我帮你联系技术顾问做集成评审。"
        )

    if any(k in q for k in ("人工", "联系", "销售", "电话", "客服")):
        return (
            "没问题，这就帮你转接！你可以：\n\n"
            "· 在官网右下角联系**在线客服**（工作时间内即时响应）\n"
            "· 拨打 400 服务热线，或给 sales@ 邮箱写信\n"
            "· 留下你的电话/微信，我们会在 **1 个工作日内** 主动联系你\n\n"
            "请告诉我你的称呼和联系方式，我帮你登记。"
        )

    if any(k in q for k in ("登录", "密码", "账号", "权限")):
        return (
            "这是常见的几个售后场景，你可以这样处理：\n\n"
            "· **忘记密码**：在登录页点击「忘记密码」自助重置；管理员也可在控制台代重置\n"
            "· **账号锁定**：连续输错会临时锁定，通常 15 分钟自动解锁\n"
            "· **权限问题**：由工作区管理员在「成员与权限」中调整角色\n\n"
            "如果还是无法登录，请提供你的工作区名称，我帮你升级到人工支持。"
        )

    return (
        f"感谢你的咨询！关于「{product}」，我可以在这些方面帮你：\n\n"
        "· 套餐与报价、免费试用开通\n"
        "· 私有化部署与 Kubernetes 落地\n"
        "· 数据安全、SSO 与合规\n"
        "· API 集成与售后问题\n\n"
        "你可以直接告诉我关心的问题，或者点击上面的快捷问题，我会立即为你解答。"
    )


def build_assistant_profile(*, product_name: str, company_name: str) -> AssistantProfile:
    """组装前端引导所需的助手档案。"""

    return AssistantProfile(
        product_name=product_name,
        company_name=company_name,
        greeting=(
            f"你好，我是 {company_name} 的 AI 助手「小枢」✨\n"
            f"我可以帮你了解「{product_name}」的产品功能、价格、试用、私有化部署与数据安全等问题，"
            "也可以处理常见的售后需求。有什么可以帮你的吗？"
        ),
        quick_questions=build_quick_questions(),
    )
