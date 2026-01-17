# 📦 TG Storage Cluster

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green?style=for-the-badge&logo=fastapi)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)
![Developer](https://img.shields.io/badge/Developer-DraxonV1-orange?style=for-the-badge)

A lightweight, high-performance file storage API that transforms Telegram into an infinite, scalable backend. 

---

## 🚀 Installation

Install the package directly via pip:

```bash
pip install tgstorage-cluster
```

## 🛠️ Setup & Usage

### 1. Configuration
The package looks for a `.env` file and a `tokens.txt` file in your **current working directory**.

1. Create a `.env` file:
```env
API_ID=your_id
API_HASH=your_hash
CHANNEL_ID=your_channel_id
ADMIN_API_KEY=your_secret_key
BASE_URL=http://localhost
```

2. Create a `tokens.txt` file and add your bot tokens (one per line).

### 2. Run the Server
You can start the storage server using the built-in CLI command:

```bash
tgstorage
```
The server will start on `http://localhost:8082`.

### 3. Generate API Keys
Manage your client keys using the management CLI:

```bash
tgstorage-key --owner "MyWebApp"
```

---

## 📦 Integration in your Python Code

You can use the cluster logic directly in your own projects without running the FastAPI server:

```python
import asyncio
from tgstorage.bot import cluster

async def upload_something():
    # It will automatically load config from your current directory's .env
    bot = await cluster.get_healthy_bot()
    with open("image.png", "rb") as f:
        msg = await bot.send_document(
            chat_id=-100123456789, 
            document=f, 
            filename="image.png"
        )
    print(f"File uploaded! Message ID: {msg.message_id}")

asyncio.run(upload_something())
```

---

## 📜 License
MIT