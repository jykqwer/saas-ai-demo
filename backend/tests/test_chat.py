"""聊天接口、会话持久化、流式输出与人工转接的测试。"""

import json

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from core.llm import StreamEvent
from domain.chat import RETRIEVE_KB_TOOL, WEB_SEARCH_TOOL
from main import create_app


@pytest.fixture()
def app_settings() -> Settings:
    return Settings(
        app_name="Test SaaS AI Assistant API",
        environment="test",
        log_level="CRITICAL",
        llm_api_key=None,
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-chat",
        saas_product_name="测试产品",
        saas_company_name="测试公司",
    )


@pytest.fixture()
def client(app_settings: Settings) -> TestClient:
    app = create_app(app_settings)
    return TestClient(app)


def test_liveness(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Test SaaS AI Assistant API"


def test_readiness(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["application"] == "ok"


def test_chat_config(client: TestClient) -> None:
    response = client.get("/api/v1/chat/config")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["product_name"] == "测试产品"
    assert body["quick_questions"]
    assert "greeting" in body


def test_chat_mock_reply(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat",
        json={"content": "你们的价格是多少？"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mock"] is True
    assert body["reply"]
    assert body["session_id"]
    # 价格问题应命中知识库中的套餐介绍。
    assert "标准版" in body["reply"]


def test_chat_persists_messages(client: TestClient) -> None:
    """非流式聊天应把用户消息与助手回复写入会话仓库。"""

    first = client.post("/api/v1/chat", json={"content": "你们的价格是多少？"})
    session_id = first.json()["session_id"]

    history = client.get(f"/api/v1/sessions/{session_id}")
    assert history.status_code == 200
    body = history.json()
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["role"] == "assistant"


def test_chat_multi_turn_keeps_context(client: TestClient) -> None:
    """同一会话内多轮对话应保留上下文并持续追加消息。"""

    first = client.post(
        "/api/v1/chat", json={"content": "你们的价格是多少？"}
    ).json()
    session_id = first["session_id"]

    second = client.post(
        "/api/v1/chat",
        json={"content": "支持私有化部署吗？", "session_id": session_id},
    )
    assert second.status_code == 200
    assert second.json()["session_id"] == session_id
    assert "Kubernetes" in second.json()["reply"]

    history = client.get(f"/api/v1/sessions/{session_id}").json()
    assert len(history["messages"]) == 4


def test_chat_rejects_blank_content(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"content": "   "})
    assert response.status_code == 422


def test_chat_rejects_extra_fields(client: TestClient) -> None:
    """协议只允许 { content, session_id }；未知字段一律拒绝。"""

    response = client.post(
        "/api/v1/chat",
        json={"content": "hi", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 422


def test_session_not_found(client: TestClient) -> None:
    import uuid

    response = client.post(
        "/api/v1/chat",
        json={"content": "hi", "session_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


def test_stream_mock_reply(client: TestClient) -> None:
    """SSE 流式对话应依次推送 meta / delta / done，且消息落库。"""

    response = client.post(
        "/api/v1/chat/stream",
        json={"content": "可以免费试用吗？"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = [
        json.loads(line[5:])
        for line in response.text.strip().split("\n\n")
        if line.startswith("data: ")
    ]
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert "delta" in types
    assert types[-1] == "done"

    meta = events[0]
    assert meta["mock"] is True
    assert meta["session_id"]
    deltas = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert "试用" in deltas

    history = client.get(f"/api/v1/sessions/{meta['session_id']}").json()
    assert len(history["messages"]) == 2


def test_auto_mode_tools_disabled_web_keeps_rag() -> None:
    """关闭联网但启用知识库时，auto 模式仍应把 RAG 检索工具交给模型。"""

    settings = Settings(
        app_name="Test SaaS AI Assistant API",
        environment="test",
        log_level="CRITICAL",
        llm_api_key="sk-test",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-chat",
        web_search_enabled=False,
        rag_enabled=True,
    )
    app = create_app(settings)
    recorded: dict = {}

    class _FakeLLM:
        configured = True
        model = "deepseek-chat"
        provider = "fake"

        async def close(self) -> None:
            pass

        async def stream_round(
            self, *, messages, request_id, tools=None, rag_chunks=None
        ):
            recorded["tools"] = tools
            yield StreamEvent(kind="delta", text="ok")

    app.state.llm_client = _FakeLLM()
    with TestClient(app) as c:
        response = c.post(
            "/api/v1/chat/stream",
            json={"content": "标准版多少钱", "mode": "auto"},
        )
        assert response.status_code == 200
        # 消费完整 SSE，确保生成器执行到工具组装点。
        assert "data: " in response.text

    # 只应下发知识库检索工具，不应依赖联网开关。
    assert recorded["tools"] == [RETRIEVE_KB_TOOL]
    assert WEB_SEARCH_TOOL not in recorded["tools"]


def test_handoff_creates_ticket(client: TestClient) -> None:
    session_id = client.post(
        "/api/v1/chat", json={"content": "我想转人工"}
    ).json()["session_id"]

    response = client.post(
        "/api/v1/chat/handoff",
        json={
            "session_id": session_id,
            "contact_name": "张三",
            "contact_type": "wechat",
            "contact_value": "zhangsan-2026",
            "subject": "咨询企业版报价",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == session_id
    assert body["ticket_id"]
    assert "人工" in body["message"]


def test_handoff_rejects_invalid_contact_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat/handoff",
        json={
            "contact_name": "张三",
            "contact_type": "fax",
            "contact_value": "123",
        },
    )
    assert response.status_code == 422


def test_sessions_listing(client: TestClient) -> None:
    client.post("/api/v1/chat", json={"content": "第一个会话"})
    client.post("/api/v1/chat", json={"content": "第二个会话"})

    response = client.get("/api/v1/sessions")
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) >= 2
    # 按更新时间倒序：最近的在最前。
    assert sessions[0]["title"] == "第二个会话"


def test_delete_session(client: TestClient) -> None:
    session_id = client.post(
        "/api/v1/chat", json={"content": "待删除"}
    ).json()["session_id"]

    response = client.delete(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    assert client.get(f"/api/v1/sessions/{session_id}").status_code == 404


def test_message_sources_persisted(client: TestClient) -> None:
    """助手消息应把实际采用的知识库来源随会话持久化，供刷新后恢复。"""

    response = client.post(
        "/api/v1/chat",
        json={"content": "私有化部署需要什么环境？"},
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    body = client.get(f"/api/v1/sessions/{session_id}").json()
    assistant = [m for m in body["messages"] if m["role"] == "assistant"]
    assert assistant
    sources = assistant[-1]["sources"]
    assert sources is not None
    assert sources["rag"]
    assert {"source", "heading", "score"} <= set(sources["rag"][0])


def test_rag_config_reports_docs(client: TestClient) -> None:
    """config 应报告知识库已加载的文档数与分块数。"""

    body = client.get("/api/v1/chat/config").json()
    assert body["rag_docs"] > 0
    assert body["rag_chunks"] > 0


def test_rag_mock_answers_from_knowledge_base(client: TestClient) -> None:
    """演示模式应优先使用知识库检索结果作答。"""

    response = client.post(
        "/api/v1/chat",
        json={"content": "私有化部署需要什么环境？"},
    )
    assert response.status_code == 200
    reply = response.json()["reply"]
    # 检索命中 deployment 知识库，回复应包含关键环境要求。
    assert "Kubernetes" in reply or "Docker" in reply
    assert "知识库" in reply or "官方资料" in reply


def test_request_id_header(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.headers.get("X-Request-ID", "").startswith("req_")

