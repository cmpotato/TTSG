"""简单演示如何调用 DeepSeek 聊天模型。"""

import os

from openai import OpenAI
import dotenv

# 通过 .env 文件加载所需的 API 密钥，避免在代码中硬编码敏感信息
dotenv.load_dotenv()


# 创建 OpenAI 客户端实例，用于向 DeepSeek 服务发送请求
client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

# 构造一次简单的对话请求并获取模型回复
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
    ],
    stream=False,
)

# 输出首条回复内容，便于快速查看模型的回答
print(response.choices[0].message.content)