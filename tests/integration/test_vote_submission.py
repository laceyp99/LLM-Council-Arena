from datetime import datetime, timezone

import gradio as gr

from arena import app as app_module


def _stub_chatbot_updates(monkeypatch) -> None:
	monkeypatch.setattr(
		app_module,
		"_chatbot_updates",
		lambda *args, **kwargs: ("panel-1", "panel-2", "panel-3"),
	)


def _patch_log_paths(monkeypatch, tmp_path) -> None:
	logs_dir = tmp_path / "arena_logs"
	session_logs_dir = logs_dir / "sessions"
	monkeypatch.setattr(app_module, "APP_DIR", tmp_path)
	monkeypatch.setattr(app_module, "LOGS_DIR", logs_dir)
	monkeypatch.setattr(app_module, "SESSION_LOGS_DIR", session_logs_dir)
	monkeypatch.setattr(app_module, "META_LOG_FILE", logs_dir / "meta.json")
	monkeypatch.setattr(app_module, "VOTES_FILE", tmp_path / "votes.json")


def _build_votable_round_state() -> dict[str, object]:
	round_state = app_module._build_round_state(
		user_text="Compare these models.",
		system_prompt="System prompt",
		message_payload=app_module._build_messages("Compare these models.", "System prompt"),
		model_ids=["alpha/one", "beta/two", "gamma/three"],
		display_order=[0, 1, 2],
		chatbot_label=lambda model_id: f"Label for {model_id}",
		provider_for_model=lambda model_id: model_id.split("/", 1)[0],
	)
	round_state["generation_completed_at"] = datetime.now(timezone.utc).isoformat()
	round_state["completed_slots"] = [0, 1, 2]
	round_state["ready_for_vote"] = True
	round_state["vote_stage"] = "ready_submit"
	round_state["first_choice"] = "Response A"
	round_state["second_choice"] = "Response B"
	round_state["third_choice"] = "Response C"

	for slot, answer in enumerate(["Alpha answer", "Beta answer", "Gamma answer"]):
		round_state["slot_logs"][slot]["status"] = "complete"
		round_state["slot_logs"][slot]["final_response"] = answer
		round_state["slot_logs"][slot]["message_history"] = [
			{"role": "assistant", "content": answer}
		]

	return round_state


def test_submit_vote_returns_current_state_when_vote_is_incomplete(monkeypatch, tmp_path) -> None:
	_stub_chatbot_updates(monkeypatch)
	_patch_log_paths(monkeypatch, tmp_path)
	round_state = _build_votable_round_state()
	round_state["third_choice"] = None

	outputs = app_module.submit_vote(round_state)

	assert outputs[0] == round_state
	assert not app_module.VOTES_FILE.exists()
	assert not app_module.SESSION_LOGS_DIR.exists()


def test_submit_vote_writes_round_artifacts_and_vote_record(monkeypatch, tmp_path) -> None:
	_stub_chatbot_updates(monkeypatch)
	_patch_log_paths(monkeypatch, tmp_path)
	round_state = _build_votable_round_state()

	outputs = app_module.submit_vote(round_state)
	submitted_state = outputs[0]
	status_update = outputs[9]
	votes = app_module._read_json_file(app_module.VOTES_FILE, list, [])
	session_dir = tmp_path / str(submitted_state["session_dir"])
	meta_log = app_module._read_json_file(app_module.META_LOG_FILE, dict, {})

	assert submitted_state["submitted"] is True
	assert submitted_state["vote_stage"] == "submitted"
	assert submitted_state["session_dir"] is not None
	assert submitted_state["submission_status"] == "success"
	assert submitted_state["submission_message"] == "Vote submitted and saved successfully."
	assert votes[0]["round_id"] == submitted_state["round_id"]
	assert session_dir.joinpath("round.json").exists()
	assert session_dir.joinpath("vote.json").exists()
	assert session_dir.joinpath("histories.json").exists()
	assert session_dir.joinpath("generation.json").exists()
	assert isinstance(status_update, gr.HTML)
	assert status_update.visible is True
	assert "Vote submitted and saved successfully." in status_update.value
	assert "background: #dcfce7" in status_update.value
	assert "color: #14532d" in status_update.value
	assert "text-align: center" in status_update.value
	assert outputs[10].startswith("## Leaderboard")
	assert outputs[11][0] == [1, "Label for alpha/one", "alpha", 1, 1, "100%"]
	assert len(outputs[11]) == 3
	assert meta_log["total_rounds"] == 1


def test_submit_vote_cleans_up_partial_session_artifacts_when_meta_update_fails(
	monkeypatch, tmp_path
) -> None:
	_stub_chatbot_updates(monkeypatch)
	_patch_log_paths(monkeypatch, tmp_path)
	round_state = _build_votable_round_state()
	written_paths: list[object] = []
	original_write_json_file = app_module._write_json_file

	def tracking_write_json_file(path, payload) -> None:
		written_paths.append(path)
		original_write_json_file(path, payload)

	monkeypatch.setattr(app_module, "_write_json_file", tracking_write_json_file)
	monkeypatch.setattr(
		app_module,
		"_update_meta_log",
		lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("meta store unavailable")),
	)

	outputs = app_module.submit_vote(round_state)
	submitted_state = outputs[0]
	status_update = outputs[9]
	session_file_paths = [
		path for path in written_paths if app_module.SESSION_LOGS_DIR in path.parents
	]
	session_dirs = {path.parent for path in session_file_paths}
	session_dir = session_dirs.pop()

	assert submitted_state["submitted"] is False
	assert submitted_state["vote_stage"] == "ready_submit"
	assert submitted_state["first_choice"] == "Response A"
	assert submitted_state["second_choice"] == "Response B"
	assert submitted_state["third_choice"] == "Response C"
	assert submitted_state["session_dir"] is None
	assert submitted_state["submission_status"] == "error"
	assert "meta store unavailable" in submitted_state["submission_message"]
	assert isinstance(status_update, gr.HTML)
	assert status_update.visible is True
	assert "meta store unavailable" in status_update.value
	assert "background: #fee2e2" in status_update.value
	assert "color: #7f1d1d" in status_update.value
	assert not app_module.VOTES_FILE.exists()
	assert app_module._read_json_file(app_module.META_LOG_FILE, dict, {})["total_rounds"] == 0
	assert sorted(path.name for path in session_file_paths) == [
		"generation.json",
		"histories.json",
		"round.json",
		"vote.json",
	]
	assert not session_dir.exists()
	assert not any(path.exists() for path in session_file_paths)


def test_submit_vote_records_log_warning_when_vote_append_fails(monkeypatch, tmp_path) -> None:
	_stub_chatbot_updates(monkeypatch)
	_patch_log_paths(monkeypatch, tmp_path)
	round_state = _build_votable_round_state()
	original_append_vote_record = app_module._append_vote_record
	append_attempts = 0

	def fail_once_append_vote_record(record) -> None:
		nonlocal append_attempts
		append_attempts += 1
		if append_attempts == 1:
			raise OSError("disk full")
		original_append_vote_record(record)

	monkeypatch.setattr(app_module, "_append_vote_record", fail_once_append_vote_record)

	outputs = app_module.submit_vote(round_state)
	submitted_state = outputs[0]
	status_update = outputs[9]

	assert submitted_state["submitted"] is False
	assert submitted_state["vote_stage"] == "ready_submit"
	assert submitted_state["first_choice"] == "Response A"
	assert submitted_state["second_choice"] == "Response B"
	assert submitted_state["third_choice"] == "Response C"
	assert submitted_state["session_dir"] is None
	assert submitted_state["log_warning"] == "disk full"
	assert submitted_state["submission_status"] == "error"
	assert "disk full" in submitted_state["submission_message"]
	assert isinstance(status_update, gr.HTML)
	assert status_update.visible is True
	assert "disk full" in status_update.value
	assert "background: #fee2e2" in status_update.value
	assert "color: #7f1d1d" in status_update.value
	assert not app_module.VOTES_FILE.exists()
	assert list(app_module.SESSION_LOGS_DIR.glob("*")) == []
	assert app_module._read_json_file(app_module.META_LOG_FILE, dict, {})["total_rounds"] == 0

	retry_outputs = app_module.submit_vote(submitted_state)
	retry_state = retry_outputs[0]
	votes = app_module._read_json_file(app_module.VOTES_FILE, list, [])
	meta_log = app_module._read_json_file(app_module.META_LOG_FILE, dict, {})

	assert retry_state["submitted"] is True
	assert retry_state["submission_status"] == "success"
	assert retry_state["session_dir"] is not None
	assert votes[0]["round_id"] == retry_state["round_id"]
	assert meta_log["total_rounds"] == 1
	assert (tmp_path / str(retry_state["session_dir"]) / "round.json").exists()


def test_submit_vote_rolls_back_vote_record_when_round_log_write_fails(
	monkeypatch, tmp_path
) -> None:
	_stub_chatbot_updates(monkeypatch)
	_patch_log_paths(monkeypatch, tmp_path)
	round_state = _build_votable_round_state()
	monkeypatch.setattr(
		app_module,
		"_write_round_session_artifacts",
		lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("log store unavailable")),
	)

	outputs = app_module.submit_vote(round_state)
	status_update = outputs[9]

	assert outputs[0]["submitted"] is False
	assert outputs[0]["vote_stage"] == "ready_submit"
	assert outputs[0]["first_choice"] == "Response A"
	assert outputs[0]["second_choice"] == "Response B"
	assert outputs[0]["third_choice"] == "Response C"
	assert outputs[0]["session_dir"] is None
	assert outputs[0]["submission_status"] == "error"
	assert "log store unavailable" in outputs[0]["submission_message"]
	assert isinstance(status_update, gr.HTML)
	assert status_update.visible is True
	assert "log store unavailable" in status_update.value
	assert "background: #fee2e2" in status_update.value
	assert "color: #7f1d1d" in status_update.value
	assert not app_module.VOTES_FILE.exists()
	assert list(app_module.SESSION_LOGS_DIR.glob("*")) == []
	assert app_module._read_json_file(app_module.META_LOG_FILE, dict, {})["total_rounds"] == 0


def test_submit_vote_returns_existing_submission_without_rewriting(monkeypatch, tmp_path) -> None:
	_stub_chatbot_updates(monkeypatch)
	_patch_log_paths(monkeypatch, tmp_path)
	round_state = _build_votable_round_state()
	round_state["submitted"] = True
	round_state["submitted_at"] = "2026-05-09T10:00:00Z"

	outputs = app_module.submit_vote(round_state)

	assert outputs[0] == round_state
	assert not app_module.VOTES_FILE.exists()
