import time

def gemAibenchmark(provider, model, output, start_time):
    duration = time.perf_counter() - start_time

    return {
        "provider": provider,
        "model": model,
        "latency": round(duration, 3),
        "output": output if isinstance(output, dict) else {}
    }