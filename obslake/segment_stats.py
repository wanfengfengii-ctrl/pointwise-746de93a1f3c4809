from dataclasses import dataclass
from .record_model import is_integer, require_text


@dataclass(frozen=True, slots=True)
class StationBounds:
    count: int
    min_time: int
    max_time: int

    def __post_init__(self):
        if not all(is_integer(x) for x in (self.count, self.min_time, self.max_time)):
            raise ValueError("invalid station statistics")
        if self.count <= 0 or self.min_time > self.max_time:
            raise ValueError("inconsistent station statistics")


@dataclass(frozen=True, slots=True)
class SegmentStatistics:
    count: int
    stations: tuple

    def __post_init__(self):
        if not is_integer(self.count) or self.count < 0 or not isinstance(self.stations, tuple):
            raise ValueError("invalid segment statistics")
        names = []
        total = 0
        for name, bounds in self.stations:
            require_text(name, "station")
            if not isinstance(bounds, StationBounds):
                raise ValueError("invalid bounds")
            names.append(name)
            total += bounds.count
        if names != sorted(set(names)) or total != self.count:
            raise ValueError("inconsistent segment statistics")

    def to_mapping(self):
        return {"count": self.count, "stations": [
            [name, bounds.count, bounds.min_time, bounds.max_time]
            for name, bounds in self.stations]}

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict) or set(value) != {"count", "stations"}:
            raise ValueError("invalid statistics mapping")
        if not isinstance(value["stations"], list):
            raise ValueError("invalid station list")
        result = []
        for item in value["stations"]:
            if not isinstance(item, list) or len(item) != 4:
                raise ValueError("invalid station entry")
            result.append((item[0], StationBounds(*item[1:])))
        return cls(value["count"], tuple(result))


def compute_segment_statistics(records):
    groups = {}
    total = 0
    for record in records:
        total += 1
        old = groups.get(record.station)
        if old is None:
            groups[record.station] = [1, record.time, record.time]
        else:
            old[0] += 1
            old[1] = min(old[1], record.time)
            old[2] = max(old[2], record.time)
    return SegmentStatistics(total, tuple(
        (name, StationBounds(*groups[name])) for name in sorted(groups)))


def may_intersect(statistics, request):
    if request.start == request.end:
        return False
    for name, bounds in statistics.stations:
        if request.station is not None and request.station != name:
            continue
        if bounds.max_time >= request.start and bounds.min_time < request.end:
            return True
    return False
