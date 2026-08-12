from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ModelReview:
    relevant: bool
    confidence: int
    category: str
    reason: str
    model: str


def enabled() -> bool:
    return os.getenv("LOCAL_MODEL_ENABLED", "1").strip().casefold() not in {"0", "false", "no", "off"}


def _profile(rule_score: int, fields_missing: bool) -> tuple[str, str, str]:
    dispatcher = os.getenv("LOCAL_MODEL_DISPATCHER_ENDPOINT", "http://127.0.0.1:8083").strip()
    if rule_score < 60 and not fields_missing:
        return "local-llm-quick.service", dispatcher or os.getenv("LOCAL_MODEL_QUICK_ENDPOINT", "http://127.0.0.1:8081"), "qwen3-0.6b"
    return "local-llm-summary.service", dispatcher or os.getenv("LOCAL_MODEL_SUMMARY_ENDPOINT", "http://127.0.0.1:8082"), "qwen3-1.7b"


def _wait_ready(endpoint: str, seconds: int = 90) -> bool:
    for _ in range(seconds):
        try:
            with urlopen(f"{endpoint}/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def _ensure_service(service: str, endpoint: str) -> bool:
    try:
        with urlopen(f"{endpoint}/health", timeout=1):
            return True
    except Exception:
        pass
    if endpoint == os.getenv("LOCAL_MODEL_DISPATCHER_ENDPOINT", "http://127.0.0.1:8083").strip():
        return False
    if os.name != "posix" or not os.path.exists("/run/systemd/system"):
        return False
    completed = subprocess.run(["sudo", "-n", "systemctl", "start", "--no-block", service], capture_output=True, timeout=10, check=False)
    return completed.returncode == 0 and _wait_ready(endpoint)


def _json_object(value: str) -> dict:
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model response is not JSON")
    result = json.loads(value[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("model response is not an object")
    return result


def review_candidate(text: str, rule_score: int, fields_missing: bool = False) -> ModelReview | None:
    """Review only uncertain candidates; any failure safely falls back to rules."""
    if not enabled() or rule_score < 20 or rule_score >= 80:
        return None
    service, endpoint, model = _profile(rule_score, fields_missing)
    if not _ensure_service(service, endpoint):
        return None
    prompt = (
        "你是国网招标附件复核器。判断内容是否属于信息化、数字化、软件开发实施、"
        "信息系统运维或IT人力外包。只依据原文，不得补造编号、金额或事实。"
        "只输出JSON：{\"relevant\":true,\"confidence\":0-100,"
        "\"category\":\"类别\",\"reason\":\"不超过80字且引用原文依据\"}。\n原文："
        + text[:5000]
    )
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 180,
        "response_format": {"type": "json_object"},
    }, ensure_ascii=False).encode("utf-8")
    try:
        request = Request(f"{endpoint}/v1/chat/completions", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=int(os.getenv("LOCAL_MODEL_TIMEOUT", "90"))) as response:
            body = json.loads(response.read().decode("utf-8"))
        result = _json_object(body["choices"][0]["message"]["content"])
        confidence = max(0, min(int(result.get("confidence", 0)), 100))
        reason = " ".join(str(result.get("reason", "")).split())[:160]
        category = " ".join(str(result.get("category", "")).split())[:60]
        relevant_value = result.get("relevant", False)
        relevant = relevant_value if isinstance(relevant_value, bool) else str(relevant_value).strip().casefold() == "true"
        return ModelReview(relevant, confidence, category, reason, model)
    except (KeyError, TypeError, ValueError, TimeoutError, URLError, json.JSONDecodeError):
        return None


def combined_score(rule_score: int, review: ModelReview | None) -> int:
    if not review or review.confidence < 60:
        return rule_score
    model_score = review.confidence if review.relevant else 100 - review.confidence
    # Rules remain authoritative; the model can move a borderline result but
    # cannot turn weak evidence into a near-certain match.
    return max(0, min(100, round(rule_score * 0.65 + model_score * 0.35)))
