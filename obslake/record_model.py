from dataclasses import dataclass


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def require_text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(name + " must be a nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(name + " must be UTF-8 encodable") from exc
    return value


@dataclass(frozen=True, slots=True)
class Record:
    record_id: str
    station: str
    time: int
    value: int

    def __post_init__(self):
        require_text(self.record_id, "record_id")
        require_text(self.station, "station")
        if not is_integer(self.time) or not is_integer(self.value):
            raise ValueError("time and value must be non-boolean integers")

    def as_list(self):
        return [self.record_id, self.station, self.time, self.value]

    def as_mapping(self):
        return dict(zip(("record_id", "station", "time", "value"), self.as_list()))


def record_sort_key(record):
    return record.station, record.time, record.record_id
