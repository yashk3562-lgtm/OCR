# This file was added to support streaming chat completions (SSE).

import typing

import pydantic
from ..core.pydantic_utilities import IS_PYDANTIC_V2, UniversalBaseModel


class ChoiceDeltaToolCallFunction(UniversalBaseModel):
    """Partial function call data in a streaming tool call."""

    name: typing.Optional[str] = None
    arguments: typing.Optional[str] = None

    if IS_PYDANTIC_V2:
        model_config: typing.ClassVar[pydantic.ConfigDict] = pydantic.ConfigDict(extra="allow", frozen=True)  # type: ignore # Pydantic v2
    else:

        class Config:
            frozen = True
            smart_union = True
            extra = pydantic.Extra.allow


class ChoiceDeltaToolCall(UniversalBaseModel):
    """A tool call chunk in a streaming response (fields may be null in subsequent chunks)."""

    index: int
    id: typing.Optional[str] = None
    type: typing.Optional[typing.Literal["function"]] = None
    function: typing.Optional[ChoiceDeltaToolCallFunction] = None

    if IS_PYDANTIC_V2:
        model_config: typing.ClassVar[pydantic.ConfigDict] = pydantic.ConfigDict(extra="allow", frozen=True)  # type: ignore # Pydantic v2
    else:

        class Config:
            frozen = True
            smart_union = True
            extra = pydantic.Extra.allow
