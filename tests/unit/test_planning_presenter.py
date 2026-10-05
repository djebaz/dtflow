"""Plan-area text mapping and error ownership; planner mathematics live in test_planning.py."""

import pytest

from dtflow.services.planning import Plan
from dtflow.ui.generation_presenter import FormError
from dtflow.ui.planning_presenter import PlanForm, build_plan, count_text, field_for


def test_form_text_becomes_planner_dimensions_in_a_stable_order():
    form = PlanForm(
        "Pair",
        prompts="a cat\n\n a dog ",
        steps="4; 8",
        sizes="512x512; 768×512",
        seeds="3..4",
        loras="my:lora: 0.5; 1",
        increment=False,
    )
    assert build_plan(form) == Plan(
        "pair",
        (
            ("prompt", ("a cat", "a dog")),
            ("seed", (3, 4)),
            ("steps", (4, 8)),
            ("size", ((512, 512), (768, 512))),
            ("lora_weight.my:lora", (0.5, 1.0)),
        ),
    )
    assert build_plan(PlanForm(prompts="x", increment=True)).seed_policy == "increment"


@pytest.mark.parametrize(
    ("form", "widget"),
    [
        (PlanForm(steps="4; x"), "planStepsEdit"),
        (PlanForm(guidance="1,5"), "planGuidanceEdit"),
        (PlanForm(sizes="512"), "planSizesEdit"),
        (PlanForm(seeds="9..3"), "planSeedsEdit"),
        (PlanForm(seeds="0..5000"), "planSeedsEdit"),
        (PlanForm(loras="no weights here"), "planLorasEdit"),
    ],
)
def test_malformed_text_is_reported_against_its_own_widget(form, widget):
    with pytest.raises(FormError) as caught:
        build_plan(form)
    assert caught.value.widget == widget


def test_planner_messages_map_to_widgets_and_the_readout_states_the_count():
    assert field_for("Item 3: steps must be an integer in 1..150.") == "planStepsEdit"
    assert field_for("Dimension 'prompt' lists a value more than once; items would be identical.") == "planPromptsEdit"
    assert field_for("Plan varies LoRA 'x', which is not in the base request.") == "planLorasEdit"
    assert field_for("Item 1: height_px must be an integer in 128..2048.") == "planSizesEdit"
    assert field_for("something else") is None
    assert count_text(PlanForm(prompts="a\nb", steps="1;2;3")) == ("ok", "6 items")
    assert count_text(PlanForm(steps="1;2;3;4;5;6;7;8;9;10", seeds="0..99;100..109"))[0] == "error"
    assert count_text(PlanForm())[0] == "unknown"
