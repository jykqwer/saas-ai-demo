"""按消息模态选择模型端点的轻量模型网关。"""

from typing import Any

from core.llm import ChatProviderError, LLMClient


def _messages_have_images(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in content
        ):
            return True
    return False


class ModelGateway:
    """文本走默认模型，图片消息自动路由到具备视觉能力的 Qwen。"""

    def __init__(self, *, primary: LLMClient, vision: LLMClient | None = None) -> None:
        self.primary = primary
        self.vision = vision

    @property
    def configured(self) -> bool:
        return self.primary.configured

    @property
    def vision_configured(self) -> bool:
        return self.vision is not None and self.vision.configured

    @property
    def model(self) -> str:
        return self.primary.model

    @property
    def vision_model(self) -> str | None:
        return self.vision.model if self.vision else None

    @property
    def provider(self) -> str:
        return self.primary.provider

    def select(self, messages: list[dict[str, Any]]) -> LLMClient:
        if not _messages_have_images(messages):
            return self.primary
        if self.vision is None or not self.vision.configured:
            raise ChatProviderError(
                code="VISION_MODEL_NOT_CONFIGURED",
                message="Image understanding is not configured.",
            )
        return self.vision

    async def chat(self, *, messages, request_id, rag_chunks=None):
        return await self.select(messages).chat(
            messages=messages, request_id=request_id, rag_chunks=rag_chunks
        )

    async def close(self) -> None:
        await self.primary.close()
        if self.vision is not None:
            await self.vision.close()
