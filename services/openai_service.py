import base64
from openai import OpenAI
from config import AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT_NAME

client = OpenAI(
    base_url=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY
)

def ask_model(prompt, page_input):
    pages = page_input if isinstance(page_input, list) else [page_input]
    content = [
        {"type": "text", "text": prompt}
    ]

    for p in pages:
        img = p.get("image")
        if img:
            encoded = base64.b64encode(img).decode()
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded}"
                }
            })

    completion = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT_NAME,
        messages=[
            {
                "role": "user",
                "content": content
            }
        ],
        max_tokens=16384,
        temperature=0.0
    )

    return completion.choices[0].message.content