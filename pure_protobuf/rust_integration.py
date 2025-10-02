"""
Rust parser integration for BaseMessage.

Use `BaseMessage.loads_rust()` for accelerated parsing:
    
    msg = MyMessage.loads_rust(buffer)  # 5-80x faster!

Automatically falls back to Python if Rust is unavailable.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Type, Tuple

from pure_protobuf._rust_backend import RUST_AVAILABLE, pure_protobuf_rust

if TYPE_CHECKING:
    from pure_protobuf.message import BaseMessage

FieldSpecTuple = Tuple[str, str, bool, Optional[Any]]

_FIELD_SPECS_CACHE: Dict[Type[BaseMessage], Dict[int, FieldSpecTuple]] = {}
_MESSAGE_CLASSES_CACHE: Dict[Type[BaseMessage], Dict[str, Type[BaseMessage]]] = {}


def extract_field_specs(message_class: Type[BaseMessage]) -> Dict[int, FieldSpecTuple]:
    """
    Extract field specifications from a BaseMessage class.
    
    Args:
        message_class: BaseMessage subclass to extract specs from
    
    Returns:
        Dict mapping field_number -> (field_name, type_str, is_repeated, enum_class, nested_specs)
        where:
          - enum_class is optional IntEnum class for conversion
          - nested_specs is dict for nested message fields (or None)
    
    Results are cached for performance.
    """
    # Check cache first
    if message_class in _FIELD_SPECS_CACHE:
        # Return full 5-tuple with nested_specs
        return _FIELD_SPECS_CACHE[message_class]
    
    specs = {}
    
    # Get enum class and message class mapping from type hints (resolves forward references)
    from enum import IntEnum
    try:
        from typing import get_origin, get_args, get_type_hints
    except ImportError:
        from typing_extensions import get_origin, get_args
        from typing import get_type_hints
    
    enum_classes = {}  # field_name -> IntEnum class
    nested_message_classes = {}  # field_name -> BaseMessage class for nested messages
    try:
        # Use get_type_hints to resolve string annotations
        type_hints = get_type_hints(message_class, include_extras=True)
        
        for field_name, field_type in type_hints.items():
            # Unwrap Annotated
            args = get_args(field_type)
            if args:
                inner_type = args[0]  # Get first arg from Annotated
                
                # Check for list[MessageType] (repeated nested messages)
                origin = get_origin(inner_type)
                if origin is list:
                    list_args = get_args(inner_type)
                    if list_args:
                        nested_type = list_args[0]
                        try:
                            # Import here to avoid circular import
                            from pure_protobuf.message import BaseMessage
                            if isinstance(nested_type, type) and issubclass(nested_type, BaseMessage):
                                nested_message_classes[field_name] = nested_type
                        except (TypeError, ImportError):
                            pass
                
                # Unwrap Optional[X] -> Union[X, None]
                inner_args = get_args(inner_type)
                if inner_args:
                    for arg in inner_args:
                        try:
                            if arg is not type(None) and isinstance(arg, type):
                                if issubclass(arg, IntEnum):
                                    enum_classes[field_name] = arg
                                    break
                                # Check for BaseMessage (nested message)
                                from pure_protobuf.message import BaseMessage
                                if issubclass(arg, BaseMessage):
                                    nested_message_classes[field_name] = arg
                                    break
                        except (TypeError, ImportError):
                            pass
                # Check if it's directly IntEnum or BaseMessage (not Optional)
                else:
                    try:
                        if isinstance(inner_type, type):
                            if issubclass(inner_type, IntEnum):
                                enum_classes[field_name] = inner_type
                            else:
                                from pure_protobuf.message import BaseMessage
                                if issubclass(inner_type, BaseMessage):
                                    nested_message_classes[field_name] = inner_type
                    except (TypeError, ImportError):
                        pass
    except Exception:
        # If get_type_hints fails, just skip type detection
        pass
    
    for field_number, (field_name, field_descriptor) in message_class.__PROTOBUF_FIELDS_BY_NUMBER__.items():
        # Determine type from write/read function names
        try:
            write_str = str(field_descriptor.write)
            read_str = str(field_descriptor.read)
            
            # Determine type_str from function names
            if 'String' in write_str or 'string' in read_str.lower():
                type_str = "string"
            elif 'Decimal' in write_str or 'decimal' in read_str.lower():
                type_str = "decimal"
            elif 'Byte' in write_str or 'byte' in read_str.lower():
                type_str = "bytes"
            elif 'Bool' in write_str or 'bool' in read_str.lower():
                type_str = "bool"
            elif 'Struct' in write_str and '<class \'float\'>' in write_str:
                type_str = "float"
            elif 'Struct' in write_str and '<class \'int\'>' in write_str:
                type_str = "int"
            elif 'Message' in write_str or 'LengthDelimited' in write_str:
                type_str = "message"
            elif 'Varint' in write_str or 'Varint' in read_str:
                type_str = "int"
            elif 'Enum' in write_str or 'Enum' in read_str:
                # IntEnum - encoded as varint int
                type_str = "int"
            else:
                # Try to guess from read
                if 'I32' in str(type(field_descriptor.read)):
                    type_str = "float"
                elif 'I64' in str(type(field_descriptor.read)):
                    type_str = "double"
                else:
                    # Skip unknown types
                    continue
            
            # Determine if repeated
            is_repeated = False
            accumulator_type = type(field_descriptor.accumulate).__name__
            if 'Message' in accumulator_type or 'Extend' in accumulator_type or 'Append' in accumulator_type:
                is_repeated = True
            
            # Get enum class if this field is IntEnum
            enum_class = enum_classes.get(field_name, None)
            
            # Get nested specs if this field is a nested message
            nested_specs = None
            if type_str == "message":
                nested_msg_class = nested_message_classes.get(field_name)
                if nested_msg_class is not None:
                    # Recursively extract specs for nested message
                    nested_specs = extract_field_specs(nested_msg_class)
            
            specs[field_number] = (field_name, type_str, is_repeated, enum_class, nested_specs)
        
        except Exception as e:
            # Skip fields that can't be analyzed
            continue
    
    # Cache the result (with nested_specs)
    _FIELD_SPECS_CACHE[message_class] = specs
    
    # Return full 5-tuple with nested_specs
    return specs


def _convert_dicts_to_messages(cls: Type[BaseMessage], data_dict: dict, specs: dict, message_classes: Optional[dict] = None) -> dict:
    """
    Recursively convert nested dicts to dataclass instances.
    
    Args:
        cls: BaseMessage class for type hints (used only once to extract classes)
        data_dict: Dict with nested dicts from Rust parser
        specs: Field specs with nested_specs
        message_classes: Cached dict of field_name -> message_class (optional, extracted if None)
    
    Returns:
        Dict with nested BaseMessage instances
    """
    # Extract message classes only once per class (with cache!)
    if message_classes is None:
        if cls in _MESSAGE_CLASSES_CACHE:
            message_classes = _MESSAGE_CLASSES_CACHE[cls]
        else:
            try:
                from typing import get_origin, get_args, get_type_hints
            except ImportError:
                from typing_extensions import get_origin, get_args
                from typing import get_type_hints
            
            type_hints = get_type_hints(cls, include_extras=True)
            message_classes = {}
            
            for field_name, field_type in type_hints.items():
                # Unwrap Annotated[T, ...] -> T
                args = get_args(field_type)
                if args:
                    inner_type = args[0]
                else:
                    inner_type = field_type
                
                # Check for list[T] (repeated)
                origin = get_origin(inner_type)
                if origin is list:
                    list_args = get_args(inner_type)
                    if list_args:
                        message_classes[field_name] = list_args[0]
                else:
                    # Single message
                    try:
                        if hasattr(inner_type, '__dataclass_fields__'):
                            message_classes[field_name] = inner_type
                    except:
                        pass
            
            # Cache it!
            _MESSAGE_CLASSES_CACHE[cls] = message_classes
    
    result = dict(data_dict)
    
    for field_num, (field_name, type_str, is_repeated, _, nested_specs) in specs.items():
        if type_str == 'message' and field_name in result and nested_specs is not None:
            message_class = message_classes.get(field_name)
            if message_class is None:
                continue
            
            if is_repeated:
                # Convert each dict in the list to message instance
                dict_list = result[field_name]
                if isinstance(dict_list, list):
                    message_list = []
                    for item_dict in dict_list:
                        if isinstance(item_dict, dict):
                            # Recursively convert nested dict (NO get_type_hints!)
                            converted_dict = _convert_dicts_to_messages(message_class, item_dict, nested_specs, message_classes=None)
                            message_obj = message_class(**converted_dict)
                            message_list.append(message_obj)
                        else:
                            # Already converted or different type
                            message_list.append(item_dict)
                    result[field_name] = message_list
            else:
                # Single nested message
                item_dict = result[field_name]
                if isinstance(item_dict, dict):
                    # Recursively convert nested dict (NO get_type_hints!)
                    converted_dict = _convert_dicts_to_messages(message_class, item_dict, nested_specs, message_classes=None)
                    result[field_name] = message_class(**converted_dict)
    
    return result


def loads_with_rust(cls: Type[BaseMessage], buffer: bytes) -> tuple[BaseMessage, bool]:
    """
    Parse a protobuf message using Rust parser.
    
    Args:
        cls: BaseMessage subclass to deserialize into
        buffer: Protobuf encoded bytes
    
    Returns:
        Tuple of (deserialized message instance, was_rust_used: bool)
        - was_rust_used is True if Rust parser was used
        - was_rust_used is False if fallback to Python occurred
    
    Falls back to Python if Rust unavailable or on error.
    """
    if not RUST_AVAILABLE:
        # Fallback to Python implementation
        return cls.loads(buffer), False
    
    try:
        # Extract field specs (returns 5-tuple: name, type_str, is_repeated, enum_class, nested_specs)
        specs = extract_field_specs(cls)
        
        # Parse with Rust - now with full recursive nested message parsing!
        # Result is a dict with nested dicts (not bytes anymore)
        result_dict = pure_protobuf_rust.parse_message(buffer, specs)
        
        # Post-process: convert nested dicts to dataclass instances
        result_dict = _convert_dicts_to_messages(cls, result_dict, specs)
        
        # Create message instance
        return cls(**result_dict), True
    
    except Exception:
        # Fall back to Python on any error
        return cls.loads(buffer), False


def loads_with_rust_batch(cls: Type[BaseMessage], buffers: list[bytes]) -> tuple[list[BaseMessage], bool]:
    """
    Parse multiple protobuf messages in batch (reduces FFI overhead).
    
    Args:
        cls: BaseMessage subclass to deserialize into
        buffers: List of protobuf encoded bytes
    
    Returns:
        Tuple of (list of deserialized message instances, was_rust_used: bool)
        - was_rust_used is True if Rust parser was used
        - was_rust_used is False if fallback to Python occurred
    
    Performance: ~2-3x faster than calling loads_rust() in a loop due to:
      - Single FFI call instead of N calls
      - Shared field spec extraction and conversion
      - Better CPU cache locality
    
    Example:
        >>> buffers = [bytes(msg1), bytes(msg2), bytes(msg3)]
        >>> messages, was_rust = MyMessage.loads_with_rust_batch(buffers,)
        >>> len(messages)
        3
        >>> was_rust
        True
    
    Falls back to Python if Rust unavailable or on error.
    """
    if not RUST_AVAILABLE:
        # Fallback to Python implementation (loop)
        return [cls.loads(buffer) for buffer in buffers], False
    
    if not buffers:
        return [], True  # Empty batch is technically "success" with Rust
    
    try:
        # Extract field specs once (shared across all messages)
        specs = extract_field_specs(cls)
        
        # Parse all messages in Rust (single FFI call!)
        result_dicts = pure_protobuf_rust.parse_message_batch(buffers, specs)
        
        # Convert dicts to dataclass instances (batch style)
        # Get message classes once
        if cls in _MESSAGE_CLASSES_CACHE:
            message_classes = _MESSAGE_CLASSES_CACHE[cls]
        else:
            try:
                from typing import get_origin, get_args, get_type_hints
            except ImportError:
                from typing_extensions import get_origin, get_args
                from typing import get_type_hints
            
            type_hints = get_type_hints(cls, include_extras=True)
            message_classes = {}
            
            for field_name, field_type in type_hints.items():
                args = get_args(field_type)
                if args:
                    inner_type = args[0]
                else:
                    inner_type = field_type
                
                origin = get_origin(inner_type)
                if origin is list:
                    list_args = get_args(inner_type)
                    if list_args:
                        message_classes[field_name] = list_args[0]
                else:
                    try:
                        if hasattr(inner_type, '__dataclass_fields__'):
                            message_classes[field_name] = inner_type
                    except:
                        pass
            
            _MESSAGE_CLASSES_CACHE[cls] = message_classes
        
        # Convert each dict to message instance
        messages = []
        for result_dict in result_dicts:
            # Convert nested dicts to dataclass instances
            converted_dict = _convert_dicts_to_messages(cls, result_dict, specs, message_classes)
            messages.append(cls(**converted_dict))
        
        return messages, True
    
    except Exception:
        # Fall back to Python on any error
        return [cls.loads(buffer) for buffer in buffers], False


__all__ = [
    'RUST_AVAILABLE',
    'loads_with_rust',
    'loads_with_rust_batch',
    'extract_field_specs',
]
