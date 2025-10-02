from __future__ import annotations

from abc import ABC
from io import BytesIO
from typing import IO, Any, ClassVar, Dict, Tuple

from typing_extensions import Self

from pure_protobuf.rust_integration import loads_with_rust_batch, loads_with_rust

try:
    from inspect import get_annotations  # type: ignore[attr-defined]
except ImportError:
    from get_annotations import get_annotations  # type: ignore[no-redef]

from pure_protobuf._accumulators import AccumulateMessages
from pure_protobuf._mergers import MergeMessages
from pure_protobuf.descriptors._field import _FieldDescriptor
from pure_protobuf.descriptors.record import RecordDescriptor
from pure_protobuf.helpers.itertools import ReadCallback
from pure_protobuf.interfaces._skip import Skip, skip_no_operation
from pure_protobuf.io.bytes_ import skip_bytes
from pure_protobuf.io.fixed32 import skip_fixed_32
from pure_protobuf.io.fixed64 import skip_fixed_64
from pure_protobuf.io.tag import Tag
from pure_protobuf.io.varint import skip_varint
from pure_protobuf.io.wire_type import WireType
from pure_protobuf.io.wrappers import (
    ReadLengthDelimited,
    ReadStrictlyTyped,
    WriteLengthDelimited,
    to_bytes,
)


class BaseMessage(ABC):
    """Base message class, inherit from it to define a specific message."""

    __slots__ = ()

    __PROTOBUF_FIELDS_BY_NUMBER__: ClassVar[Dict[int, Tuple[str, _FieldDescriptor]]]
    __PROTOBUF_FIELDS_BY_NAME__: ClassVar[Dict[str, _FieldDescriptor]]

    __PROTOBUF_SKIP__: ClassVar[Dict[WireType, Skip]] = {
        WireType.VARINT: skip_varint,
        WireType.I64: skip_fixed_64,
        WireType.LEN: skip_bytes,
        WireType.I32: skip_fixed_32,
        WireType.SGROUP: skip_no_operation,
        WireType.EGROUP: skip_no_operation,
    }
    """Defines how to skip a field of the given wire type."""

    def __init_subclass__(cls) -> None:  # noqa: D105
        cls.__PROTOBUF_FIELDS_BY_NUMBER__ = {}
        cls.__PROTOBUF_FIELDS_BY_NAME__ = {}

        type_hints: Dict[str, Any] = get_annotations(cls, eval_str=True)
        for name, hint in type_hints.items():
            descriptor = _FieldDescriptor.from_attribute(cls, hint)
            if descriptor is not None:
                cls.__PROTOBUF_FIELDS_BY_NUMBER__[descriptor.number] = (name, descriptor)
                cls.__PROTOBUF_FIELDS_BY_NAME__[name] = descriptor
                one_of = descriptor.one_of
                if one_of is not None:
                    one_of._add_field(descriptor.number, name)

    @classmethod
    def read_from(cls, io: IO[bytes]) -> Self:
        """Read a message from the file."""

        values: Dict[str, Any] = {}
        while True:
            try:
                tag = Tag.read_from(io)
            except EOFError:
                break
            try:
                name, descriptor = cls.__PROTOBUF_FIELDS_BY_NUMBER__[tag.field_number]
            except KeyError:
                # The field is not defined, skip it in the stream.
                # TODO: https://github.com/eigenein/protobuf/issues/99.
                cls.__PROTOBUF_SKIP__[tag.wire_type](io)
            else:
                # Read the value and accumulate it.
                values[name] = descriptor.accumulate(
                    values.get(name),
                    descriptor.read(io, tag.wire_type),
                )

                # Possibly update the one-of field.
                one_of = descriptor.one_of
                if one_of is not None:
                    one_of._keep_values(values, descriptor.number)

        return cls(**values)

    @classmethod
    def loads(cls, buffer: bytes) -> Self:
        """
        Read a message from the buffer.

        This is functionally the same as calling `read_from(BytesIO(buffer))`.
        """
        return cls.read_from(BytesIO(buffer))

    @classmethod
    def loads_rust(cls, buffer: bytes) -> tuple[Self, bool]:
        """
        Read a message from the buffer using Rust parser (if available).

        This method uses the Rust backend for accelerated parsing. Falls back
        to the Python implementation if Rust is not available or on error.

        Performance: typically 5-80x faster than `loads()` depending on message complexity.

        Example:
            >>> msg, was_rust = BaseMessage.loads_rust(buffer)
            >>> if was_rust:
            ...     print("Parsed with Rust!")
            ... else:
            ...     print("Fell back to Python")

        Args:
            buffer: Protobuf encoded bytes

        Returns:
            Tuple of (message instance, was_rust_used: bool)
            - was_rust_used is True if Rust parser was used
            - was_rust_used is False if fallback to Python occurred

        See Also:
            - `loads()`: Python implementation
            - `loads_rust_batch()`: Batch parsing for multiple messages
            - Environment variable `PURE_PROTOBUF_NO_RUST=1` to force disable Rust

        Note:
            Supports: int, float, bool, string, bytes, Decimal, IntEnum,
            repeated fields, and nested messages.
        """

        return loads_with_rust(cls, buffer)

    @classmethod
    def loads_rust_batch(cls, buffers: list[bytes]) -> tuple[list[Self], bool]:
        """
        Parse multiple messages in batch using Rust parser (reduces FFI overhead).

        This method is 2-3x faster than calling `loads_rust()` in a loop because:
          - Single FFI call instead of N calls
          - Shared field spec extraction
          - Better CPU cache locality

        Performance: useful when parsing 10+ messages of the same type.

        Example:
            >>> buffers = [bytes(msg1), bytes(msg2), bytes(msg3)]
            >>> messages, was_rust = BaseMessage.loads_with_rust_batch(buffers,)
            >>> len(messages)
            3
            >>> was_rust
            True

        Args:
            buffers: List of protobuf encoded bytes

        Returns:
            Tuple of (list of message instances, was_rust_used: bool)
            - was_rust_used is True if Rust parser was used
            - was_rust_used is False if fallback to Python occurred

        See Also:
            - `loads_rust()`: Single message parsing
            - `loads()`: Python implementation

        Falls back to Python if Rust unavailable or on error.
        """

        return loads_with_rust_batch(cls, buffers)

    def write_to(self, io: IO[bytes]) -> None:
        """Write the message to the file."""
        for _, (name, descriptor) in self.__PROTOBUF_FIELDS_BY_NUMBER__.items():
            descriptor.write(getattr(self, name), io)

    def __bytes__(self) -> bytes:
        """
        Convert the message to a bytestring.

        This is functionally the same as calling `dumps()` or `write_to(BytesIO(…))`.
        """
        return to_bytes(BaseMessage.write_to, self)

    def dumps(self) -> bytes:
        """
        Convert the message to a bytestring.

        This is functionally the same as calling `bytes(message)` or `write_to(BytesIO(…))`.
        """
        return bytes(self)

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: D105
        if value is not None:
            super().__setattr__(name, value)
            descriptor = self.__PROTOBUF_FIELDS_BY_NAME__[name]
            one_of = descriptor.one_of
            if one_of is not None and value is not None:
                one_of._keep_attribute(self, descriptor.number)

    @classmethod
    def _init_embedded_descriptor(cls) -> RecordDescriptor[Self]:
        accumulate = AccumulateMessages(cls)
        return RecordDescriptor(
            wire_type=WireType.LEN,
            write=WriteLengthDelimited(cls.write_to),
            read=ReadStrictlyTyped(ReadLengthDelimited(ReadCallback(cls.read_from)), WireType.LEN),
            accumulate=accumulate,
            merge=MergeMessages(accumulate),
        )
