from openai import OpenAI
from app.config import OPENAI_API_KEY
from app.prompts import SYSTEM_PROMPT

client = OpenAI(api_key=OPENAI_API_KEY)

def run_openai(image_b64):
    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": SYSTEM_PROMPT},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_b64}"}
                ]
            }
        ]
    )

    return response.output_text, response.usage
