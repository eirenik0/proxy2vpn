"""Pure batch reservations for proxy and control host TCP ports."""


def allocate_port_pairs(
    occupied: set[int],
    count: int,
    *,
    proxy_start: int = 20000,
    control_start: int = 30000,
) -> list[tuple[int, int]]:
    """Return a complete reservation batch or fail without changing OCCUPIED."""
    if count < 0 or not 1 <= proxy_start <= 65535 or not 1 <= control_start <= 65535:
        raise ValueError(
            "Port allocation requires a nonnegative count and ports in 1..65535"
        )
    reserved = set(occupied)

    def reserve(start: int) -> int:
        while start in reserved and start <= 65535:
            start += 1
        if start > 65535:
            raise ValueError("Host port range exhausted")
        reserved.add(start)
        return start

    pairs = []
    for _ in range(count):
        proxy = reserve(proxy_start)
        control = reserve(control_start)
        pairs.append((proxy, control))
        proxy_start, control_start = proxy + 1, control + 1
    return pairs
