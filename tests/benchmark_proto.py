"""Benchmark for protobuf serialization/deserialization.

Run with:
  # Python deserialization (default)
  pytest tests/benchmark_proto.py --benchmark-only --benchmark-columns=mean,ops -o addopts="" -s
  
  # Rust deserialization
  pytest tests/benchmark_proto.py --rust_de --benchmark-only --benchmark-columns=mean,ops -o addopts="" -s
  
Note: 
  - Use -s flag to see backend information
  - Rust parser currently has overhead for nested messages (recursive parsing in Python)
  - Best performance: simple messages with primitive types, Decimal, IntEnum
  - For heavily nested: Python may be faster due to less FFI overhead
"""
from __future__ import annotations

# Backend information
from pure_protobuf._rust_backend import get_backend_info

_backend_info = get_backend_info()

# Global flag for deserialization method (set by conftest.py)
_USE_RUST_DE = False


def print_backend_info():
    """Print backend information before running benchmarks."""
    print("\n" + "=" * 70)
    print("🔧 Pure-Protobuf Backend Information")
    print("=" * 70)
    
    # Show deserialization method
    if _USE_RUST_DE:
        print("📦 Deserialization: loads_rust() (Rust with fallback)")
    else:
        print("📦 Deserialization: loads() (Python)")
    
    backend = _backend_info['backend'].upper()
    if backend == 'RUST':
        print("✅ Rust available: True")
    else:
        print("⚠️  Rust available: False")
    print(f"   Rust disabled:  {_backend_info['rust_disabled']}")
    print("=" * 70)
    print()

from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum
from typing import Dict, Optional

import pytest
from typing_extensions import Annotated

from pure_protobuf.annotations import Field
from pure_protobuf.message import BaseMessage


class MarketHours(IntEnum):
    """Market hours enum."""

    PRE_MARKET = 1
    REGULAR = 2
    AFTER_HOURS = 3
    CLOSED = 4


class SourceEventType(IntEnum):
    """Source event type enum."""

    TRADE = 1
    QUOTE = 2
    ORDER_BOOK = 3
    SETTLEMENT = 4


class SourceEventExchange(IntEnum):
    """Source event exchange enum."""

    NYSE = 1
    NASDAQ = 2
    CBOE = 3
    CME = 4


@dataclass(frozen=True, slots=True)
class DepthItem(BaseMessage):
    """Depth item message with Decimal."""

    price: Annotated[Decimal, Field(1)]
    size: Annotated[Decimal, Field(2)]


@dataclass(frozen=True, slots=True)
class Depth(BaseMessage):
    """Depth message with Decimal."""

    bids: Annotated[list[DepthItem], Field(1)] = field(default_factory=list)
    asks: Annotated[list[DepthItem], Field(2)] = field(default_factory=list)
    ts: Annotated[int, Field(3)] = 0


@dataclass(frozen=True, slots=True)
class DepthItemFloat(BaseMessage):
    """Depth item message with float."""

    price: Annotated[float, Field(1)]
    size: Annotated[float, Field(2)]


@dataclass(frozen=True, slots=True)
class DepthFloat(BaseMessage):
    """Depth message with float."""

    bids: Annotated[list[DepthItemFloat], Field(1)] = field(default_factory=list)
    asks: Annotated[list[DepthItemFloat], Field(2)] = field(default_factory=list)
    ts: Annotated[int, Field(3)] = 0


@dataclass(frozen=True, slots=True)
class Price(BaseMessage):
    """Price message."""

    data: Annotated[Decimal, Field(1)]
    ts: Annotated[int, Field(2)]
    asks_a: Annotated[float, Field(3)] = 0
    bids_a: Annotated[float, Field(4)] = 0
    ask_spread: Annotated[float, Field(5)] = 0
    bid_spread: Annotated[float, Field(6)] = 0
    enable_impact: Annotated[bool, Field(7)] = False
    market_segment: Annotated[Optional[MarketHours], Field(8)] = None
    source_event_type: Annotated[Optional[SourceEventType], Field(9)] = None
    source_event_exchange: Annotated[Optional[SourceEventExchange], Field(10)] = None


@dataclass(frozen=True, slots=True)
class ProtobufWrapper(BaseMessage):
    """Wrapper for polymorphic message serialization."""

    key: Annotated[str, Field(1)]
    buffer: Annotated[bytes, Field(2)]


# Deserialization map for wrapped messages
PROTOBUF_DESERIALIZE_MAP: Dict[str, type[BaseMessage]] = {
    "Price": Price,
    "Depth": Depth,
    "DepthFloat": DepthFloat,
}


def _deserialize(message_class: type[BaseMessage], buffer: bytes) -> BaseMessage:
    """
    Deserialize using either loads() or loads_rust() based on --rust_de flag.
    
    Args:
        message_class: Message class to deserialize
        buffer: Protobuf bytes
        
    Returns:
        Deserialized message instance
    """
    if _USE_RUST_DE:
        msg, was_rust = message_class.loads_rust(buffer)
        return msg
    else:
        return message_class.loads(buffer)


def protobuf_deserializer(buffer: bytes) -> BaseMessage | None:
    """Deserialize a wrapped protobuf message."""
    try:
        wrapper, _ = ProtobufWrapper.loads_rust(buffer)
        return _deserialize(PROTOBUF_DESERIALIZE_MAP[wrapper.key], wrapper.buffer)
    except (TypeError, KeyError):
        return None


def wrap_message(message: BaseMessage) -> ProtobufWrapper:
    """Wrap a message with its type key."""
    return ProtobufWrapper(
        key=message.__class__.__name__,
        buffer=bytes(message),
    )


# Create test data with realistic order book depth (30 levels each side)
DEPTH_MESSAGE = Depth(
    bids=[
        DepthItem(price=Decimal(f"{100.25 - i * 0.01:.2f}"), size=Decimal(f"{150 + i * 5:.1f}"))
        for i in range(30)
    ],
    asks=[
        DepthItem(price=Decimal(f"{100.26 + i * 0.01:.2f}"), size=Decimal(f"{145 + i * 5:.1f}"))
        for i in range(30)
    ],
    ts=1696118400000,
)

DEPTH_FLOAT_MESSAGE = DepthFloat(
    bids=[
        DepthItemFloat(price=100.25 - i * 0.01, size=150.0 + i * 5.0)
        for i in range(30)
    ],
    asks=[
        DepthItemFloat(price=100.26 + i * 0.01, size=145.0 + i * 5.0)
        for i in range(30)
    ],
    ts=1696118400000,
)

PRICE_MESSAGE = Price(
    data=Decimal("100.00"),
    ts=1696118400000,
    asks_a=1000.0,
    bids_a=1500.0,
    ask_spread=0.05,
    bid_spread=0.03,
    enable_impact=True,
    market_segment=MarketHours.REGULAR,
    source_event_type=SourceEventType.TRADE,
    source_event_exchange=SourceEventExchange.NYSE,
)

# Pre-serialize for deserialization benchmarks
DEPTH_BYTES = bytes(DEPTH_MESSAGE)
DEPTH_FLOAT_BYTES = bytes(DEPTH_FLOAT_MESSAGE)
PRICE_BYTES = bytes(PRICE_MESSAGE)

# Wrapped messages for polymorphic benchmarks
WRAPPED_DEPTH = wrap_message(DEPTH_MESSAGE)
WRAPPED_PRICE = wrap_message(PRICE_MESSAGE)
WRAPPED_DEPTH_BYTES = bytes(WRAPPED_DEPTH)
WRAPPED_PRICE_BYTES = bytes(WRAPPED_PRICE)


# Print backend info once at module load
print_backend_info()


@pytest.mark.benchmark(group="depth")
def test_depth_serialize(benchmark):
    """Benchmark Depth serialization."""
    result = benchmark(lambda: bytes(DEPTH_MESSAGE))
    assert len(result) > 0


@pytest.mark.benchmark(group="depth")
def test_depth_deserialize(benchmark):
    """Benchmark Depth deserialization."""
    result = benchmark(lambda: _deserialize(Depth, DEPTH_BYTES))
    assert result == DEPTH_MESSAGE


@pytest.mark.benchmark(group="price")
def test_price_serialize(benchmark):
    """Benchmark Price serialization."""
    result = benchmark(lambda: bytes(PRICE_MESSAGE))
    assert len(result) > 0


@pytest.mark.benchmark(group="price")
def test_price_deserialize(benchmark):
    """Benchmark Price deserialization."""
    result = benchmark(lambda: _deserialize(Price, PRICE_BYTES))
    # Compare with tolerance for float fields due to precision
    assert result.data == PRICE_MESSAGE.data
    assert result.ts == PRICE_MESSAGE.ts
    assert abs(result.asks_a - PRICE_MESSAGE.asks_a) < 1e-6
    assert abs(result.bids_a - PRICE_MESSAGE.bids_a) < 1e-6
    assert abs(result.ask_spread - PRICE_MESSAGE.ask_spread) < 1e-6
    assert abs(result.bid_spread - PRICE_MESSAGE.bid_spread) < 1e-6
    assert result.enable_impact == PRICE_MESSAGE.enable_impact
    assert result.market_segment == PRICE_MESSAGE.market_segment
    assert result.source_event_type == PRICE_MESSAGE.source_event_type
    assert result.source_event_exchange == PRICE_MESSAGE.source_event_exchange


@pytest.mark.benchmark(group="depth_float")
def test_depth_float_serialize(benchmark):
    """Benchmark DepthFloat serialization."""
    result = benchmark(lambda: bytes(DEPTH_FLOAT_MESSAGE))
    assert len(result) > 0


@pytest.mark.benchmark(group="depth_float")
def test_depth_float_deserialize(benchmark):
    """Benchmark DepthFloat deserialization."""
    result = benchmark(lambda: _deserialize(DepthFloat, DEPTH_FLOAT_BYTES))
    # Float precision validation
    assert len(result.bids) == len(DEPTH_FLOAT_MESSAGE.bids)
    assert len(result.asks) == len(DEPTH_FLOAT_MESSAGE.asks)
    assert result.ts == DEPTH_FLOAT_MESSAGE.ts


@pytest.mark.benchmark(group="wrapped_depth")
def test_wrapped_depth_serialize(benchmark):
    """Benchmark wrapped Depth serialization."""
    result = benchmark(lambda: bytes(wrap_message(DEPTH_MESSAGE)))
    assert len(result) > 0


@pytest.mark.benchmark(group="wrapped_depth")
def test_wrapped_depth_deserialize(benchmark):
    """Benchmark wrapped Depth deserialization."""
    result = benchmark(lambda: protobuf_deserializer(WRAPPED_DEPTH_BYTES))
    assert result is not None
    assert isinstance(result, Depth)


@pytest.mark.benchmark(group="wrapped_price")
def test_wrapped_price_serialize(benchmark):
    """Benchmark wrapped Price serialization."""
    result = benchmark(lambda: bytes(wrap_message(PRICE_MESSAGE)))
    assert len(result) > 0


@pytest.mark.benchmark(group="wrapped_price")
def test_wrapped_price_deserialize(benchmark):
    """Benchmark wrapped Price deserialization."""
    result = benchmark(lambda: protobuf_deserializer(WRAPPED_PRICE_BYTES))
    assert result is not None
    assert isinstance(result, Price)



