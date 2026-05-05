import argparse
import json
import re
import sys
from pathlib import Path

from database import Database
from processor import ensure_ollama, process_batch, process_one
from scraper import _make_praw, load_config, scrape_all, scrape_subreddit
from server import create_app


def validate_env(config: dict):
    Path(config["settings"]["stories_dir"]).mkdir(parents=True, exist_ok=True)


# ── scrape ────────────────────────────────────────────────────────────────
def cmd_scrape(args):
    config = load_config(args.config)
    validate_env(config)
    db = Database(config["settings"]["db_path"])
    reddit = _make_praw(config["settings"])
    try:
        if args.subreddit:
            sub = args.subreddit.lower()
            if sub not in [s.lower() for s in config["subreddits"]]:
                print(f"Unbekanntes Subreddit '{sub}'. Trage es zuerst in config.json ein.")
                sys.exit(1)
            saved, skipped = scrape_subreddit(sub, db, config, reddit)
            print(f"r/{sub}: +{saved} saved, {skipped} skipped")
        else:
            print("Scraping alle Subreddits...")
            scrape_all(config, db)
    finally:
        db.close()


# ── process ───────────────────────────────────────────────────────────────
def cmd_process(args):
    config = load_config(args.config)
    validate_env(config)
    stories_dir = config["settings"]["stories_dir"]
    db = Database(config["settings"]["db_path"])

    model = ensure_ollama(args.model or "llama3.2")
    print(f"Modell: {model}")

    try:
        if args.code:
            # Single story by code
            story = db.get_story_by_code(args.code.upper())
            if not story:
                print(f"Story mit Code '{args.code}' nicht gefunden.")
                sys.exit(1)
            print(f"Verarbeite [{story['story_code']}] {story['title'][:60]}...")
            path = process_one(story, model, stories_dir, db)
            print(f"Gespeichert: {path}")

        else:
            # Batch
            stories = db.get_unprocessed_stories(
                subreddit=args.subreddit.lower() if args.subreddit else None
            )
            if not stories:
                print("Alle Stories bereits verarbeitet.")
                return
            print(f"{len(stories)} Stories zu verarbeiten...")
            done, failed = process_batch(stories, model, stories_dir, db)
            print(f"\nFertig: {done} OK, {failed} Fehler")
    finally:
        db.close()


# ── serve ─────────────────────────────────────────────────────────────────
def cmd_serve(args):
    config = load_config(args.config)
    validate_env(config)
    ensure_ollama()
    db = Database(config["settings"]["db_path"])
    app = create_app(config, db)
    print(f"Server läuft auf http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


# ── CLI ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Reddit Story Scraper + TikTok Processor")
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape", help="Stories von Reddit holen")
    p_scrape.add_argument("--subreddit", help="Nur ein Subreddit")

    p_proc = sub.add_parser("process", help="Stories mit Ollama zu TikTok-Content verarbeiten")
    p_proc.add_argument("--subreddit", help="Nur ein Subreddit verarbeiten")
    p_proc.add_argument("--code",      help="Einzelne Story per 6-Zeichen-Code")
    p_proc.add_argument("--model",     help="Ollama-Modell (Standard: erstes verfügbares)")

    p_serve = sub.add_parser("serve", help="Webserver starten")
    p_serve.add_argument("--host",  default="127.0.0.1")
    p_serve.add_argument("--port",  type=int, default=5000)
    p_serve.add_argument("--debug", action="store_true")

    p_gen = sub.add_parser("generate", help="TikTok-Video aus verarbeiteter Story generieren")
    p_gen.add_argument("--code",  required=True, help="6-Zeichen Story-Code")
    p_gen.add_argument("--words", type=int, default=250, help="Wörter pro Part (Standard: 250)")

    args = parser.parse_args()
    {
        "scrape":   cmd_scrape,
        "process":  cmd_process,
        "serve":    cmd_serve,
        "generate": cmd_generate,
    }[args.command](args)


# ── generate ──────────────────────────────────────────────────────────────
def _split_into_parts(text: str, max_words: int = 250) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    parts, current, count = [], [], 0
    for sent in sentences:
        words = sent.split()
        if count + len(words) > max_words and current:
            parts.append(" ".join(current))
            current, count = words[:], len(words)
        else:
            current.extend(words)
            count += len(words)
    if current:
        parts.append(" ".join(current))
    return parts or [text]


def cmd_generate(args):
    config = load_config(args.config)
    db = Database(config["settings"]["db_path"])

    story = db.get_story_by_code(args.code.upper())
    if not story:
        print(f"Story '{args.code}' nicht gefunden.")
        sys.exit(1)

    json_path = story.get("json_path")
    if not json_path or not Path(json_path).exists():
        print("Story noch nicht mit Ollama verarbeitet. Führe zuerst aus:")
        print(f"  python main.py process --code {args.code}")
        sys.exit(1)

    tiktok = json.loads(Path(json_path).read_text(encoding="utf-8"))
    story_text = tiktok.get("story", "")
    if not story_text:
        print("Kein Story-Text in tiktok.json gefunden.")
        sys.exit(1)

    from video_engine import generate_video
    parts = _split_into_parts(story_text, max_words=args.words)
    print(f"[{story['story_code']}] → {len(parts)} Part(s) à ~{args.words} Wörter")

    for i, part_text in enumerate(parts, 1):
        wc = len(part_text.split())
        print(f"  Part {i}/{len(parts)} ({wc} Wörter)...", end=" ", flush=True)
        try:
            out = generate_video(
                story_title=tiktok.get("tiktok_title", story["title"]),
                story_code=story["story_code"],
                part_num=i,
                text=part_text,
            )
            print(f"OK → {out}")
        except Exception as e:
            print(f"FEHLER: {e}")

    db.close()


if __name__ == "__main__":
    main()
