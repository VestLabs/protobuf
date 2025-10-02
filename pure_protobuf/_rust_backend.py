"""Rust backend integration with automatic fallback to Python.

This module provides a transparent interface to Rust acceleration.
If Rust extension is not available, it falls back to pure Python.
"""

import os
from typing import Any, Dict, Optional

# Check if Rust should be disabled via environment variable
_RUST_DISABLED = os.environ.get("PURE_PROTOBUF_NO_RUST", "0") == "1"

# Try to import Rust extension
_rust_module: Optional[object] = None
RUST_AVAILABLE = False

if not _RUST_DISABLED:
    try:
        import pure_protobuf_rust
        _rust_module = pure_protobuf_rust
        RUST_AVAILABLE = True
        print("pure_protobuf: Rust extension available")
    except ImportError:
        print("pure_protobuf: WARNING: Rust extension not available, using pure Python instead")
        pass


def get_backend_info() -> Dict[str, Any]:
    """Get information about the active backend.
    
    Returns:
        Dict with keys:
            - rust_available (bool): Whether Rust extension is available
            - rust_disabled (bool): Whether Rust is explicitly disabled
            - backend (str): Active backend name ("rust" or "python")
    """
    return {
        "rust_available": RUST_AVAILABLE,
        "rust_disabled": _RUST_DISABLED,
        "backend": "rust" if RUST_AVAILABLE else "python",
    }


# Export Rust module
pure_protobuf_rust: Optional[Any] = _rust_module


__all__ = [
    "RUST_AVAILABLE",
    "pure_protobuf_rust",
    "get_backend_info",
]

