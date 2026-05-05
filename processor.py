"""
Ollama Story Processor
----------------------
Verarbeitet Reddit-Stories zu TikTok-Content via lokalem Ollama-Modell.

Setup:
  1. Ollama installieren: https://ollama.com
  2. Modell laden: ollama pull llama3.2
  3. python main.py process

Befehle:
  python main.py process                      # alle unverarbeiteten Stories
  python main.py process --subreddit tifu     # nur ein Subreddit
  python main.py process --code AB3X7K        # eine einzelne Story
  python main.py process --model gemma3       # anderes Modell
"""
import json
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import requests

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11436")

_SYSTEM = (
    "You are a TikTok content creator specializing in viral Reddit stories. "
    "Always respond with valid JSON only — no markdown, no explanation."
)

_PROMPT = """\
Given this Reddit story, produce TikTok-ready content as JSON with exactly these keys:

"tiktok_title"      : Catchy, hook-first title. Max 80 chars. Start with a question or shocking statement.
"tiktok_description": Short teaser for the video description. Max 200 chars. No spoilers.
"hashtags"          : Array of 8-12 hashtags. Always include "#storytime" and "#reddit". Add niche tags.
"story"             : The cleaned story text:
                        - Remove ALL URLs and hyperlinks (http..., markdown [text](url))
                        - Remove image embeds (![alt](url)) and imgur links
                        - Remove Reddit artifacts: [View Poll], [deleted], vote tallies
                        - Remove excessive blank lines (max one blank line between paragraphs)
                        - Keep the full narrative — do not shorten or summarize
                        - Fix obvious typos

Subreddit: r/{subreddit}
Title: {title}

Story:
{body}
"""


def check_ollama() -> bool:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def list_models() -> list[str]:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except requests.RequestException:
        return []


def pull_model(model_name: str) -> None:
    """Pull a model from Ollama registry, streaming progress to stdout."""
    with requests.post(
        f"{OLLAMA_BASE}/api/pull",
        json={"name": model_name},
        stream=True,
        timeout=1800,
    ) as r:
        r.raise_for_status()
        for raw_line in r.iter_lines():
            if not raw_line:
                continue
            try:
                d = json.loads(raw_line)
                status = d.get("status", "")
                if d.get("total"):
                    pct = int(d.get("completed", 0) / d["total"] * 100)
                    print(f"\r  {status}: {pct}%  ", end="", flush=True)
                elif status:
                    print(f"  {status}", flush=True)
            except (json.JSONDecodeError, ZeroDivisionError):
                pass
    print()


def ensure_ollama(preferred_model: str = "llama3.2") -> str:
    """
    Ensures Ollama is installed, running, and has at least one model.
    Returns the model name to use.
    Skips local install when OLLAMA_HOST points to a remote host (e.g. Docker).
    """
    is_remote = OLLAMA_BASE not in ("http://localhost:11436", "http://127.0.0.1:11436")

    if not is_remote:
        if not shutil.which("ollama"):
            _install_ollama()
        if not check_ollama():
            _start_ollama()
            print("Warte auf Ollama...", end=" ", flush=True)
            for _ in range(20):
                time.sleep(1)
                if check_ollama():
                    print("bereit.")
                    break
            else:
                print("\nWARNUNG: Ollama antwortet nicht nach 20s.")
                return preferred_model
    else:
        if not check_ollama():
            print(f"Warte auf Ollama ({OLLAMA_BASE})...", end=" ", flush=True)
            for _ in range(30):
                time.sleep(2)
                if check_ollama():
                    print("bereit.")
                    break
            else:
                print("\nWARNUNG: Ollama nicht erreichbar.")
                return preferred_model

    models = list_models()
    if not models:
        print(f"Kein Modell vorhanden. Lade '{preferred_model}'...")
        pull_model(preferred_model)
        return preferred_model

    return next((m for m in models if m.startswith(preferred_model.split(":")[0])), models[0])


def _install_ollama() -> None:
    system = platform.system()
    print(f"Ollama nicht gefunden. Installiere für {system}...")
    if system in ("Linux", "Darwin"):
        subprocess.run(
            "curl -fsSL https://ollama.com/install.sh | sh",
            shell=True, check=True,
        )
    elif system == "Windows":
        if shutil.which("winget"):
            subprocess.run(
                ["winget", "install", "Ollama.Ollama",
                 "--accept-package-agreements", "--accept-source-agreements", "--silent"],
                check=True,
            )
        else:
            tmp = Path(os.environ.get("TEMP", ".")) / "OllamaSetup.exe"
            print("Lade Ollama-Installer herunter (~80 MB)...")
            urllib.request.urlretrieve("https://ollama.com/download/OllamaSetup.exe", tmp)
            subprocess.run([str(tmp), "/S"], check=True)
            time.sleep(8)
    else:
        print(f"Unbekanntes System '{system}'. Installiere Ollama manuell: https://ollama.com")


def _start_ollama() -> None:
    print("Starte Ollama-Server...")
    kwargs = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    subprocess.Popen(["ollama", "serve"], **kwargs)


def _call_ollama(prompt: str, model: str) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "system": _SYSTEM,
        "options": {"temperature": 0.7},
    }
    r = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=600)
    r.raise_for_status()
    raw = r.json().get("response", "").strip()
    # Strip accidental markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _read_body(story: dict, stories_dir: str) -> str:
    """Reads the story body from disk, trying the new folder structure first."""
    code = story.get("story_code", "")
    sub = story.get("subreddit", "")

    candidates = [
        Path(stories_dir) / sub / code / "original.txt",
        Path(story.get("file_path", "")),
    ]
    for p in candidates:
        if p and p.exists():
            raw = p.read_text(encoding="utf-8")
            parts = raw.split("---\n", 1)
            return parts[1].strip() if len(parts) > 1 else raw
    return ""


def _migrate_to_folder(story: dict, stories_dir: str, db) -> None:
    """
    Moves an old <reddit_id>.txt to the new stories/<sub>/<code>/original.txt
    folder if it hasn't been moved yet.
    """
    code = story.get("story_code", "")
    sub = story.get("subreddit", "")
    if not code or not sub:
        return

    new_path = Path(stories_dir) / sub / code / "original.txt"
    if new_path.exists():
        return  # Already migrated

    old_path = Path(story.get("file_path", ""))
    if old_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(old_path.read_bytes())
        db.update_file_path(story["id"], str(new_path))


def save_tiktok_json(story: dict, tiktok_data: dict, stories_dir: str) -> str:
    code = story["story_code"]
    sub = story["subreddit"]
    story_dir = Path(stories_dir) / sub / code
    story_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "code":                code,
        "reddit_id":           story["reddit_id"],
        "subreddit":           sub,
        "original_title":      story["title"],
        "tiktok_title":        tiktok_data.get("tiktok_title", story["title"]),
        "tiktok_description":  tiktok_data.get("tiktok_description", ""),
        "hashtags":            tiktok_data.get("hashtags", []),
        "story":               tiktok_data.get("story", ""),
        "url":                 story.get("url", ""),
        "score":               story.get("score", 0),
        "processed_at":        datetime.now(timezone.utc).isoformat(),
    }

    json_path = story_dir / "tiktok.json"
    json_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return str(json_path)


def process_one(story: dict, model: str, stories_dir: str, db) -> str:
    """
    Processes a single story.
    Returns the json_path on success, raises on failure.
    """
    _migrate_to_folder(story, stories_dir, db)

    body = _read_body(story, stories_dir)
    if not body:
        raise ValueError("Kein Story-Text gefunden")

    prompt = _PROMPT.format(
        subreddit=story["subreddit"],
        title=story["title"],
        body=body[:5000],  # cap to avoid very slow responses
    )

    tiktok_data = _call_ollama(prompt, model)
    json_path = save_tiktok_json(story, tiktok_data, stories_dir)
    db.mark_processed(story["id"], json_path)
    return json_path


def process_batch(
    stories: list[dict], model: str, stories_dir: str, db
) -> tuple[int, int]:
    """Processes a list of stories. Returns (done, failed)."""
    done = failed = 0
    total = len(stories)
    for i, story in enumerate(stories, 1):
        code = story.get("story_code", "?")
        title = story.get("title", "")[:55]
        print(f"  [{i}/{total}] {code} — {title}...", end=" ", flush=True)
        try:
            process_one(story, model, stories_dir, db)
            print("OK")
            done += 1
        except Exception as e:
            print(f"FEHLER: {e}")
            failed += 1
    return done, failed
