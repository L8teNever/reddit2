import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from database import Database

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
})


def load_config(config_path: str = "config.json") -> dict:
    config = {
        "subreddits": [s.strip() for s in os.environ.get("SUBREDDITS", "AmItheAsshole,tifu,relationship_advice").split(",") if s.strip()],
        "settings": {
            "min_word_count": int(os.environ.get("MIN_WORD_COUNT", "200")),
            "stories_per_fetch": int(os.environ.get("STORIES_PER_FETCH", "50")),
            "request_delay_seconds": float(os.environ.get("REQUEST_DELAY_SECONDS", "1.5")),
            "user_agent": os.environ.get("USER_AGENT", "RedditStoryScraper/1.0"),
            "db_path": os.environ.get("DB_PATH", "stories.db"),
            "stories_dir": os.environ.get("STORIES_DIR", "stories"),
            "reddit_api": {
                "client_id": os.environ.get("REDDIT_CLIENT_ID", ""),
                "client_secret": os.environ.get("REDDIT_CLIENT_SECRET", "")
            }
        }
    }
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                fc = json.load(f)
                if "subreddits" in fc: config["subreddits"] = fc["subreddits"]
                if "settings" in fc: config["settings"].update(fc["settings"])
        except Exception:
            pass

    s = config["settings"]
    s["db_path"]     = os.environ.get("DB_PATH",     s.get("db_path",     "stories.db"))
    s["stories_dir"] = os.environ.get("STORIES_DIR", s.get("stories_dir", "stories"))
    return config


def _make_praw(settings: dict):
    creds = settings.get("reddit_api", {})
    if not creds.get("client_id") or not creds.get("client_secret"):
        return None
    try:
        import praw
        return praw.Reddit(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            user_agent=settings.get("user_agent", "RedditStoryScraper/1.0"),
        )
    except Exception as e:
        print(f"  PRAW-Fehler: {e} — Fallback auf public API")
        return None


def fetch_subreddit_praw(reddit, subreddit: str, limit: int) -> list[dict]:
    posts = []
    for s in reddit.subreddit(subreddit).top(time_filter="all", limit=limit):
        posts.append({"data": {
            "id": s.id,
            "subreddit": s.subreddit.display_name,
            "title": s.title,
            "author": str(s.author) if s.author else "[deleted]",
            "score": s.score,
            "selftext": s.selftext,
            "permalink": s.permalink,
            "created_utc": s.created_utc,
        }})
    return posts


def fetch_subreddit_http(subreddit: str, limit: int) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/top.json"
    resp = _SESSION.get(url, params={"limit": limit, "t": "all"}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("children", [])


def count_words(text: str) -> int:
    cleaned = re.sub(r"[*_~`#\[\]()>]", " ", text)
    return len(cleaned.split())


def parse_post(raw_post: dict) -> dict | None:
    data = raw_post.get("data", {})
    body = data.get("selftext", "").strip()
    if not body or body in ("[removed]", "[deleted]"):
        return None
    return {
        "reddit_id":   data.get("id", ""),
        "subreddit":   data.get("subreddit", "").lower(),
        "title":       data.get("title", ""),
        "author":      data.get("author", "[deleted]"),
        "score":       data.get("score", 0),
        "body":        body,
        "word_count":  count_words(body),
        "url":         f"https://www.reddit.com{data.get('permalink', '')}",
        "created_utc": int(data.get("created_utc", 0)),
    }


def save_story_file(story: dict, stories_dir: str) -> str:
    """Saves original.txt inside stories/<subreddit>/<code>/original.txt"""
    code = story["story_code"]
    sub = story["subreddit"]
    story_dir = Path(stories_dir) / sub / code
    story_dir.mkdir(parents=True, exist_ok=True)
    file_path = story_dir / "original.txt"
    scraped_at = story.get("scraped_at", datetime.now().isoformat())
    content = (
        f"Title: {story['title']}\n"
        f"Author: u/{story['author']}\n"
        f"Score: {story['score']}\n"
        f"URL: {story['url']}\n"
        f"Scraped: {scraped_at}\n"
        f"Words: {story['word_count']}\n"
        f"---\n\n"
        f"{story['body']}\n"
    )
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


def scrape_subreddit(
    subreddit: str, db: Database, config: dict, reddit=None
) -> tuple[int, int]:
    settings = config["settings"]
    min_words = settings["min_word_count"]
    saved = skipped = 0

    print(f"  Fetching r/{subreddit}...")
    try:
        raw_posts = (
            fetch_subreddit_praw(reddit, subreddit, settings["stories_per_fetch"])
            if reddit else
            fetch_subreddit_http(subreddit, settings["stories_per_fetch"])
        )
    except Exception as e:
        print(f"  ERROR r/{subreddit}: {e}")
        return 0, 0

    for raw in raw_posts:
        story = parse_post(raw)
        if story is None or story["word_count"] < min_words or db.exists(story["reddit_id"]):
            skipped += 1
            continue
        story["story_code"] = db.generate_code()
        story["scraped_at"] = datetime.now(timezone.utc).isoformat()
        story["file_path"] = save_story_file(story, settings["stories_dir"])
        db.insert_story(story)
        saved += 1

    return saved, skipped


def scrape_all(config: dict, db: Database) -> None:
    delay = config["settings"]["request_delay_seconds"]
    subreddits = config["subreddits"]
    reddit = _make_praw(config["settings"])

    mode = "PRAW (OAuth)" if reddit else "Public JSON API"
    print(f"  Modus: {mode}")

    total_saved = total_skipped = 0
    for i, subreddit in enumerate(subreddits):
        saved, skipped = scrape_subreddit(subreddit, db, config, reddit)
        total_saved += saved
        total_skipped += skipped
        print(f"  r/{subreddit}: +{saved} saved, {skipped} skipped")
        if i < len(subreddits) - 1:
            time.sleep(delay)

    print(f"\nDone. Total saved: {total_saved}, skipped: {total_skipped}")
