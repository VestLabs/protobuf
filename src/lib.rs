use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use rust_decimal::Decimal;
use prost::encoding::{decode_varint, decode_key, WireType};

/// Read unsigned varint from buffer at given position using prost.
/// Returns tuple (value, new_position).
fn read_unsigned_varint(buffer: &[u8], pos: usize) -> PyResult<(u64, usize)> {
    if pos >= buffer.len() {
        return Err(PyValueError::new_err("Unexpected end of buffer"));
    }
    
    let mut buf = &buffer[pos..];
    let original_len = buf.len();
    
    let value = decode_varint(&mut buf)
        .map_err(|e| PyValueError::new_err(format!("Failed to decode varint: {}", e)))?;
    
    let bytes_read = original_len - buf.len();
    Ok((value, pos + bytes_read))
}

/// Read signed varint using ZigZag encoding (prost-compatible).
/// Returns tuple (value, new_position).
fn read_signed_varint(buffer: &[u8], pos: usize) -> PyResult<(i64, usize)> {
    let (unsigned, new_pos) = read_unsigned_varint(buffer, pos)?;
    
    // ZigZag decoding: (n >> 1) ^ -(n & 1)
    let signed = ((unsigned >> 1) as i64) ^ -((unsigned & 1) as i64);
    
    Ok((signed, new_pos))
}

/// Read protobuf tag from buffer at given position using prost.
/// Returns tuple (field_number, wire_type, new_position).
fn read_tag(buffer: &[u8], pos: usize) -> PyResult<(u32, u8, usize)> {
    if pos >= buffer.len() {
        return Err(PyValueError::new_err("Unexpected end of buffer"));
    }
    
    let mut buf = &buffer[pos..];
    let original_len = buf.len();
    
    let (field_number, wire_type) = decode_key(&mut buf)
        .map_err(|e| PyValueError::new_err(format!("Failed to decode tag: {}", e)))?;
    
    let bytes_read = original_len - buf.len();
    Ok((field_number, wire_type as u8, pos + bytes_read))
}

// ============================================================================
// Generic Message Parser
// ============================================================================

use pyo3::types::{PyDict, PyList, PyBytes};
use std::collections::HashMap;

/// Field types for parsing
#[derive(Debug, Clone)]
enum FieldType {
    Int,           // int (varint)
    SignedInt,     // signed int (zigzag varint)
    Bool,          // bool (varint)
    Float,         // float (I32)
    Double,        // double (I64)
    String,        // string (LEN)
    Bytes,         // bytes (LEN)
    Decimal,       // decimal (LEN, encoded as string)
    Message,       // nested message (LEN)
}

impl FieldType {
    fn from_str(s: &str) -> PyResult<Self> {
        match s {
            "int" => Ok(FieldType::Int),
            "signed_int" => Ok(FieldType::SignedInt),
            "bool" => Ok(FieldType::Bool),
            "float" => Ok(FieldType::Float),
            "double" => Ok(FieldType::Double),
            "string" => Ok(FieldType::String),
            "bytes" => Ok(FieldType::Bytes),
            "decimal" => Ok(FieldType::Decimal),
            "message" => Ok(FieldType::Message),
            _ => Err(PyValueError::new_err(format!("Unsupported field type: {}", s))),
        }
    }
    
    fn expected_wire_type(&self) -> WireType {
        match self {
            FieldType::Int | FieldType::SignedInt | FieldType::Bool => WireType::Varint,
            FieldType::Float => WireType::ThirtyTwoBit,
            FieldType::Double => WireType::SixtyFourBit,
            FieldType::String | FieldType::Bytes | FieldType::Decimal | FieldType::Message => WireType::LengthDelimited,
        }
    }
}

/// Field specification from Python
#[derive(Debug)]
struct FieldSpec {
    number: u32,
    name: String,
    field_type: FieldType,
    is_repeated: bool,
    enum_class: Option<Py<PyAny>>,  // Optional IntEnum class for conversion
    nested_specs: Option<Box<HashMap<u32, FieldSpec>>>,  // For nested message parsing
}

/// Parse a single field value based on wire type
fn parse_field_value(
    buffer: &[u8],
    pos: usize,
    wire_type: WireType,
    field_type: &FieldType,
) -> PyResult<(PyObject, usize)> {
    Python::with_gil(|py| {
        match (wire_type, field_type) {
            // Varint types
            (WireType::Varint, FieldType::Int) => {
                let (value, new_pos) = read_unsigned_varint(buffer, pos)?;
                Ok((value.into_py(py), new_pos))
            }
            (WireType::Varint, FieldType::SignedInt) => {
                let (value, new_pos) = read_signed_varint(buffer, pos)?;
                Ok((value.into_py(py), new_pos))
            }
            (WireType::Varint, FieldType::Bool) => {
                let (value, new_pos) = read_unsigned_varint(buffer, pos)?;
                Ok(((value != 0).into_py(py), new_pos))
            }
            
            (WireType::ThirtyTwoBit, FieldType::Float) => {
                if pos + 4 > buffer.len() {
                    return Err(PyValueError::new_err("Unexpected end of buffer for float"));
                }
                let bytes = &buffer[pos..pos+4];
                let value = f32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
                Ok((value.into_py(py), pos + 4))
            }
            
            (WireType::SixtyFourBit, FieldType::Double) => {
                if pos + 8 > buffer.len() {
                    return Err(PyValueError::new_err("Unexpected end of buffer for double"));
                }
                let bytes = &buffer[pos..pos+8];
                let value = f64::from_le_bytes([
                    bytes[0], bytes[1], bytes[2], bytes[3],
                    bytes[4], bytes[5], bytes[6], bytes[7],
                ]);
                Ok((value.into_py(py), pos + 8))
            }
            
            (WireType::LengthDelimited, FieldType::String) => {
                let (length, new_pos) = read_unsigned_varint(buffer, pos)?;
                let end = new_pos + length as usize;
                if end > buffer.len() {
                    return Err(PyValueError::new_err("Unexpected end of buffer for string"));
                }
                let string = std::str::from_utf8(&buffer[new_pos..end])
                    .map_err(|e| PyValueError::new_err(format!("Invalid UTF-8: {}", e)))?;
                Ok((string.into_py(py), end))
            }
            (WireType::LengthDelimited, FieldType::Decimal) => {
                let (length, new_pos) = read_unsigned_varint(buffer, pos)?;
                let end = new_pos + length as usize;
                if end > buffer.len() {
                    return Err(PyValueError::new_err("Unexpected end of buffer for decimal"));
                }
                let string = std::str::from_utf8(&buffer[new_pos..end])
                    .map_err(|e| PyValueError::new_err(format!("Invalid UTF-8 in decimal: {}", e)))?;
                let decimal = string.parse::<Decimal>()
                    .map_err(|e| PyValueError::new_err(format!("Invalid decimal: {}", e)))?;
                Ok((decimal.into_py(py), end))
            }
            (WireType::LengthDelimited, FieldType::Bytes) => {
                let (length, new_pos) = read_unsigned_varint(buffer, pos)?;
                let end = new_pos + length as usize;
                if end > buffer.len() {
                    return Err(PyValueError::new_err("Unexpected end of buffer for bytes"));
                }
                let bytes = PyBytes::new_bound(py, &buffer[new_pos..end]);
                Ok((bytes.into_py(py), end))
            }
            (WireType::LengthDelimited, FieldType::Message) => {
                let (length, new_pos) = read_unsigned_varint(buffer, pos)?;
                let end = new_pos + length as usize;
                if end > buffer.len() {
                    return Err(PyValueError::new_err("Unexpected end of buffer for message"));
                }
                // Return raw bytes for nested messages - Python will parse them
                let bytes = PyBytes::new_bound(py, &buffer[new_pos..end]);
                Ok((bytes.into_py(py), end))
            }
            
            _ => Err(PyValueError::new_err(format!(
                "Wire type {:?} doesn't match field type {:?}",
                wire_type, field_type
            ))),
        }
    })
}

/// Skip a field value based on wire type
fn skip_field_at_pos(buffer: &[u8], pos: usize, wire_type: WireType) -> PyResult<usize> {
    match wire_type {
        WireType::Varint => {
            let (_, new_pos) = read_unsigned_varint(buffer, pos)?;
            Ok(new_pos)
        }
        WireType::SixtyFourBit => {
            if pos + 8 > buffer.len() {
                return Err(PyValueError::new_err("Unexpected end of buffer"));
            }
            Ok(pos + 8)
        }
        WireType::LengthDelimited => {
            let (length, new_pos) = read_unsigned_varint(buffer, pos)?;
            let end = new_pos + length as usize;
            if end > buffer.len() {
                return Err(PyValueError::new_err("Unexpected end of buffer"));
            }
            Ok(end)
        }
        WireType::ThirtyTwoBit => {
            if pos + 4 > buffer.len() {
                return Err(PyValueError::new_err("Unexpected end of buffer"));
            }
            Ok(pos + 4)
        }
        _ => Err(PyValueError::new_err(format!("Unsupported wire type for skipping: {:?}", wire_type))),
    }
}

/// Convert Python field specs dict to Rust HashMap
fn convert_field_specs(py: Python, field_specs: &Bound<'_, PyDict>) -> PyResult<HashMap<u32, FieldSpec>> {
    let mut specs_map: HashMap<u32, FieldSpec> = HashMap::new();
    
    for (key, value) in field_specs.iter() {
        let field_number: u32 = key.extract()?;
        let spec_tuple: &Bound<'_, pyo3::types::PyTuple> = value.downcast()?;
        
        let name: String = spec_tuple.get_item(0)?.extract()?;
        let type_str: String = spec_tuple.get_item(1)?.extract()?;
        let is_repeated: bool = spec_tuple.get_item(2)?.extract()?;
        
        // Optional: enum_class for IntEnum conversion (4th element)
        let enum_class: Option<Py<PyAny>> = if spec_tuple.len() > 3 {
            let enum_obj = spec_tuple.get_item(3)?;
            if enum_obj.is_none() {
                None
            } else {
                Some(enum_obj.unbind())
            }
        } else {
            None
        };
        
        // Optional: nested_specs for nested message parsing (5th element)
        let nested_specs: Option<Box<HashMap<u32, FieldSpec>>> = if spec_tuple.len() > 4 {
            let nested_obj = spec_tuple.get_item(4)?;
            if nested_obj.is_none() {
                None
            } else {
                // Recursively convert nested specs
                let nested_dict: &Bound<'_, PyDict> = nested_obj.downcast()?;
                let nested_map = convert_field_specs(py, nested_dict)?;
                Some(Box::new(nested_map))
            }
        } else {
            None
        };
        
        specs_map.insert(field_number, FieldSpec {
            number: field_number,
            name,
            field_type: FieldType::from_str(&type_str)?,
            is_repeated,
            enum_class,
            nested_specs,
        });
    }
    
    Ok(specs_map)
}

/// Recursively parse a message with given specs
fn parse_message_recursive(
    py: Python,
    buffer: &[u8],
    specs_map: &HashMap<u32, FieldSpec>,
    enum_cache: &mut HashMap<(u32, i64), Py<PyAny>>,
) -> PyResult<Py<PyDict>> {
    let result = PyDict::new_bound(py);
    let mut pos = 0;
    
    while pos < buffer.len() {
        // Read tag
        let (field_number, wire_type_u8, new_pos) = read_tag(buffer, pos)?;
        pos = new_pos;
        
        // Convert u8 to WireType using prost's WireType::try_from
        let wire_type = WireType::try_from(wire_type_u8 as u64)
            .map_err(|_| PyValueError::new_err(format!("Invalid wire type: {}", wire_type_u8)))?;
        
        // Find field spec
        if let Some(spec) = specs_map.get(&field_number) {
            // Verify wire type matches
            if wire_type != spec.field_type.expected_wire_type() {
                // Skip mismatched field
                pos = skip_field_at_pos(buffer, pos, wire_type)?;
                continue;
            }
            
            // Parse value - special handling for nested messages
            let (mut value, new_pos) = if matches!(spec.field_type, FieldType::Message) {
                // Read length-delimited data
                let (length, msg_pos) = read_unsigned_varint(buffer, pos)?;
                let end = msg_pos + length as usize;
                if end > buffer.len() {
                    return Err(PyValueError::new_err("Unexpected end of buffer for message"));
                }
                
                // If we have nested specs, parse recursively in Rust!
                if let Some(ref nested_specs) = spec.nested_specs {
                    let nested_dict = parse_message_recursive(py, &buffer[msg_pos..end], nested_specs, enum_cache)?;
                    (nested_dict.into_py(py), end)
                } else {
                    // No nested specs, return raw bytes
                    let bytes = PyBytes::new_bound(py, &buffer[msg_pos..end]);
                    (bytes.into_py(py), end)
                }
            } else {
                parse_field_value(buffer, pos, wire_type, &spec.field_type)?
            };
            pos = new_pos;
            
            // Convert int to IntEnum if enum_class is provided
            if let Some(ref enum_class) = spec.enum_class {
                if matches!(spec.field_type, FieldType::Int | FieldType::SignedInt) {
                    // Extract int value for caching
                    let int_value: i64 = value.bind(py).extract()?;
                    let cache_key = (spec.number, int_value);
                    
                    // Check cache first
                    if let Some(cached_enum) = enum_cache.get(&cache_key) {
                        // Cache hit - reuse existing instance
                        value = cached_enum.clone_ref(py);
                    } else {
                        // Cache miss - create new instance via FFI
                        let bound_value = value.bind(py);
                        match enum_class.bind(py).call1((bound_value,)) {
                            Ok(enum_instance) => {
                                let enum_py = enum_instance.unbind();
                                // Cache the instance for future use
                                enum_cache.insert(cache_key, enum_py.clone_ref(py));
                                value = enum_py;
                            }
                            Err(_) => {
                                // If conversion fails, keep original int value
                            }
                        }
                    }
                }
            }
            
            if spec.is_repeated {
                // Accumulate in list
                if let Some(existing) = result.get_item(&spec.name)? {
                    if let Ok(list) = existing.downcast::<PyList>() {
                        list.append(value)?;
                    } else {
                        // Create new list with old value and new value
                        let list = PyList::empty_bound(py);
                        list.append(existing)?;
                        list.append(value)?;
                        result.set_item(&spec.name, list)?;
                    }
                } else {
                    let list = PyList::empty_bound(py);
                    list.append(value)?;
                    result.set_item(&spec.name, list)?;
                }
            } else {
                result.set_item(&spec.name, value)?;
            }
        } else {
            // Unknown field - skip it
            pos = skip_field_at_pos(buffer, pos, wire_type)?;
        }
    }
    
    Ok(result.into())
}

/// Parse a protobuf message using field specifications.
/// 
/// Args:
///     buffer: bytes - protobuf encoded message
///     field_specs: dict - mapping of field_number -> (name, type_str, is_repeated, enum_class?, nested_specs?)
///         Example: {1: ("price", "float", False, None, None), 2: ("segment", "int", False, MarketHours, None)}
/// 
/// Returns:
///     dict - parsed field values by name
/// 
/// Example:
///     >>> specs = {1: ("price", "float", False, None, None), 2: ("size", "float", False, None, None)}
///     >>> parse_message(b'\x0d\x00\x00\xa0A\x15\x00\x00\xc8A', specs)
///     {'price': 20.0, 'size': 25.0}
#[pyfunction]
fn parse_message(py: Python, buffer: &[u8], field_specs: &Bound<'_, PyDict>) -> PyResult<Py<PyDict>> {
    // Convert Python field specs to Rust HashMap (with recursive nested specs)
    let specs_map = convert_field_specs(py, field_specs)?;
    
    // Create local enum cache for this parse operation
    let mut enum_cache: HashMap<(u32, i64), Py<PyAny>> = HashMap::new();
    
    // Parse message recursively
    parse_message_recursive(py, buffer, &specs_map, &mut enum_cache)
}

/// Parse multiple protobuf messages in batch (reduces FFI overhead).
/// 
/// Args:
///     buffers: list[bytes] - list of protobuf encoded messages
///     field_specs: dict - field specifications (same as parse_message)
/// 
/// Returns:
///     list[dict] - list of parsed messages
/// 
/// Example:
///     >>> buffers = [msg1_bytes, msg2_bytes, msg3_bytes]
///     >>> results = parse_message_batch(buffers, specs)
///     >>> len(results)
///     3
#[pyfunction]
fn parse_message_batch(
    py: Python,
    buffers: &Bound<'_, pyo3::types::PyList>,
    field_specs: &Bound<'_, PyDict>,
) -> PyResult<Py<pyo3::types::PyList>> {
    // Convert field specs once (shared across all messages)
    let specs_map = convert_field_specs(py, field_specs)?;
    
    // Create shared enum cache for all messages in batch
    // This is where we get massive speedup: enum instances created once, reused across batch!
    let mut enum_cache: HashMap<(u32, i64), Py<PyAny>> = HashMap::new();
    
    // Create result list
    let result_list = pyo3::types::PyList::empty_bound(py);
    
    // Parse each buffer
    for item in buffers.iter() {
        let buffer: &[u8] = item.extract()?;
        let parsed_dict = parse_message_recursive(py, buffer, &specs_map, &mut enum_cache)?;
        result_list.append(parsed_dict)?;
    }
    
    Ok(result_list.into())
}

/// A Python module implemented in Rust.
#[pymodule]
fn pure_protobuf_rust(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Core parsing functions
    m.add_function(wrap_pyfunction!(parse_message, m)?)?;
    m.add_function(wrap_pyfunction!(parse_message_batch, m)?)?;
    Ok(())
}

