import time
import json
from app.pricing import estimate_cost


def safe_json(output):
    try:
        return json.loads(output)
    except:
        return {"raw": output}


def benchmark(provider, model, output, start_time, usage=None):
    duration = time.perf_counter() - start_time

    input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

    cost = estimate_cost(provider, input_tokens, output_tokens)

    return {
        "provider": provider,
        "model": model,
        "time_sec": round(duration, 3),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
        "raw_output": safe_json(output)
    }
