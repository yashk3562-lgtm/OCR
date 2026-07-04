# This file was added to support streaming chat completions (SSE).

import typing

import pydantic
from ..core.pydantic_utilities import IS_PYDANTIC_V2, UniversalBaseModel
from .chunk_choice import ChunkChoice
from .completion_usage import CompletionUsage


class ChatCompletionChunk(UniversalBaseModel):
    """A single chunk from a streaming chat completion response."""

    id: str = pydantic.Field()
    """
    A unique identifier for the chat completion (same across all chunks).
    """

    choices: typing.List[ChunkChoice] = pydantic.Field()
    """
    A list of chat completion choices for this chunk.
    """

    created: int = pydantic.Field()
    """
    The Unix timestamp (in seconds) of when the chat completion was created.
    """

    model: str = pydantic.Field()
    """
    The model used for the chat completion.
    """

    object: typing.Literal["chat.completion.chunk"] = pydantic.Field(default="chat.completion.chunk")
    """
    The object type, which is always `chat.completion.chunk` for streaming.
    """

    service_tier: typing.Optional[str] = None
    system_fingerprint: typing.Optional[str] = None
    usage: typing.Optional[CompletionUsage] = None

    if IS_PYDANTIC_V2:
        model_config: typing.ClassVar[pydantic.ConfigDict] = pydantic.ConfigDict(extra="allow", frozen=True)  # type: ignore # Pydantic v2
    else:

        class Config:
            frozen = True
            smart_union = True
            extra = pydantic.Extra.allow
