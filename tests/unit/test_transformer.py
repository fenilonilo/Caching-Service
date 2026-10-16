from caching_service.infrastructure.transformer import UppercaseTransformer


async def test_uppercase_transformer_uppercases_like_the_task_example() -> None:
    assert await UppercaseTransformer().transform("first string") == "FIRST STRING"
