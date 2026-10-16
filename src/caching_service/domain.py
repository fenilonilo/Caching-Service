"""Pure payload rules: how an output is assembled and how a payload is identified.

Kept free of I/O so the core behaviour is testable without a database or a network.
"""

import json
from collections.abc import Sequence
from itertools import chain
from uuid import NAMESPACE_URL, UUID, uuid5

# Derived instead of random so ids stay identical across deployments and restarts.
_PAYLOAD_NAMESPACE = uuid5(NAMESPACE_URL, "caching-service/payload")


def build_output(first: Sequence[str], second: Sequence[str]) -> str:
    """Interleave two equally long lists: first[0], second[0], first[1], second[1], ..."""
    return ", ".join(chain.from_iterable(zip(first, second, strict=True)))


def payload_id(list_1: Sequence[str], list_2: Sequence[str]) -> UUID:
    """Content-derived id: identical input always maps to the same payload, with no lookup.

    JSON keeps list and string boundaries unambiguous, unlike joining with a separator.
    """
    canonical = json.dumps([list(list_1), list(list_2)], ensure_ascii=False, separators=(",", ":"))
    return uuid5(_PAYLOAD_NAMESPACE, canonical)
