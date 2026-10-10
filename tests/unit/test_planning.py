"""Queue planner contracts (6-O1 to 6-O3): pure expansion, ordering, bounds and failure before enqueueing."""

import json

import pytest

from dtflow.core.models import LoRASelection, ResolvedGenerationRequest, ValidationError
from dtflow.services.config import ConfigError
from dtflow.services.planning import (
    Plan,
    PlanError,
    describe_plan,
    expand_plan,
    load_plan,
    parse_plan,
    plan_size,
    seed_range,
)


def base(seed=7, **extra):
    return ResolvedGenerationRequest(
        "base", "a cat", "fake-fixture", seed, resolution_evidence=(("source", "t"),), **extra
    )


def plan(mode, seed=None, **dims):
    return Plan(mode, tuple(dims.items()), seed_policy=seed)


def varied(items, *attrs):
    return [tuple(getattr(item, attr) for attr in attrs) for item in items]


def test_seed_mode_is_the_count_rule_with_exact_queue_position_evidence():
    items = expand_plan(base(), Plan("seed", count=3))
    assert [item.seed for item in items] == [7, 8, 9] and len({item.request_id for item in items}) == 3
    assert dict(items[2].resolution_evidence) == {"source": "t", "queue_position": "3"}


def test_pair_zips_dimensions_in_listed_order_and_keeps_the_base_seed_fixed():
    """6-O1: matched inputs give explicit stable rows; the size is one paired dimension."""
    items = expand_plan(
        base(),
        plan("pair", prompt=["a", "b", "c"], steps=[4, 8, 12], size=[[512, 512], [768, 512], [512, 768]]),
    )
    assert varied(items, "prompt", "steps", "width_px", "height_px") == [
        ("a", 4, 512, 512),
        ("b", 8, 768, 512),
        ("c", 12, 512, 768),
    ]
    assert {item.seed for item in items} == {7}
    evidence = dict(items[1].resolution_evidence)
    assert evidence["plan.index"] == "2" and evidence["plan.dimension.size"] == "768x512"
    assert evidence["plan.mode"] == "pair" and evidence["plan.seed"] == "fixed" and evidence["source"] == "t"


def test_pair_mismatch_names_every_length_and_nothing_is_truncated():
    with pytest.raises(PlanError, match=r"equal lengths \(prompt=3, steps=2\)"):
        expand_plan(base(), plan("pair", prompt=["a", "b", "c"], steps=[4, 8]))


def test_cross_cardinality_is_arithmetic_and_the_last_dimension_varies_fastest():
    """6-O2: exact size and nested-loop order; seed dimension and LoRA weights take part like any other."""
    chosen = base(loras=(LoRASelection("style", 0.5),))
    spec = Plan("cross", (("prompt", ("a", "b")), ("seed", (1, 2, 3)), ("lora_weight.style", (0.4, 0.8))))
    assert plan_size(spec) == 12
    items = expand_plan(chosen, spec)
    assert varied(items, "prompt", "seed")[:4] == [("a", 1), ("a", 1), ("a", 2), ("a", 2)]
    assert [item.loras[0].weight for item in items[:4]] == [0.4, 0.8, 0.4, 0.8]
    assert items[-1].prompt == "b" and items[-1].seed == 3
    assert len({item.request_id for item in items}) == 12
    assert dict(items[0].resolution_evidence)["plan.dimension.lora_weight.style"] == "0.4"


def test_seed_policy_increment_numbers_items_and_is_refused_next_to_a_seed_dimension():
    items = expand_plan(base(), plan("cross", seed="increment", prompt=["a", "a2"], steps=[4, 4]))
    assert [item.seed for item in items] == [7, 8, 9, 10]  # distinct seeds, so repeated values are not duplicates
    with pytest.raises(PlanError, match="both"):
        plan_size(Plan("pair", (("seed", (1,)),), seed_policy="increment"))


def test_dry_run_table_matches_the_expansion_without_persisting_anything():
    table = describe_plan(base(), plan("pair", prompt=["a", "b"], steps=[4, 8]))
    assert (table.mode, table.seed_policy, table.total) == ("pair", "fixed", 2)
    assert table.columns == ("#", "prompt", "steps", "seed")
    assert table.rows == (("1", "a", "4", "7"), ("2", "b", "8", "7"))
    listed = describe_plan(base(), Plan("cross", (("seed", (3, 4)),)))
    assert listed.columns == ("#", "seed") and listed.seed_policy == "dimension"


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (Plan("bogus", (("prompt", ("a",)),)), "mode"),
        (Plan("cross"), "at least one dimension"),
        (Plan("cross", (("prompt", ()),)), "no values"),
        (Plan("cross", (("prompt", ("a",)), ("prompt", ("b",)))), "only once"),
        (Plan("cross", (("color", ("red",)),)), "Unknown plan dimension"),
        (Plan("cross", (("prompt", ("a", "a")),)), "identical"),
        (Plan("pair", (("prompt", ("a", "a")), ("steps", (4, 4)))), "identical"),
        (Plan("seed", (("prompt", ("a",)),), count=2), "no dimensions"),
        (Plan("seed", count=0), "count"),
        (Plan("seed", count=1001), "count"),
        (Plan("seed", count=True), "count"),
        (Plan("seed", count=2, seed_policy="fixed"), "increments"),
        (Plan("cross", (("prompt", ("a",)),), count=2), "seed plans only"),
        (Plan("cross", (("prompt", ("a",)),), seed_policy="random"), "Seed policy"),
        (Plan("cross", (("steps", (4, 500)),)), "steps"),
        (Plan("cross", (("steps", (True, 8)),)), "steps"),
        (Plan("cross", (("sampler", ("not-a-sampler",)),)), "sampler"),
        (Plan("cross", (("size", ((512, 200),)),)), "multiple of 64"),
        (Plan("cross", (("size", ((512,),)),)), "pair"),
        (Plan("cross", (("seed", (-1,)),)), "seed"),
        (Plan("cross", (("seed", (4294967296,)),)), "seed"),
        (Plan("cross", (("guidance", (float("nan"),)),)), "guidance"),
        (Plan("cross", (("lora_weight.missing", (0.5,)),)), "not in the base"),
        (Plan("cross", (("lora_weight.style", (float("inf"),)),)), "weight"),
        (Plan("cross", (("prompt", ({"x": 1},)),)), "text, numbers"),
    ],
)
def test_invalid_plans_fail_before_anything_is_enqueued(spec, message):
    """6-O3: every failure is a PlanError (a ValidationError) raised by the planner itself."""
    with pytest.raises(PlanError, match=message) as caught:
        expand_plan(base(loras=(LoRASelection("style", 0.5),)), spec)
    assert isinstance(caught.value, ValidationError)
    overflow = Plan("pair", (("prompt", ("a", "b")),), seed_policy="increment")
    with pytest.raises(PlanError, match="32-bit"):
        expand_plan(base(4294967295), overflow)


def test_oversize_cross_is_refused_by_arithmetic_without_building_the_matrix():
    huge = Plan("cross", tuple((f"{name}", tuple(range(1, 40))) for name in ("steps", "seed", "guidance")))
    assert plan_size(huge) == 39**3  # 59319 > 1000, counted without enumerating
    with pytest.raises(PlanError, match="59319 items"):
        expand_plan(base(), huge)
    assert plan_size(Plan("cross", (("steps", tuple(range(1, 11))), ("seed", tuple(range(100)))))) == 1000
    with pytest.raises(PlanError, match="1001 items"):
        expand_plan(base(), Plan("cross", (("steps", tuple(range(1, 8))), ("seed", tuple(range(143))))))
    for start, count in ((0, 0), (0, 1001), (4294967295, 2), (-1, 3), (True, 2)):
        with pytest.raises(PlanError):
            seed_range(start, count)


def document(**fields):
    return {"schema": "dtflow.queue-plan", "version": 1, **fields}


def test_plan_documents_are_versioned_credential_free_and_bounded(tmp_path):
    parsed = parse_plan(
        document(
            mode="cross",
            dimensions={
                "prompt": ["a", "b"],
                "seed": {"start": 10, "count": 3},
                "size": [[512, 512]],
                "lora_weight": [{"identifier": "style", "weights": [0.5, 1]}],
            },
        )
    )
    assert [name for name, _ in parsed.dimensions] == ["prompt", "seed", "size", "lora_weight.style"]
    assert plan_size(parsed) == 2 * 3 * 1 * 2 and dict(parsed.dimensions)["seed"] == (10, 11, 12)
    assert parse_plan(document(mode="seed", count=4)) == Plan("seed", count=4)
    for bad, message in (
        ([], "JSON object"),
        ({"schema": "other", "version": 1}, "schema"),
        (document(mode="seed", count=2) | {"version": 2}, "version 2"),
        (document(mode="seed", count=2, extra=1), "Unknown plan field"),
        (document(mode="cross", dimensions=[]), "object"),
        (document(mode="cross", dimensions={"prompt": "a"}), "list of values"),
        (document(mode="cross", dimensions={"color": ["red"]}), "Unknown plan dimension"),
        (document(mode="cross", dimensions={"seed": {"start": 1}}), "start and count"),
        (document(mode="cross", dimensions={"lora_weight": [{"identifier": "x"}]}), "identifier and weights"),
        (document(mode="cross", dimensions={"prompt": ["a"], "api_key": ["x"]}), "credentials"),
        (document(mode="pair", dimensions={"prompt": ["a"], "steps": [1, 2]}), "equal lengths"),
        (document(mode="seed"), "count"),
    ):
        with pytest.raises(PlanError, match=message):
            parse_plan(bad)
    good = tmp_path / "plan.json"
    good.write_text(json.dumps(document(mode="seed", count=2)), encoding="utf-8")
    assert load_plan(good) == Plan("seed", count=2)
    for name, content in (("bad.json", b"{"), ("big.json", b" " * (1024 * 1024 + 1))):
        (tmp_path / name).write_bytes(content)
        with pytest.raises(ConfigError):
            load_plan(tmp_path / name)
    with pytest.raises(ConfigError, match="could not be read"):
        load_plan(tmp_path / "absent.json")
