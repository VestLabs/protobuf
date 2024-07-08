from collections.abc import Iterable
from collections import deque
from typing import Any, Deque, List, Tuple, Union

from typing_extensions import TypeGuard, get_args, get_origin

try:
    from types import NoneType  # type: ignore
except ImportError:
    NoneType = type(None)  # type: ignore


class Sentinel:  # pragma: no cover
    """Sentinel object used for defaults."""

    def __str__(self) -> str:  # pragma: no cover
        return "DEFAULT"

    def __repr__(self) -> str:  # pragma: no cover
        return "DEFAULT"


DEFAULT = Sentinel()


def extract_repeated(hint: Any) -> Tuple[Any, TypeGuard[Deque | List | Tuple]]:
    """Extract a possible repeated flag."""
    origin = get_origin(hint)
    if isinstance(origin, type) and (
        origin is Iterable
        or issubclass(origin, (list, deque))
        or (
            issubclass(origin, tuple)
            and len(get_args(hint)) == 2
            and get_args(hint)[1] is Ellipsis
        )
    ):
        return get_args(hint)[0], True
    return hint, False


def extract_optional(hint: Any) -> Tuple[Any, bool]:
    """Extract a possible optional flag."""
    if get_origin(hint) is Union:
        cleaned_args = tuple(arg for arg in get_args(hint) if arg is not NoneType)
        return Union[cleaned_args], True
    return hint, False
