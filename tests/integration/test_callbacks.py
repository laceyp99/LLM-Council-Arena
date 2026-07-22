from arena import app as app_module


def _patch_log_paths(monkeypatch, tmp_path) -> None:
	logs_dir = tmp_path / "arena_logs"
	session_logs_dir = logs_dir / "sessions"
	monkeypatch.setattr(app_module, "APP_DIR", tmp_path)
	monkeypatch.setattr(app_module, "LOGS_DIR", logs_dir)
	monkeypatch.setattr(app_module, "SESSION_LOGS_DIR", session_logs_dir)
	monkeypatch.setattr(app_module, "META_LOG_FILE", logs_dir / "meta.json")
	monkeypatch.setattr(app_module, "VOTES_FILE", tmp_path / "votes.json")


def _build_completed_round_state(
	*,
	first_choice: str,
	second_choice: str,
	third_choice: str,
	created_at: str,
	submitted_at: str,
	display_order: list[int] | None = None,
) -> dict[str, object]:
	resolved_display_order = display_order if display_order is not None else [2, 0, 1]
	round_state = app_module._build_round_state(
		user_text="Compare these models.",
		system_prompt="System prompt",
		message_payload=app_module._build_messages("Compare these models.", "System prompt"),
		model_ids=["alpha/one", "beta/two", "gamma/three"],
		display_order=resolved_display_order,
		chatbot_label=lambda model_id: f"Label for {model_id}",
		provider_for_model=lambda model_id: model_id.split("/", 1)[0],
	)
	round_state["created_at"] = created_at
	round_state["generation_completed_at"] = submitted_at
	round_state["submitted_at"] = submitted_at
	round_state["submitted"] = True
	round_state["completed_slots"] = [0, 1, 2]
	round_state["ready_for_vote"] = True
	round_state["vote_stage"] = "submitted"
	round_state["first_choice"] = first_choice
	round_state["second_choice"] = second_choice
	round_state["third_choice"] = third_choice

	for slot, answer in enumerate(["Alpha answer", "Beta answer", "Gamma answer"]):
		round_state["slot_logs"][slot]["status"] = "complete"
		round_state["slot_logs"][slot]["final_response"] = answer
		round_state["slot_logs"][slot]["message_history"] = [
			{"role": "assistant", "content": answer}
		]

	return round_state


def test_update_panel_provider_resolves_model_choices_and_resets_outputs(monkeypatch) -> None:
	reset_outputs = (
		"chat-1",
		"chat-2",
		"chat-3",
		"round-state",
		"vote-a",
		"vote-b",
		"vote-c",
		"vote-reset",
		"vote-submit",
	)
	monkeypatch.setattr(app_module, "_resolve_model_for_provider", lambda provider_key: "beta/two")
	monkeypatch.setattr(
		app_module,
		"_reasoning_control_updates",
		lambda model_id: (
			"reasoning-effort",
			"reasoning-cost-hint",
		),
	)
	monkeypatch.setattr(
		app_module,
		"_model_choices_for_provider",
		lambda provider_key: [("Two", "beta/two")],
	)
	monkeypatch.setattr(app_module, "_reset_arena_outputs", lambda: reset_outputs)
	monkeypatch.setattr(app_module, "_selectors_interactive", lambda: True)
	monkeypatch.setattr(
		app_module.gr,
		"Dropdown",
		lambda **kwargs: {
			"choices": kwargs["choices"],
			"value": kwargs["value"],
			"interactive": kwargs["interactive"],
		},
	)

	outputs = app_module.update_panel_provider("beta")

	assert outputs == (
		{"choices": [("Two", "beta/two")], "value": "beta/two", "interactive": True},
		"reasoning-effort",
		"reasoning-cost-hint",
		*reset_outputs,
	)


def test_update_panel_model_resets_arena_outputs(monkeypatch) -> None:
	reset_outputs = (
		"chat-1",
		"chat-2",
		"chat-3",
		"round-state",
		"vote-a",
		"vote-b",
		"vote-c",
		"vote-reset",
		"vote-submit",
	)
	monkeypatch.setattr(app_module, "_reset_arena_outputs", lambda: reset_outputs)
	monkeypatch.setattr(
		app_module,
		"_reasoning_control_updates",
		lambda model_id: (
			"reasoning-effort",
			"reasoning-cost-hint",
		),
	)

	assert app_module.update_panel_model("beta/two") == (
		"reasoning-effort",
		"reasoning-cost-hint",
		*reset_outputs,
	)


def test_reasoning_control_updates_hide_controls_for_unsupported_models(monkeypatch) -> None:
	monkeypatch.setattr(app_module, "MODEL_LOOKUP", {"alpha/one": {}})
	monkeypatch.setattr(app_module, "_selectors_interactive", lambda: True)

	effort, cost_hint = app_module._reasoning_control_updates("alpha/one")

	assert effort["visible"] is False
	assert cost_hint["visible"] is False


def test_reasoning_control_updates_show_effort_for_generic_reasoning(monkeypatch) -> None:
	monkeypatch.setattr(
		app_module,
		"MODEL_LOOKUP",
		{
			"alpha/one": {
				"supported_parameters": ["include_reasoning", "max_tokens", "reasoning"],
				"top_provider": {"max_completion_tokens": 8192},
				"default_parameters": {"reasoning": {"max_tokens": 2048}},
				"pricing": {"prompt": "0.000001", "completion": "0.000002"},
			}
		},
	)
	monkeypatch.setattr(app_module, "_selectors_interactive", lambda: True)

	effort, cost_hint = app_module._reasoning_control_updates("alpha/one")

	assert effort["visible"] is True
	assert effort["choices"] == [
		"none",
		"minimal",
		"low",
		"medium",
		"high",
		"xhigh",
		"max",
	]
	assert effort["value"] == "medium"
	assert cost_hint["visible"] is True
	assert cost_hint["value"] == (
		"Arena defaults to medium because the model supports it and declares no usable effort "
		"default. Reasoning can increase latency, billed output-token usage, and cost. "
		"Estimated rates: input $1/M; output $2/M."
	)


def test_reasoning_control_updates_show_effort_dropdown_only(monkeypatch) -> None:
	monkeypatch.setattr(
		app_module,
		"MODEL_LOOKUP",
		{
			"alpha/one": {
				"supported_parameters": ["reasoning.effort", "reasoning.max_tokens"],
				"default_parameters": {"reasoning_effort": "high"},
				"top_provider": {"max_completion_tokens": 8192},
				"pricing": {"internal_reasoning": "0.000003"},
			}
		},
	)
	monkeypatch.setattr(app_module, "_selectors_interactive", lambda: True)

	effort, cost_hint = app_module._reasoning_control_updates("alpha/one")

	assert effort["visible"] is True
	assert effort["choices"][-1] == "max"
	assert effort["value"] == "high"
	assert cost_hint["visible"] is True
	assert cost_hint["value"] == (
		"Uses the model-declared high effort by default. Reasoning can increase latency, billed "
		"output-token usage, and cost. Estimated rates: reasoning $3/M."
	)


def test_reasoning_control_updates_hide_budget_only_models(monkeypatch) -> None:
	monkeypatch.setattr(
		app_module,
		"MODEL_LOOKUP",
		{
			"budget/model": {
				"id": "unknown/reasoner",
				"supported_parameters": ["reasoning.max_tokens"],
				"top_provider": {"max_completion_tokens": 4096},
			},
			"effort/model": {"supported_parameters": ["reasoning"]},
		},
	)

	effort, cost_hint = app_module._reasoning_control_updates("budget/model")

	assert effort["visible"] is False
	assert cost_hint["visible"] is True
	assert cost_hint["value"] == (
		"No effort selector is available, so Arena omits reasoning settings and defers to "
		"OpenRouter/provider defaults."
	)


def test_reasoning_control_updates_honor_modern_mandatory_metadata(monkeypatch) -> None:
	monkeypatch.setattr(
		app_module,
		"MODEL_LOOKUP",
		{
			"alpha/one": {
				"reasoning": {
					"supported_efforts": ["none", "high", "medium"],
					"default_effort": "high",
					"mandatory": True,
				}
			}
		},
	)
	monkeypatch.setattr(app_module, "_selectors_interactive", lambda: True)

	effort, hint = app_module._reasoning_control_updates("alpha/one")

	assert effort["visible"] is True
	assert effort["choices"] == ["high", "medium"]
	assert effort["value"] == "high"
	assert "required" in hint["value"]
	assert "cannot be disabled" in hint["value"]


def test_reasoning_control_updates_hide_modern_model_without_efforts(monkeypatch) -> None:
	monkeypatch.setattr(
		app_module,
		"MODEL_LOOKUP",
		{"alpha/one": {"reasoning": {"default_enabled": True}}},
	)

	effort, hint = app_module._reasoning_control_updates("alpha/one")

	assert effort["visible"] is False
	assert effort["choices"] == []
	assert effort["value"] is None
	assert hint["visible"] is True
	assert "omits reasoning settings" in hint["value"]


def test_clear_histories_clears_user_input_and_resets_outputs(monkeypatch) -> None:
	reset_outputs = (
		"chat-1",
		"chat-2",
		"chat-3",
		"round-state",
		"vote-a",
		"vote-b",
		"vote-c",
		"vote-reset",
		"vote-submit",
	)
	monkeypatch.setattr(app_module, "_reset_arena_outputs", lambda: reset_outputs)

	assert app_module.clear_histories("alpha/one", "beta/two", "gamma/three") == (
		"",
		*reset_outputs,
	)


def test_submit_vote_outputs_uses_round_histories_and_leaderboard(monkeypatch) -> None:
	captured: dict[str, object] = {}
	round_state = _build_completed_round_state(
		first_choice="Response A",
		second_choice="Response B",
		third_choice="Response C",
		created_at="2026-05-09T10:00:00Z",
		submitted_at="2026-05-09T10:01:00Z",
	)
	monkeypatch.setattr(
		app_module,
		"_chatbot_updates",
		lambda histories, display_order, current_state: (
			captured.update(
				{
					"histories": histories,
					"display_order": display_order,
					"round_state": current_state,
				}
			)
			or ("panel-1", "panel-2", "panel-3")
		),
	)
	monkeypatch.setattr(
		app_module,
		"_vote_ui_updates",
		lambda current_state: ("vote-a", "vote-b", "vote-c", "vote-reset", "vote-submit"),
	)
	monkeypatch.setattr(
		app_module,
		"_leaderboard_view_data",
		lambda: ("## Leaderboard\n\nTwo rounds", [[1, "Alpha", "alpha", 1, 2, "50%"]]),
	)

	outputs = app_module._submit_vote_outputs(round_state)

	assert outputs[0] == round_state
	assert outputs[1:4] == ("panel-1", "panel-2", "panel-3")
	assert outputs[4:9] == ("vote-a", "vote-b", "vote-c", "vote-reset", "vote-submit")
	assert outputs[9] == "## Leaderboard\n\nTwo rounds"
	assert outputs[10] == [[1, "Alpha", "alpha", 1, 2, "50%"]]
	assert captured["display_order"] == [2, 0, 1]
	assert captured["round_state"] == round_state
	assert captured["histories"][0][0]["content"] == "Alpha answer"
	assert captured["histories"][2][0]["content"] == "Gamma answer"


def test_leaderboard_view_data_aggregates_multiple_rounds(monkeypatch, tmp_path) -> None:
	_patch_log_paths(monkeypatch, tmp_path)
	round_state_one = _build_completed_round_state(
		first_choice="Response A",
		second_choice="Response B",
		third_choice="Response C",
		created_at="2026-05-09T10:00:00Z",
		submitted_at="2026-05-09T10:01:00Z",
		display_order=[0, 1, 2],
	)
	round_state_two = _build_completed_round_state(
		first_choice="Response C",
		second_choice="Response A",
		third_choice="Response B",
		created_at="2026-05-09T11:00:00Z",
		submitted_at="2026-05-09T11:01:00Z",
		display_order=[0, 1, 2],
	)

	app_module._update_meta_log(round_state_one, "arena_logs/sessions/round-1")
	app_module._update_meta_log(round_state_two, "arena_logs/sessions/round-2")

	summary, rows = app_module._leaderboard_view_data()
	meta_log = app_module._read_json_file(app_module.META_LOG_FILE, dict, {})

	assert (
		summary
		== "## Leaderboard\n\n2 submitted rounds across 3 ranked models. Sorted by total wins."
	)
	assert rows == [
		[1, "Label for alpha/one", "alpha", 1, 2, "50%"],
		[2, "Label for gamma/three", "gamma", 1, 2, "50%"],
		[3, "Label for beta/two", "beta", 0, 2, "0%"],
	]
	assert meta_log["total_rounds"] == 2
	assert len(meta_log["round_summaries"]) == 2
