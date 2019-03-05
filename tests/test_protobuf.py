#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
`pure-protobuf` contributors © 2011-2019
"""

from __future__ import absolute_import

from pytest import mark, raises

from pure_protobuf import (
    Bool,
    Bytes,
    EmbeddedMessage,
    Flags,
    Int32,
    Int64,
    MessageType,
    UInt64,
    Unicode,
    UVarint,
    Varint,
)

try:
    # noinspection PyCompatibility
    from StringIO import StringIO
except ImportError:
    from io import StringIO


@mark.parametrize('value, expected', [
    (0, '\x00'),
    (3, '\x03'),
    (270, '\x8E\x02'),
    (86942, '\x9E\xA7\x05'),
])
def test_dumps_uvarint(value, expected):
    assert UVarint.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\x00', 0),
    ('\x03', 3),
    ('\x8E\x02', 270),
    ('\x9E\xA7\x05', 86942),
])
def test_loads_uvarint(value, expected):
    assert UVarint.loads(value) == expected


@mark.parametrize('value, expected', [
    (0, '\x00'),
    (-1, '\x01'),
    (1, '\x02'),
    (-2, '\x03'),
])
def test_dumps_varint(value, expected):
    assert Varint.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\x00', 0),
    ('\x01', -1),
    ('\x02', 1),
    ('\x03', -2),
])
def test_loads_varint(value, expected):
    assert Varint.loads(value) == expected


@mark.parametrize('value, expected', [
    (True, '\x01'),
    (False, '\x00'),
])
def test_dumps_bool(value, expected):
    assert Bool.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\x00', False),
    ('\x01', True),
])
def test_loads_bool(value, expected):
    assert Bool.loads(value) == expected


@mark.parametrize('value, expected', [
    (1, '\x00\x00\x00\x00\x00\x00\x00\x01'),
])
def test_dumps_uint64(value, expected):
    assert UInt64.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\x00\x00\x00\x00\x00\x00\x00\x01', 1),
])
def test_loads_uint64(value, expected):
    assert UInt64.loads(value) == expected


@mark.parametrize('value, expected', [
    (-2, '\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFE'),
])
def test_dumps_int64(value, expected):
    assert Int64.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFE', -2),
])
def test_loads_int64(value, expected):
    assert Int64.loads(value) == expected


@mark.parametrize('value, expected', [
    (-2, '\xFF\xFF\xFF\xFE'),
])
def test_dumps_int32(value, expected):
    assert Int32.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\xFF\xFF\xFF\xFE', -2),
])
def test_loads_int32(value, expected):
    assert Int32.loads(value) == expected


@mark.parametrize('value, expected', [
    ('testing', '\x07\x74\x65\x73\x74\x69\x6e\x67'),
])
def test_dumps_bytes(value, expected):
    assert Bytes.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\x07\x74\x65\x73\x74\x69\x6e\x67', 'testing'),
])
def test_loads_bytes(value, expected):
    assert Bytes.loads(value) == expected


@mark.parametrize('value, expected', [
    (u'Привет', '\x0c\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82'),
])
def test_dumps_unicode(value, expected):
    assert Unicode.dumps(value) == expected


@mark.parametrize('value, expected', [
    ('\x0c\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82', u'Привет'),
])
def test_loads_unicode(value, expected):
    assert Unicode.loads(value) == expected


def test_dump_message_type():
    message_type = MessageType()
    message_type.add_field(2, 'b', Bytes)
    msg = message_type()
    msg.b = 'testing'
    fp = StringIO()
    msg.dump(fp)
    assert fp.getvalue() == '\x12\x07\x74\x65\x73\x74\x69\x6e\x67'


def test_dumps_message_type():
    message_type = MessageType()
    message_type.add_field(2, 'b', Bytes)
    msg = message_type()
    msg.b = 'testing'
    assert msg.dumps() == '\x12\x07\x74\x65\x73\x74\x69\x6e\x67'


def test_dumps_missing_optional_value():
    message_type = MessageType()
    message_type.add_field(2, 'b', Bytes)
    msg = message_type()
    assert msg.dumps() == ''


def test_dumps_missing_required_value():
    message_type = MessageType()
    message_type.add_field(2, 'b', Bytes, flags=Flags.REQUIRED)
    msg = message_type()
    with raises(ValueError):
        msg.dumps()


def test_dumps_repeated_value():
    message_type = MessageType()
    message_type.add_field(1, 'b', UVarint, flags=Flags.REPEATED)
    msg = message_type()
    msg.b = (1, 2, 3)
    assert msg.dumps() == '\x08\x01\x08\x02\x08\x03'


def test_dumps_packed_repeated_value():
    message_type = MessageType()
    message_type.add_field(4, 'd', UVarint, flags=Flags.PACKED_REPEATED)
    msg = message_type()
    msg.d = (3, 270, 86942)
    assert msg.dumps() == '\x22\x06\x03\x8E\x02\x9E\xA7\x05'


def test_loads_missing_optional_value():
    message_type = MessageType()
    message_type.add_field(2, 'b', Bytes)
    msg = message_type.loads('')
    assert 'b' not in msg


def test_loads_missing_required_value():
    message_type = MessageType()
    message_type.add_field(2, 'b', Bytes, flags=Flags.REQUIRED)
    with raises(ValueError):
        message_type.loads('')


def test_loads_non_repeated():
    """
    Tests that the last value from the input stream is assigned to a non-repeated field.
    """
    message_type = MessageType()
    message_type.add_field(1, 'b', UVarint)
    msg = message_type.loads('\x08\x01\x08\x02\x08\x03')
    assert msg.b == 3


def test_loads_repeated_value():
    """
    Tests repeated value.
    """
    message_type = MessageType()
    message_type.add_field(1, 'b', UVarint, flags=Flags.REPEATED)
    msg = message_type.loads('\x08\x01\x08\x02\x08\x03')
    assert 'b' in msg
    assert msg.b == [1, 2, 3]


def test_loads_packed_repeated_value():
    message_type = MessageType()
    message_type.add_field(4, 'd', UVarint, flags=Flags.PACKED_REPEATED)
    msg = message_type.loads('\x22\x06\x03\x8E\x02\x9E\xA7\x05')
    assert 'd' in msg
    assert msg.d == [3, 270, 86942]


def test_hash():
    type_1 = MessageType().add_field(1, 'b', UVarint)
    type_2 = MessageType().add_field(1, 'a', UVarint)
    type_3 = MessageType().add_field(2, 'a', UVarint)
    type_4 = MessageType().add_field(1, 'b', UVarint, flags=Flags.REPEATED)
    assert hash(type_1) == hash(type_2)
    assert hash(type_1) != hash(type_3)
    assert hash(type_1) != hash(type_4)


def test_iter():
    message_type = MessageType()
    message_type.add_field(1, 'b', UVarint, flags=Flags.REPEATED)
    message_type.add_field(2, 'c', Bytes, flags=Flags.PACKED_REPEATED)
    assert list(message_type) == [
        (1, 'b', UVarint, Flags.REPEATED),
        (2, 'c', Bytes, Flags.PACKED_REPEATED),
    ]


def test_empty_optional_bytes():
    message_type = MessageType()
    message_type.add_field(1, 'a', Bytes)
    msg = message_type.loads('\n\x00')
    assert msg.a == ''


def test_delattr():
    message_type = MessageType()
    message_type.add_field(2, 'b', Bytes)
    message = message_type()
    message.b = 'testing'
    del message.b
    assert not hasattr(message, 'b')


def test_dumps_embedded_message():
    type_1 = MessageType().add_field(1, 'a', UVarint)
    type_3 = MessageType().add_field(3, 'c', EmbeddedMessage(type_1))
    msg = type_3()
    msg.c = type_1()
    msg.c.a = 150
    assert msg.dumps() == '\x1a\x03\x08\x96\x01'


def test_dumps_loads_embedded_message():
    type_2 = MessageType().add_field(1, 'a', UVarint)
    type_1 = MessageType()
    type_1.add_field(1, 'a', UVarint)
    type_1.add_field(2, 'b', EmbeddedMessage(type_2))
    type_1.add_field(3, 'c', UVarint)
    msg = type_1()
    msg.a = 1
    msg.c = 3
    msg.b = type_2()
    msg.b.a = 2
    msg = type_1.loads(msg.dumps())
    assert msg.a == 1
    assert msg.c == 3
    assert msg.b.a == 2


def test_loads_embedded_message():
    type_1 = MessageType().add_field(1, 'a', UVarint)
    type_3 = MessageType().add_field(3, 'c', EmbeddedMessage(type_1))
    msg = type_3.loads('\x1a\x03\x08\x96\x01')
    assert 'c' in msg
    assert 'a' in msg.c
    assert msg.c.a == 150
