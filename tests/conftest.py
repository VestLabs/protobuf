from io import BytesIO
from typing import Callable, Dict, Tuple

from pytest import fixture


def pytest_addoption(parser):
    """Add custom command line option."""
    parser.addoption(
        "--rust_de",
        action="store_true",
        default=False,
        help="Use loads_rust() for deserialization benchmarks"
    )


def pytest_configure(config):
    import tests.benchmark_proto as bp
    bp._USE_RUST_DE = config.getoption("--rust_de")
    if bp._USE_RUST_DE:
        print("\n🦀 Deserialization mode: loads_rust()")
    else:
        print("\n🐍 Deserialization mode: loads()")


@fixture
def bytes_io() -> Callable[[bytes], Callable[[], Tuple[Tuple[BytesIO], Dict]]]:
    def make_setup(bytes_: bytes) -> Callable[[], Tuple[Tuple[BytesIO], Dict]]:
        """Make `setup` function for `pytest-benchmark`."""

        def setup() -> Tuple[Tuple[BytesIO], Dict]:
            return (BytesIO(bytes_),), {}

        return setup

    return make_setup
