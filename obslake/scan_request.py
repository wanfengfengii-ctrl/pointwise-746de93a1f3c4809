from dataclasses import dataclass
from .record_model import is_integer, require_text


@dataclass(frozen=True, slots=True)
class ScanRequest:
    station: str | None
    start: int
    end: int

    def __post_init__(self):
        if self.station is not None:
            require_text(self.station, "station")
        if not is_integer(self.start) or not is_integer(self.end) or self.start > self.end:
            raise ValueError("invalid half-open time window")


def validate_scan_request(station, start, end):
    return ScanRequest(station, start, end)


def contains_record(request, record):
    return ((request.station is None or request.station == record.station)
            and request.start <= record.time < request.end)
