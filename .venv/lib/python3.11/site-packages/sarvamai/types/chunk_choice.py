# This file was added to support streaming chat completions (SSE).

import typing

import pydantic
from ..core.pydantic_utilities import IS_PYDANTIC_V2, UniversalBaseModel
from .choice_delta import ChoiceDelta
from .finish_reason import FinishReason


class ChunkChoice(UniversalBaseModel):
    """A choice in a streaming chat completion chunk."""

    delta: ChoiceDelta
    finish_reason: typing.Optional[FinishReason] = pydantic.Field(default=None)
    """
    The reason the model stopped generating (null while streaming, set on the final chunk).
    """

    index: int = pydantic.Field()
    """
    The index of the choice in the list of choices.
    """

    logprobs: typing.Optional[typing.Dict[str, typing.Optional[typing.Any]]] = None

    if IS_PYDANTIC_V2:
        model_config: typing.ClassVar[pydantic.ConfigDict] = pydantic.ConfigDict(extra="allow", frozen=True)  # type: ignore # Pydantic v2
    else:

        class Config:
            frozen = True
            smart_union = True
            extra = pydantic.Extra.allow
