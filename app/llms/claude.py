import anthropic
from app.config import ANTHROPIC_API_KEY
from app.prompts import SYSTEM_PROMPT

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def run_claude(image_b64):
    msg = client.messages.create(
        model="claude-3-opus-20240229",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": SYSTEM_PROMPT
            }
        ]
    )

    usage = getattr(msg, "usage", None)

    return msg.content[0].text, usage
