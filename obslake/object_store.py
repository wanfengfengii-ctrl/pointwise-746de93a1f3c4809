from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile


@dataclass(frozen=True, slots=True)
class ObjectWriteResult:
    object_id: str
    success: bool
    reused: bool = False
    error: str | None = None


class LocalObjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def object_path(self, object_id):
        if not isinstance(object_id, str) or not re.fullmatch("[0-9a-f]{64}", object_id):
            raise ValueError("invalid object_id")
        return self.root / object_id[:2] / object_id[2:]

    def read_bytes(self, object_id):
        return self.object_path(object_id).read_bytes()

    def put_immutable(self, object_id, data):
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != object_id:
            raise ValueError("object identity does not match bytes")
        target = self.object_path(object_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise OSError("existing immutable object is corrupt")
            return ObjectWriteResult(object_id, True, True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".pending-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != data:
                    raise OSError("immutable object collision")
                return ObjectWriteResult(object_id, True, True)
            return ObjectWriteResult(object_id, True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
