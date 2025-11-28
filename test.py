import os
from openai import OpenAI
import dotenv

dotenv.load_dotenv()


client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com")

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
    ],
    stream=False
)

print(response.choices[0].message.content)

#python text_to_scene.py --input-prompt "A firetruck from the left road is coming when the ego car is turning right." --return-ego --plan-only