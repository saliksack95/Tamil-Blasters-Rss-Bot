import asyncio
import logging
import threading
import io
import re

from flask import Flask
from bs4 import BeautifulSoup
import cloudscraper

from pyrogram import Client, utils as pyroutils
from config import BOT, API, OWNER, CHANNEL

# ---- Pyrogram ID limits ----
pyroutils.MIN_CHAT_ID = -999999999999
pyroutils.MIN_CHANNEL_ID = -10099999999999

# ---- Logging ----
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

# ---- Flask health check (for Render/Koyeb) ----
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# ---- Helpers ----
def extract_size(text: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB|KB))", text, re.IGNORECASE)
    return match.group(1) if match else "Unknown"

# ---- Crawl 1TamilMV ----
def crawl_tamilmv():
    base_url = "https://www.1tamilmv.kiwi"
    torrents = []
    scraper = cloudscraper.create_scraper()

    try:
        r = scraper.get(base_url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        topic_links = [
            a.get("href") for a in soup.select('a[href*="/forums/topic/"]')
            if a.get("href")
        ]

        for rel in list(dict.fromkeys(topic_links))[:15]:
            try:
                topic_url = rel if rel.startswith("http") else base_url + rel
                tr = scraper.get(topic_url, timeout=10)
                tr.raise_for_status()
                tsoup = BeautifulSoup(tr.text, "html.parser")

                torrent_tags = tsoup.select('a[data-fileext="torrent"]')
                files = []

                for tag in torrent_tags:
                    href = tag.get("href")
                    if not href:
                        continue

                    raw = tag.get_text(strip=True)
                    title = raw.replace("www.1TamilMV", "").replace(".torrent", "").strip()
                    size = extract_size(raw)

                    files.append({
                        "title": title,
                        "link": href.strip(),
                        "size": size
                    })

                if files:
                    torrents.append({
                        "topic_url": topic_url,
                        "title": files[0]["title"],
                        "size": files[0]["size"],
                        "links": files
                    })

            except Exception as e:
                logging.error(f"Failed to parse topic {rel}: {e}")

    except Exception as e:
        logging.error(f"Failed to fetch TamilMV homepage: {e}")

    return torrents

# ---- Telegram Bot ----
class MN_Bot(Client):
    MAX_MSG_LENGTH = 4000

    def __init__(self):
        super().__init__(
            "MN-TamilMV-Bot",
            api_id=API.ID,
            api_hash=API.HASH,
            bot_token=BOT.TOKEN,
            plugins=dict(root="plugins"),
            workers=8
        )
        self.channel_id = CHANNEL.ID
        self.posted_links = set()
        self.seen_topics = set()

    async def auto_post_torrents(self):
        while True:
            try:
                torrents = crawl_tamilmv()

                for t in torrents:
                    topic = t["topic_url"]
                    new_files = [f for f in t["links"] if f["link"] not in self.posted_links]

                    if topic in self.seen_topics and not new_files:
                        continue

                    for file in new_files:
                        try:
                            scraper = cloudscraper.create_scraper()
                            fr = scraper.get(file["link"], timeout=10)
                            fr.raise_for_status()

                            data = io.BytesIO(fr.content)
                            filename = file["title"].replace(" ", "_") + ".torrent"
                            caption = (
                                f"{file['title']}\n"
                                f"📦 {file['size']}\n"
                                f"#TamilMV"
                            )

                            await self.send_document(
                                self.channel_id,
                                data,
                                file_name=filename,
                                caption=caption
                            )

                            self.posted_links.add(file["link"])
                            logging.info(f"Posted: {file['title']}")
                            await asyncio.sleep(3)

                        except Exception as e:
                            logging.error(f"Failed to send torrent: {e}")

                    self.seen_topics.add(topic)

            except Exception as e:
                logging.error(f"Auto-post error: {e}")

            await asyncio.sleep(900)  # 15 minutes

    async def start(self):
        await super().start()
        me = await self.get_me()
        BOT.USERNAME = f"@{me.username}"
        await self.send_message(
            OWNER.ID,
            f"{me.first_name} ✅ TamilMV bot started (15‑min checks)"
        )
        asyncio.create_task(self.auto_post_torrents())

    async def stop(self, *args):
        await super().stop()
        logging.info("Bot stopped")

# ---- Entrypoint ----
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    MN_Bot().run()
