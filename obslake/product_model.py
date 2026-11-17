from dataclasses import dataclass
import json
from .record_model import is_integer, require_text
from .scan_request import ScanRequest


@dataclass(frozen=True, slots=True)
class ProductRequest:
    kind: str
    snapshot_id: str
    station: str | None
    start: int
    end: int

    def __post_init__(self):
        if self.kind not in ("summary", "export"):
            raise ValueError("unknown product kind")
        require_text(self.snapshot_id, "snapshot_id")
        ScanRequest(self.station, self.start, self.end)


@dataclass(frozen=True, slots=True)
class SummaryRow:
    station: str
    count: int
    sum: int
    first_time: int
    last_time: int

    def __post_init__(self):
        require_text(self.station, "station")
        if not all(is_integer(x) for x in (self.count, self.sum, self.first_time, self.last_time)):
            raise ValueError("summary values must be integers")
        if self.count <= 0 or self.first_time > self.last_time:
            raise ValueError("invalid summary bounds")

    def as_mapping(self):
        return {"station": self.station, "count": self.count, "sum": self.sum,
                "first_time": self.first_time, "last_time": self.last_time}


@dataclass(frozen=True, slots=True)
class SavedProduct:
    product_id: str
    request: ProductRequest
    summary: tuple = ()
    export_bytes: bytes = b""

    def __post_init__(self):
        if not isinstance(self.summary, tuple) or not isinstance(self.export_bytes, bytes):
            raise ValueError("product content must be immutable")
        if self.request.kind == "summary":
            if self.export_bytes or any(not isinstance(row, SummaryRow) for row in self.summary):
                raise ValueError("invalid summary content")
            names = [row.station for row in self.summary]
            if names != sorted(set(names)):
                raise ValueError("summary station order invalid")
        elif self.summary:
            raise ValueError("export cannot contain summary rows")


def canonical_product_request(request):
    return json.dumps([1, request.kind, request.snapshot_id, request.station, request.start, request.end],
                      ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
