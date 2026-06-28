import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from arena import app as app_module


def test_timestamp_slug_normalizes_valid_timestamp() -> None:
	assert app_module._timestamp_slug("2026-05-09T10:11:12+02:00") == "20260509T081112Z"


def test_write_and_read_json_file_roundtrip(tmp_path: Path) -> None:
	json_file = tmp_path / "round.json"
	payload = {"round_id": "abc123", "count": 2}

	app_module._write_json_file(json_file, payload)

	assert app_module._read_json_file(json_file, dict, {}) == payload
	assert json.loads(json_file.read_text(encoding="utf-8")) == payload


def test_write_json_file_keeps_existing_file_valid_when_replace_fails(
	monkeypatch,
	tmp_path: Path,
) -> None:
	json_file = tmp_path / "round.json"
	original_payload = {"round_id": "existing", "count": 1}
	app_module._write_json_file(json_file, original_payload)

	def fail_replace(source, destination) -> None:
		raise OSError("replace failed")

	monkeypatch.setattr(app_module.os, "replace", fail_replace)

	try:
		app_module._write_json_file(json_file, {"round_id": "new", "count": 2})
		assert False, "Expected OSError for failed atomic replacement"
	except OSError as exc:
		assert "replace failed" in str(exc)

	assert app_module._read_json_file(json_file, dict, {}) == original_payload
	assert list(tmp_path.glob(".round.json.*.tmp")) == []


def test_write_json_file_keeps_replaced_file_when_directory_sync_fails(
	caplog,
	monkeypatch,
	tmp_path: Path,
) -> None:
	json_file = tmp_path / "round.json"
	new_payload = {"round_id": "new", "count": 2}

	def fail_parent_sync(path) -> None:
		raise OSError("directory sync failed")

	monkeypatch.setattr(app_module, "_fsync_parent_directory", fail_parent_sync)

	with caplog.at_level(logging.WARNING, logger=app_module.LOGGER.name):
		app_module._write_json_file(json_file, new_payload)

	assert app_module._read_json_file(json_file, dict, {}) == new_payload
	assert "was replaced, but syncing the parent directory failed" in caplog.text
	assert list(tmp_path.glob(".round.json.*.tmp")) == []


def test_write_json_file_warns_when_directory_open_fails_after_replace(
	caplog,
	monkeypatch,
	tmp_path: Path,
) -> None:
	json_file = tmp_path / "round.json"
	new_payload = {"round_id": "new", "count": 2}
	original_open = app_module.os.open

	monkeypatch.setattr(app_module.os, "name", "posix")

	def fail_open(path, flags, mode=0o777, *, dir_fd=None) -> int:
		if str(path) == str(json_file.parent):
			raise OSError("directory open failed")
		if dir_fd is None:
			return original_open(path, flags, mode)
		return original_open(path, flags, mode, dir_fd=dir_fd)

	monkeypatch.setattr(app_module.os, "open", fail_open)

	with caplog.at_level(logging.WARNING, logger=app_module.LOGGER.name):
		app_module._write_json_file(json_file, new_payload)

	assert app_module._read_json_file(json_file, dict, {}) == new_payload
	assert "directory open failed" in caplog.text
	assert list(tmp_path.glob(".round.json.*.tmp")) == []


def test_read_json_file_raises_for_invalid_json_and_wrong_structure(tmp_path: Path) -> None:
	invalid_file = tmp_path / "invalid.json"
	invalid_file.write_text("{not valid json}", encoding="utf-8")

	try:
		app_module._read_json_file(invalid_file, dict, {})
		assert False, "Expected RuntimeError for invalid JSON"
	except RuntimeError as exc:
		assert "is not valid JSON" in str(exc)

	wrong_shape_file = tmp_path / "wrong-shape.json"
	wrong_shape_file.write_text("[]\n", encoding="utf-8")

	try:
		app_module._read_json_file(wrong_shape_file, dict, {})
		assert False, "Expected RuntimeError for unexpected structure"
	except RuntimeError as exc:
		assert "unexpected JSON structure" in str(exc)


def test_ensure_log_store_and_append_vote_record_use_configured_paths(
	monkeypatch,
	tmp_path: Path,
) -> None:
	logs_dir = tmp_path / "arena_logs"
	session_logs_dir = logs_dir / "sessions"
	meta_log_file = logs_dir / "meta.json"
	votes_file = tmp_path / "votes.json"

	assert not logs_dir.exists()
	assert not session_logs_dir.exists()
	assert not meta_log_file.exists()
	assert not votes_file.exists()

	monkeypatch.setattr(app_module, "LOGS_DIR", logs_dir)
	monkeypatch.setattr(app_module, "SESSION_LOGS_DIR", session_logs_dir)
	monkeypatch.setattr(app_module, "META_LOG_FILE", meta_log_file)
	monkeypatch.setattr(app_module, "VOTES_FILE", votes_file)

	app_module._ensure_log_store()
	app_module._append_vote_record({"round_id": "round-1"})

	assert session_logs_dir.exists()
	assert app_module._read_json_file(meta_log_file, dict, {})["schema_version"] == 1
	assert app_module._read_json_file(votes_file, list, []) == [{"round_id": "round-1"}]


def test_append_vote_record_preserves_concurrent_records(monkeypatch, tmp_path: Path) -> None:
	votes_file = tmp_path / "votes.json"
	monkeypatch.setattr(app_module, "VOTES_FILE", votes_file)

	def append_record(index: int) -> None:
		app_module._append_vote_record({"round_id": f"round-{index}"})

	with ThreadPoolExecutor(max_workers=8) as executor:
		list(executor.map(append_record, range(40)))

	votes = app_module._read_json_file(votes_file, list, [])
	round_ids = {record["round_id"] for record in votes}

	assert len(votes) == 40
	assert round_ids == {f"round-{index}" for index in range(40)}


def test_build_vote_record_uses_round_state_mapping_and_rankings() -> None:
	round_state = {
		"submitted_at": "2026-05-09T08:11:12Z",
		"round_id": "round-1",
		"user_text": "Compare",
		"prompt_sha256": "hash",
		"system_prompt": "System",
		"display_order": [2, 0, 1],
		"slot_logs": [
			{
				"selection_slot": 0,
				"response_label": "Response B",
				"model_id": "alpha/one",
				"model_label": "Alpha One",
				"provider_key": "alpha",
			},
			{
				"selection_slot": 1,
				"response_label": "Response C",
				"model_id": "beta/two",
				"model_label": "Beta Two",
				"provider_key": "beta",
			},
			{
				"selection_slot": 2,
				"response_label": "Response A",
				"model_id": "gamma/three",
				"model_label": "Gamma Three",
				"provider_key": "gamma",
			},
		],
		"first_choice": "Response A",
		"second_choice": "Response B",
		"third_choice": "Response C",
	}

	vote_record = app_module._build_vote_record(
		round_state, session_dir="arena_logs/sessions/round-1"
	)

	assert vote_record["round_id"] == "round-1"
	assert vote_record["selected_models"][0]["model_id"] == "alpha/one"
	assert vote_record["display_order"][0]["response_label"] == "Response A"
	assert vote_record["ranking"][0]["response_label"] == "Response A"
	assert vote_record["session_dir"] == "arena_logs/sessions/round-1"
