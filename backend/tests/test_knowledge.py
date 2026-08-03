"""知识库管理接口测试：列表、读取、导入、删除与安全校验。"""

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from main import create_app


@pytest.fixture()
def client(tmp_path):
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "sample.md").write_text(
        "# 测试文档\n\n## 价格\n标准版每月 99 元，年付 8 折。\n\n## 试用\n提供 14 天免费试用。",
        encoding="utf-8",
    )
    settings = Settings(
        app_name="Test SaaS AI Assistant API",
        environment="test",
        log_level="CRITICAL",
        llm_api_key=None,
        rag_enabled=True,
        rag_knowledge_base_dir=str(kb_dir),
    )
    return TestClient(create_app(settings))


def test_list_docs(client: TestClient) -> None:
    body = client.get("/api/v1/knowledge/docs").json()
    assert [d["name"] for d in body["docs"]] == ["sample.md"]
    assert body["total_chunks"] >= 1
    assert body["docs"][0]["chunks"] >= 1


def test_read_doc(client: TestClient) -> None:
    response = client.get("/api/v1/knowledge/docs/sample.md")
    assert response.status_code == 200
    assert "标准版" in response.json()["content"]


def test_import_doc_usable_in_chat(client: TestClient) -> None:
    response = client.post(
        "/api/v1/knowledge/docs",
        json={"filename": "faq.md", "content": "# FAQ\n\n## 忘记密码\n可点击忘记密码自助重置，15 分钟解锁。"},
    )
    assert response.status_code == 201
    assert response.json()["doc"]["name"] == "faq.md"

    # 导入后列表可见、检索与聊天立即可用。
    names = [d["name"] for d in client.get("/api/v1/knowledge/docs").json()["docs"]]
    assert "faq.md" in names

    reply = client.post("/api/v1/chat", json={"content": "忘记密码怎么处理"}).json()["reply"]
    assert "自助重置" in reply


def test_import_overwrites_existing(client: TestClient) -> None:
    client.post(
        "/api/v1/knowledge/docs",
        json={"filename": "sample.md", "content": "# 覆盖后的文档\n\n## 新内容\n新版说明。"},
    )
    body = client.get("/api/v1/knowledge/docs").json()
    assert len(body["docs"]) == 1


def test_delete_doc(client: TestClient) -> None:
    client.post(
        "/api/v1/knowledge/docs",
        json={"filename": "tmp.md", "content": "# 临时文档"},
    )
    response = client.delete("/api/v1/knowledge/docs/tmp.md")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert client.get("/api/v1/knowledge/docs/tmp.md").status_code == 404


def test_import_rejects_unsafe_name(client: TestClient) -> None:
    response = client.post(
        "/api/v1/knowledge/docs",
        json={"filename": "../evil.md", "content": "# x"},
    )
    assert response.status_code == 422


def test_import_rejects_blank_content(client: TestClient) -> None:
    response = client.post(
        "/api/v1/knowledge/docs",
        json={"filename": "ok.md", "content": "   "},
    )
    assert response.status_code == 422


def test_knowledge_disabled_returns_empty(client: TestClient) -> None:
    settings = Settings(
        environment="test",
        log_level="CRITICAL",
        llm_api_key=None,
        rag_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        body = c.get("/api/v1/knowledge/docs").json()
        assert body["docs"] == []
        assert body["total_chunks"] == 0
        assert c.post(
            "/api/v1/knowledge/docs",
            json={"filename": "x.md", "content": "# x"},
        ).status_code == 409


def test_retrieve_across_all_docs(client: TestClient) -> None:
    body = client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "标准版多少钱", "k": 3},
    ).json()
    assert body["query"] == "标准版多少钱"
    assert body["doc"] is None
    assert body["results"]
    assert all(r["score"] > 0 for r in body["results"])
    # 检索结果应来自知识库文档（source 为去扩展名的文件名）。
    sources = {r["source"] for r in body["results"]}
    assert sources <= {"sample"}


def test_retrieve_filter_by_doc(client: TestClient) -> None:
    # 导入第二篇文档，验证可按文档过滤。
    client.post(
        "/api/v1/knowledge/docs",
        json={
            "filename": "deploy.md",
            "content": (
                "# 部署\n\n"
                "## 环境要求\n"
                "私有化部署推荐 Kubernetes 1.24 或更高版本，也可以使用 Docker Compose。"
                "最低配置为 4 核 8G 内存，数据保存在客户自己的环境里。"
            ),
        },
    )
    body = client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "Kubernetes 环境要求", "doc": "deploy.md", "k": 3},
    ).json()
    assert body["doc"] == "deploy.md"
    assert body["results"]
    assert {r["source"] for r in body["results"]} == {"deploy"}

    # 过滤到不存在的文档返回空结果。
    empty = client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "Kubernetes 环境要求", "doc": "missing.md", "k": 3},
    ).json()
    assert empty["results"] == []


def test_retrieve_rejects_blank_query(client: TestClient) -> None:
    response = client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "   "},
    )
    assert response.status_code == 422
