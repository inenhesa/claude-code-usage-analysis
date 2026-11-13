from relayd.cache import cache_key
from relayd.codec import encode_varint
from relayd.retry import backoff


def test_varint_keeps_going_past_14_bits():
    encoded = encode_varint(16384)
    assert encoded != bytes([16384 & 0x7F])
    assert encoded[-1] & 0x80 == 0
    assert len(encoded) >= 2


def test_cache_key_is_the_whole_payload():
    left = b"0123456789abcdef-a"
    right = b"0123456789abcdef-b"
    assert cache_key(left) != cache_key(right)


def test_backoff_respects_the_cap():
    assert backoff(20) == 30
