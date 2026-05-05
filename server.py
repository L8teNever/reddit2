import json
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from database import Database
from processor import check_ollama, list_models, ensure_ollama


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
            "video_stories": db.get_video_count(),
            "background_count": len(list(Path("data/backgrounds").glob("*.mp4"))) if Path("data/backgrounds").exists() else 0,
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

        processed = request.args.get("processed") == "1"
        video     = request.args.get("video") == "1"

        stories = db.get_stories(
            subreddit=subreddit, sort=sort, limit=per_page, offset=offset,
            processed=processed, video=video
        )
        total = db.get_total_count(subreddit=subreddit, processed=processed, video=video)
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
            is_processed=processed,
            is_video=video,
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

    import threading
    from scraper import scrape_all
    from processor import process_batch, process_one

    job_state = {"status": "idle", "message": "", "progress": 0, "total": 0, "should_pause": False}

    def run_scrape():
        nonlocal job_state
        job_state["status"] = "running"
        job_state["message"] = "Scraping in progress..."
        job_state["should_pause"] = False
        try:
            scrape_all(config, db)
            job_state["message"] = "Scraping finished successfully."
        except Exception as e:
            job_state["message"] = f"Error during scraping: {str(e)}"
        finally:
            job_state["status"] = "idle"

    def run_process():
        nonlocal job_state
        job_state["status"] = "running"
        job_state["message"] = "Processing in progress..."
        job_state["should_pause"] = False
        try:
            model = config["settings"].get("ollama_model", "llama3.2")
            ensure_ollama(model)
            stories = db.get_unprocessed_stories()
            
            job_state["total"] = len(stories)
            job_state["progress"] = 0
            
            if not stories:
                job_state["message"] = "No unprocessed stories found."
            else:
                done = failed = 0
                for story in stories:
                    if job_state.get("should_pause"):
                        job_state["message"] = f"Paused. Processed {done} OK, {failed} Failed."
                        break
                    
                    job_state["message"] = f"Processing: {story.get('title', '')[:40]}..."
                    try:
                        process_one(story, model, config["settings"]["stories_dir"], db)
                        done += 1
                    except Exception:
                        failed += 1
                    
                    job_state["progress"] += 1
                
                if not job_state.get("should_pause"):
                    job_state["message"] = f"Processing finished: {done} OK, {failed} Failed."
        except Exception as e:
            job_state["message"] = f"Error during processing: {str(e)}"
        finally:
            job_state["status"] = "idle"

    @app.route("/api/trigger/scrape", methods=["POST"])
    def trigger_scrape():
        if job_state["status"] != "idle":
            return jsonify({"error": "A job is already running", "state": job_state}), 400
        threading.Thread(target=run_scrape, daemon=True).start()
        return jsonify({"message": "Scraping started"})

    @app.route("/api/trigger/process", methods=["POST"])
    def trigger_process():
        if job_state["status"] != "idle":
            return jsonify({"error": "A job is already running", "state": job_state}), 400
        threading.Thread(target=run_process, daemon=True).start()
        return jsonify({"message": "Processing started"})

    @app.route("/api/trigger/pause", methods=["POST"])
    def trigger_pause():
        if job_state["status"] == "running":
            job_state["should_pause"] = True
            return jsonify({"message": "Pause requested"})
        return jsonify({"message": "No job running"})

    def run_generate(code, words):
        nonlocal job_state
        job_state["status"] = "running"
        job_state["message"] = f"Generating video for {code}..."
        try:
            from main import cmd_generate
            from argparse import Namespace
            args = Namespace(config="config.json", code=code, words=words)
            cmd_generate(args)
            job_state["message"] = f"Video generation for {code} finished."
        except Exception as e:
            job_state["message"] = f"Error generating video: {str(e)}"
        finally:
            job_state["status"] = "idle"

    @app.route("/api/trigger/generate/<code>", methods=["POST"])
    def trigger_generate(code):
        if job_state["status"] != "idle":
            return jsonify({"error": "A job is already running", "state": job_state}), 400
        words = request.json.get("words", 250) if request.is_json else 250
        threading.Thread(target=run_generate, args=(code, words), daemon=True).start()
        return jsonify({"message": f"Video generation for {code} started"})

    @app.route("/api/job/status")
    def job_status():
        return jsonify(job_state)

    @app.route("/settings")
    def settings():
        bg_dir = Path("data/backgrounds")
        bg_dir.mkdir(parents=True, exist_ok=True)
        files = [f.name for f in bg_dir.glob("*") if f.suffix.lower() in (".mp4", ".mov", ".avi")]
        return render_template("settings.html", background_files=files)

    @app.route("/api/backgrounds/upload", methods=["POST"])
    def upload_background():
        if "file" not in request.files:
            return redirect(url_for("settings"))
        f = request.files["file"]
        if f.filename == "":
            return redirect(url_for("settings"))
        
        bg_dir = Path("data/backgrounds")
        bg_dir.mkdir(parents=True, exist_ok=True)
        
        # Sicherer Dateiname
        filename = "".join(c for c in f.filename if c.isalnum() or c in "._-")
        f.save(bg_dir / filename)
        return redirect(url_for("settings"))

    @app.route("/api/backgrounds/delete/<name>", methods=["POST"])
    def delete_background(name):
        bg_file = Path("data/backgrounds") / name
        if bg_file.exists() and bg_file.is_file():
            bg_file.unlink()
        return redirect(url_for("settings"))

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    return app
