from arena.state.voting import (
	_ranking_choices_from_state,
	_remaining_vote_choices,
	_vote_response,
	reset_vote,
)


def test_ranking_choices_from_state_ignores_invalid_values() -> None:
	first_choice, second_choice, third_choice = _ranking_choices_from_state(
		{
			"first_choice": "Response A",
			"second_choice": "Not a label",
			"third_choice": "Response C",
		}
	)

	assert first_choice == "Response A"
	assert second_choice is None
	assert third_choice == "Response C"


def test_remaining_vote_choices_excludes_selected_labels() -> None:
	remaining = _remaining_vote_choices(
		{
			"first_choice": "Response B",
			"second_choice": "Response C",
			"third_choice": None,
		}
	)

	assert remaining == ["Response A"]


def test_vote_response_records_first_then_second_choice() -> None:
	initial_state = {
		"round_id": "round-1",
		"ready_for_vote": True,
		"submitted": False,
		"first_choice": None,
		"second_choice": None,
		"third_choice": None,
		"vote_stage": "pick_first",
	}

	after_first_pick = _vote_response(initial_state, "Response B")
	after_second_pick = _vote_response(after_first_pick, "Response A")

	assert after_first_pick["first_choice"] == "Response B"
	assert after_first_pick["second_choice"] is None
	assert after_first_pick["vote_stage"] == "pick_second"
	assert after_second_pick["second_choice"] == "Response A"
	assert after_second_pick["third_choice"] == "Response C"
	assert after_second_pick["vote_stage"] == "ready_submit"


def test_reset_vote_clears_rankings_for_active_round() -> None:
	state = {
		"round_id": "round-1",
		"ready_for_vote": True,
		"submitted": False,
		"first_choice": "Response A",
		"second_choice": "Response B",
		"third_choice": "Response C",
		"vote_stage": "ready_submit",
	}

	updated_state, *_ = reset_vote(state)

	assert updated_state["first_choice"] is None
	assert updated_state["second_choice"] is None
	assert updated_state["third_choice"] is None
	assert updated_state["vote_stage"] == "pick_first"
