import json
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from database import Database
from processor import check_ollama, list_models


def create_app(config: dict, db: Database) -> Flask:
    app = Flask(__name__)
    subreddits_list = config["subreddits"]
    stories_dir = config["settings"]["stories_dir"]

    @app.context_processor
    def inject_globals():
        return {
            "subreddits_list": subreddits_list,
            "total_stories": db.get_total_count(),
            "processed_stories": db.get_processed_count(),
        }

    # ── Index: alle Stories, filterbar + sortierbar ───────────────────────
    @app.route("/")
    def index():
        subreddit = request.args.get("subreddit", "").lower().strip()
        sort      = request.args.get("sort", "score")
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1

        per_page = 24
        offset   = (page - 1) * per_page

        if sort not in ("score", "date", "words"):
            sort = "score"

        stories = db.get_stories(
            subreddit=subreddit, sort=sort, limit=per_page, offset=offset
        )
        total = db.get_total_count(subreddit=subreddit)
        pages = max(1, -(-total // per_page))  # ceiling division

        sub_stats = db.get_all_subreddits()

        return render_template(
            "index.html",
            stories=stories,
            total=total,
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            current_sub=subreddit,
            sub_stats=sub_stats,
        )

    # ── Story ─────────────────────────────────────────────────────────────
    @app.route("/story/<code>")
    def story(code: str):
        row = db.get_story_by_code(code)
        if not row:
            abort(404)

        original_body = ""
        orig_path = Path(stories_dir) / row["subreddit"] / row["story_code"] / "original.txt"
        fallback  = Path(row["file_path"]) if row.get("file_path") else None
        for p in [orig_path, fallback]:
            if p and p.exists():
                raw = p.read_text(encoding="utf-8")
                parts = raw.split("---\n", 1)
                original_body = parts[1].strip() if len(parts) > 1 else raw
                break

        tiktok = None
        if row.get("json_path") and Path(row["json_path"]).exists():
            tiktok = json.loads(Path(row["json_path"]).read_text(encoding="utf-8"))

        video_dir = Path("data/outputs") / row["story_code"]
        video_parts = sorted(
            int(p.stem.split("_")[1])
            for p in video_dir.glob("part_*.mp4")
        ) if video_dir.exists() else []

        return render_template(
            "story.html",
            story=row,
            original_body=original_body,
            tiktok=tiktok,
            video_parts=video_parts,
        )

    # ── Video files ───────────────────────────────────────────────────────
    @app.route("/video/<code>/<int:part>.mp4")
    def video_file(code: str, part: int):
        p = Path("data/outputs") / code.upper() / f"part_{part}.mp4"
        if not p.exists():
            abort(404)
        return send_file(str(p.resolve()), mimetype="video/mp4")

    # ── Legacy: /<subreddit> → redirect to /?subreddit=… ─────────────────
    @app.route("/<subreddit>")
    def project(subreddit: str):
        known = [s.lower() for s in subreddits_list]
        if subreddit.lower() not in known:
            abort(404)
        return redirect(url_for("index", subreddit=subreddit.lower()))

    # ── API ───────────────────────────────────────────────────────────────
    @app.route("/api/subreddits")
    def api_subreddits():
        return jsonify(db.get_all_subreddits())

    @app.route("/api/stories")
    def api_stories():
        subreddit = request.args.get("subreddit", "")
        sort      = request.args.get("sort", "score")
        limit     = min(int(request.args.get("limit", 50)), 200)
        offset    = int(request.args.get("offset", 0))
        stories   = db.get_stories(subreddit=subreddit, sort=sort, limit=limit, offset=offset)
        return jsonify({"total": db.get_total_count(subreddit), "stories": stories})

    @app.route("/api/ollama/status")
    def api_ollama_status():
        running = check_ollama()
        return jsonify({"running": running, "models": list_models() if running else []})

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    return app
