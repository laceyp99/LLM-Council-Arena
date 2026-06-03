import asyncio
import json
import os
import time
from typing import Any, AsyncGenerator

import httpx
from dotenv import load_dotenv

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _to_float(value: Any) -> float | None:
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _normalize_reasoning_details(value: Any) -> list[dict[str, Any]]:
	if isinstance(value, dict):
		return [dict(value)]
	if isinstance(value, list):
		return [dict(item) for item in value if isinstance(item, dict)]
	return []


def _reasoning_detail_key(detail: dict[str, Any]) -> tuple[Any, Any, Any] | None:
	detail_id = detail.get("id")
	detail_index = detail.get("index")
	detail_type = detail.get("type")
	if detail_id is None and detail_index is None:
		return None
	return detail_id, detail_index, detail_type


def _merge_reasoning_field(existing_value: Any, incoming_value: Any) -> Any:
	if not isinstance(incoming_value, str) or not incoming_value:
		return existing_value
	if not isinstance(existing_value, str) or not existing_value:
		return incoming_value
	if incoming_value.startswith(existing_value):
		return incoming_value
	if existing_value.endswith(incoming_value):
		return existing_value
	return f"{existing_value}{incoming_value}"


def _merge_reasoning_details(
	existing: list[dict[str, Any]],
	incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
	merged = [dict(detail) for detail in existing]
	positions: dict[tuple[Any, Any, Any], int] = {}

	for index, detail in enumerate(merged):
		detail_key = _reasoning_detail_key(detail)
		if detail_key is not None:
			positions[detail_key] = index

	for detail in incoming:
		normalized_detail = dict(detail)
		detail_key = _reasoning_detail_key(normalized_detail)

		if detail_key is None or detail_key not in positions:
			merged.append(normalized_detail)
			if detail_key is not None:
				positions[detail_key] = len(merged) - 1
			continue

		current = merged[positions[detail_key]]
		for field_name in ("text", "summary", "data"):
			current[field_name] = _merge_reasoning_field(
				current.get(field_name),
				normalized_detail.get(field_name),
			)

		for field_name, value in normalized_detail.items():
			if field_name in {"text", "summary", "data"}:
				continue
			if current.get(field_name) in (None, "") and value not in (None, ""):
				current[field_name] = value

	return merged


def _legacy_reasoning_details(delta: dict[str, Any], start_index: int) -> list[dict[str, Any]]:
	legacy_reasoning = delta.get("reasoning") or delta.get("reasoning_content")
	if not isinstance(legacy_reasoning, str) or not legacy_reasoning:
		return []

	return [
		{
			"type": "reasoning.text",
			"text": legacy_reasoning,
			"format": "unknown",
			"index": start_index,
		}
	]


class OpenRouterAPI:
	def __init__(self, api_key: str, site_url: str = "", site_name: str = ""):
		self.api_key = api_key
		self.base_url = OPENROUTER_BASE_URL
		self.headers = {
			"Authorization": f"Bearer {self.api_key}",
			"Content-Type": "application/json",
			"HTTP-Referer": site_url,
			"X-Title": site_name,
		}

	def get_models(self) -> list[dict]:
		"""Synchronously fetch available models from OpenRouter."""
		with httpx.Client() as client:
			response = client.get(
				f"{self.base_url}/models",
				headers=self.headers,
			)
			response.raise_for_status()
			return response.json().get("data", [])

	@staticmethod
	def normalize_model_catalog(models: list[dict]) -> list[dict]:
		"""Return text-capable models grouped by provider-friendly metadata."""
		normalized: list[dict] = []

		for model in models:
			model_id = model.get("id")
			if not isinstance(model_id, str) or "/" not in model_id:
				continue

			architecture = model.get("architecture") or {}
			input_modalities = architecture.get("input_modalities") or []
			output_modalities = architecture.get("output_modalities") or []

			if input_modalities and "text" not in input_modalities:
				continue
			if output_modalities and "text" not in output_modalities:
				continue

			provider_key, slug = model_id.split("/", 1)
			full_name = str(model.get("name") or model_id).strip()

			if ":" in full_name:
				provider_label, model_label = [part.strip() for part in full_name.split(":", 1)]
			else:
				provider_label = provider_key.replace("-", " ").title()
				model_label = slug.replace("-", " ").replace("_", " ").strip()

			normalized.append(
				{
					"model_id": model_id,
					"provider_key": provider_key,
					"provider_label": provider_label,
					"model_label": model_label or slug,
					"full_label": full_name,
				}
			)

		normalized.sort(
			key=lambda entry: (
				entry["provider_label"].lower(),
				entry["model_label"].lower(),
				entry["model_id"].lower(),
			)
		)
		return normalized

	def get_normalized_text_models(self) -> list[dict]:
		"""Fetch and normalize the text-capable model catalog."""
		return self.normalize_model_catalog(self.get_models())

	@staticmethod
	def _normalize_prompt_requests(models: list[Any]) -> list[dict[str, Any]]:
		requests: list[dict[str, Any]] = []

		for index, entry in enumerate(models):
			if isinstance(entry, str):
				requests.append({"slot": index, "model": entry})
				continue

			if not isinstance(entry, dict):
				raise TypeError("Each model request must be a model id string or a dict.")

			model_id = entry.get("model")
			if not isinstance(model_id, str) or not model_id:
				raise ValueError("Each model request dict must include a non-empty 'model'.")

			requests.append(
				{
					**entry,
					"slot": entry.get("slot", index),
					"model": model_id,
				}
			)

		return requests

	async def _prompt_model(
		self,
		client: httpx.AsyncClient,
		request: dict[str, Any],
		messages: list[dict],
		**kwargs,
	) -> AsyncGenerator[dict, None]:
		"""Asynchronously prompt a single model and yield stream chunks."""
		model = request["model"]
		slot = request.get("slot")
		payload = {
			"model": model,
			"messages": messages,
			"stream": True,  # Enable streaming
			**kwargs,
		}
		request_started_at = time.perf_counter()
		first_token_at: float | None = None
		final_usage: dict[str, Any] | None = None
		finish_reason: str | None = None
		accumulated_reasoning_details: list[dict[str, Any]] = []
		provider_error_seen = False

		try:
			async with client.stream(
				"POST",
				f"{self.base_url}/chat/completions",
				headers=self.headers,
				json=payload,
			) as response:
				response.raise_for_status()

				async for line in response.aiter_lines():
					if not line:
						continue
					if line.startswith("data: "):
						data_str = line[len("data: ") :]
						if data_str == "[DONE]":
							break

						try:
							data = json.loads(data_str)
							error = data.get("error")
							if isinstance(error, dict):
								provider_error_seen = True
								yield {
									"event": "error",
									"slot": slot,
									"model": model,
									"error": error.get("message") or str(error),
									"response": data,
								}
								break

							usage = data.get("usage")
							if isinstance(usage, dict):
								final_usage = usage

							choices = data.get("choices") or []
							if not choices:
								continue

							choice = choices[0] or {}
							finish_reason = choice.get("finish_reason") or finish_reason
							delta = choice.get("delta") or {}
							reasoning_details = _normalize_reasoning_details(
								delta.get("reasoning_details")
							)
							if not reasoning_details:
								reasoning_details.extend(
									_legacy_reasoning_details(
										delta, len(accumulated_reasoning_details)
									)
								)

							if reasoning_details:
								accumulated_reasoning_details = _merge_reasoning_details(
									accumulated_reasoning_details,
									reasoning_details,
								)
								yield {
									"event": "reasoning",
									"slot": slot,
									"model": model,
									"reasoning_details": [
										dict(detail) for detail in accumulated_reasoning_details
									],
									"response": data,
								}

							content = delta.get("content")
							if content:
								if first_token_at is None:
									first_token_at = time.perf_counter()
								yield {
									"slot": slot,
									"model": model,
									"delta": content,
									"response": data,
								}
						except (json.JSONDecodeError, KeyError, IndexError):
							# Handle potential malformed JSON or unexpected structure
							continue

				if provider_error_seen:
					return

				completed_at = time.perf_counter()
				total_generation_time = completed_at - request_started_at
				time_to_first_token = None
				tokens_per_second = None

				if first_token_at is not None:
					time_to_first_token = first_token_at - request_started_at
					generation_window = completed_at - first_token_at
					completion_tokens = _to_float((final_usage or {}).get("completion_tokens"))
					if completion_tokens is not None and generation_window > 0:
						tokens_per_second = completion_tokens / generation_window

				yield {
					"event": "complete",
					"slot": slot,
					"model": model,
					"usage": final_usage,
					"reasoning_details": [dict(detail) for detail in accumulated_reasoning_details],
					"stats": {
						"time_to_first_token": time_to_first_token,
						"total_generation_time": total_generation_time,
						"tokens_per_second": tokens_per_second,
						"finish_reason": finish_reason,
					},
				}
		except Exception as e:
			yield {
				"slot": slot,
				"model": model,
				"error": str(e),
			}

	async def prompt_models_concurrent(
		self, models: list[Any], messages: list[dict], **kwargs
	) -> AsyncGenerator[dict, None]:
		"""
		Asynchronously prompt multiple models concurrently and yield
		individual chunks from their streams as they arrive.
		"""
		request_specs = self._normalize_prompt_requests(models)

		async with httpx.AsyncClient(timeout=60.0) as client:
			# Create a generator for each model's stream
			generators = [
				self._prompt_model(client, request_spec, messages, **kwargs)
				for request_spec in request_specs
			]

			# Use an async queue to collect chunks from all streams
			queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

			async def producer(gen):
				async for chunk in gen:
					await queue.put(chunk)

			# Start all streams as concurrent tasks
			tasks = [asyncio.create_task(producer(gen)) for gen in generators]

			try:
				# Keep draining queued chunks until all producers finish or one fails.
				while True:
					try:
						chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
						yield chunk
						queue.task_done()
					except asyncio.TimeoutError:
						pass

					finished_tasks = [task for task in tasks if task.done()]
					for task in finished_tasks:
						if task.cancelled():
							continue

						task_exception = task.exception()
						if task_exception is not None:
							for remaining_task in tasks:
								if not remaining_task.done():
									remaining_task.cancel()
							await asyncio.gather(*tasks, return_exceptions=True)
							raise task_exception

					tasks = [task for task in tasks if not task.done()]
					if not tasks and queue.empty():
						break
			finally:
				for task in tasks:
					if not task.done():
						task.cancel()
				if tasks:
					await asyncio.gather(*tasks, return_exceptions=True)


async def main():
	load_dotenv()
	api_key = os.getenv("OPENROUTER_API_KEY")
	api = OpenRouterAPI(
		api_key=api_key, site_url="http://localhost:6767", site_name="LLM Council Arena"
	)

	# Synchronously fetch models
	models = api.get_models()
	print(f"Available models: {len(models)}")

	# Select a few models to prompt
	selected_models = [
		"openai/gpt-5-nano",
		"anthropic/claude-haiku-4.5",
		"google/gemini-3-flash-preview",
	]

	messages = [
		{
			"role": "system",
			"content": "You are a professional comic with a specialty in creating only knock knock jokes.",
		},
		{"role": "user", "content": "What do you have for me?"},
	]

	# Prompt all models concurrently and process responses as they arrive
	print("\nStreaming responses...")
	async for result in api.prompt_models_concurrent(selected_models, messages):
		if "error" in result:
			print(f"\nError for model {result.get('model', 'unknown')}: {result['error']}")
		else:
			# Simple way to show chunks from different models
			model_name = result["model"]
			delta = result["delta"]
			print(f"[{model_name}] {delta}", end="", flush=True)

	print("\n\nAll streams complete.")


if __name__ == "__main__":
	asyncio.run(main())
