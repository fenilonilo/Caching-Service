from uuid import UUID

import pytest

from caching_service.domain import build_output, payload_id


def test_build_output_interleaves_lists_starting_with_the_first() -> None:
    assert build_output(["A1", "A2", "A3"], ["B1", "B2", "B3"]) == "A1, B1, A2, B2, A3, B3"


def test_build_output_rejects_lists_of_different_length() -> None:
    with pytest.raises(ValueError):
        build_output(["A1", "A2"], ["B1"])


def test_payload_id_is_stable_for_identical_input() -> None:
    assert payload_id(["a", "b"], ["c", "d"]) == payload_id(["a", "b"], ["c", "d"])


def test_payload_id_never_changes_for_a_known_input() -> None:
    # Ids are persisted: changing the canonical form would orphan every stored payload.
    assert payload_id(["a"], ["b"]) == UUID("5919001c-2d11-5440-91c4-6d299c2190b4")


def test_payload_id_changes_with_order_or_list_assignment() -> None:
    reference = payload_id(["a", "b"], ["c", "d"])
    assert payload_id(["b", "a"], ["c", "d"]) != reference
    assert payload_id(["c", "d"], ["a", "b"]) != reference


def test_payload_id_keeps_string_boundaries() -> None:
    # Naively joining the strings would turn both inputs into "a, b, c".
    assert payload_id(["a, b", "c"], ["x", "y"]) != payload_id(["a", "b, c"], ["x", "y"])
