PRICING = {
    "gemini": {
        "input": 0.00035 / 1000,
        "output": 0.00105 / 1000
    },
    "openai": {
        "input": 0.005 / 1000,
        "output": 0.015 / 1000
    },
    "claude": {
        "input": 0.003 / 1000,
        "output": 0.015 / 1000
    }
}


def estimate_cost(provider, input_tokens, output_tokens):
    p = PRICING[provider]
    return (input_tokens * p["input"]) + (output_tokens * p["output"])
