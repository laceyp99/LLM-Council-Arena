from arena import app as app_module


def _sample_round_state() -> dict[str, object]:
	return {
		"round_id": "round-1",
		"created_at": "2026-05-09T10:00:00Z",
		"generation_completed_at": "2026-05-09T10:00:30Z",
		"submitted_at": "2026-05-09T10:01:00Z",
		"user_text": "Compare these models.",
		"prompt_sha256": "abc123",
		"system_prompt": "System prompt",
		"messages_payload": [{"role": "user", "content": "Compare these models."}],
		"display_order": [2, 0, 1],
		"completed_slots": [0, 2],
		"errored_slots": [1],
		"first_choice": "Response A",
		"second_choice": "Response B",
		"third_choice": "Response C",
		"slot_logs": [
			{
				"selection_slot": 0,
				"response_label": "Response B",
				"model_id": "alpha/one",
				"model_label": "Alpha One",
				"provider_key": "alpha",
				"status": "complete",
				"error": None,
				"final_response": "Answer one",
				"reasoning_trace": "Thought one",
				"reasoning_details": [{"type": "reasoning.text", "text": "Thought one"}],
				"usage": {"completion_tokens": 11},
				"stats": {"total_generation_time": 1.5},
				"finish_reason": "stop",
				"cost": 0.001,
				"completion_tokens": 11,
				"reasoning_tokens": 4,
				"message_history": [{"role": "assistant", "content": "Answer one"}],
			},
			{
				"selection_slot": 1,
				"response_label": "Response C",
				"model_id": "beta/two",
				"model_label": "Beta Two",
				"provider_key": "beta",
				"status": "error",
				"error": "timeout",
				"final_response": "",
				"reasoning_trace": "",
				"reasoning_details": [],
				"usage": {},
				"stats": {},
				"finish_reason": None,
				"cost": None,
				"completion_tokens": None,
				"reasoning_tokens": None,
				"message_history": [],
			},
			{
				"selection_slot": 2,
				"response_label": "Response A",
				"model_id": "gamma/three",
				"model_label": "Gamma Three",
				"provider_key": "gamma",
				"status": "complete",
				"error": None,
				"final_response": "Answer three",
				"reasoning_trace": "Thought three",
				"reasoning_details": [{"type": "reasoning.summary", "summary": "Summary"}],
				"usage": {"completion_tokens": 22},
				"stats": {"total_generation_time": 2.5},
				"finish_reason": "stop",
				"cost": 0.002,
				"completion_tokens": 22,
				"reasoning_tokens": 6,
				"message_history": [{"role": "assistant", "content": "Answer three"}],
			},
		],
	}


def test_build_messages_includes_system_and_user_content() -> None:
	messages = app_module._build_messages("User prompt", "")

	assert messages == [
		{"role": "system", "content": app_module.DEFAULT_SYSTEM_PROMPT},
		{"role": "user", "content": "User prompt"},
	]


def test_build_round_payload_shapes_round_metadata() -> None:
	round_payload = app_module._build_round_payload(_sample_round_state())

	assert round_payload["round_id"] == "round-1"
	assert round_payload["prompt"]["text"] == "Compare these models."
	assert round_payload["display_mapping"][0]["response_label"] == "Response A"
	assert round_payload["completed_slots"] == [0, 2]
	assert round_payload["errored_slots"] == [1]


def test_build_histories_payload_preserves_message_history_by_panel() -> None:
	histories_payload = app_module._build_histories_payload(_sample_round_state())

	assert histories_payload["round_id"] == "round-1"
	assert histories_payload["panels"][0]["model_id"] == "alpha/one"
	assert histories_payload["panels"][2]["message_history"][0]["content"] == "Answer three"


def test_build_generation_payload_includes_generation_stats_and_usage() -> None:
	generation_payload = app_module._build_generation_payload(_sample_round_state())

	assert generation_payload["round_id"] == "round-1"
	assert generation_payload["panels"][0]["status"] == "complete"
	assert generation_payload["panels"][0]["completion_tokens"] == 11
	assert generation_payload["panels"][2]["reasoning_details"][0]["summary"] == "Summary"


def test_update_meta_log_aggregates_model_totals_and_round_summary(monkeypatch, tmp_path) -> None:
	meta_log_file = tmp_path / "meta.json"
	logs_dir = tmp_path / "arena_logs"
	session_logs_dir = logs_dir / "sessions"

	monkeypatch.setattr(app_module, "META_LOG_FILE", meta_log_file)
	monkeypatch.setattr(app_module, "LOGS_DIR", logs_dir)
	monkeypatch.setattr(app_module, "SESSION_LOGS_DIR", session_logs_dir)

	round_state = _sample_round_state()
	app_module._update_meta_log(round_state, "arena_logs/sessions/round-1")

	meta_log = app_module._read_json_file(meta_log_file, dict, {})

	assert meta_log["total_rounds"] == 1
	assert meta_log["model_totals"]["gamma/three"]["wins"] == 1
	assert meta_log["model_totals"]["beta/two"]["error_count"] == 1
	assert meta_log["round_summaries"][0]["session_dir"] == "arena_logs/sessions/round-1"
