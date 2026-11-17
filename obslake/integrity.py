import hashlib


class Unavailable(Exception):
    pass


def verify_ordinary_object(descriptor, data):
    if not isinstance(data, bytes) or len(data) != descriptor.byte_length:
        raise Unavailable("ordinary object length mismatch")
    if hashlib.sha256(data).hexdigest() != descriptor.sha256:
        raise Unavailable("ordinary object digest mismatch")
    return data
