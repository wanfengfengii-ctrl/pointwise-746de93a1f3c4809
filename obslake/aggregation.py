from .product_model import SummaryRow


def summarize_records(records):
    groups = {}
    for record in records:
        state = groups.get(record.station)
        if state is None:
            groups[record.station] = [1, record.value, record.time, record.time]
        else:
            state[0] += 1
            state[1] += record.value
            state[2] = min(state[2], record.time)
            state[3] = max(state[3], record.time)
    return tuple(SummaryRow(station, *groups[station]) for station in sorted(groups))
