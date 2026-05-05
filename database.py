import random
import sqlite3
import string
from datetime import datetime, timezone


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate()        # adds columns BEFORE dependent indexes

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS stories (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                reddit_id    TEXT UNIQUE NOT NULL,
                subreddit    TEXT NOT NULL,
                title        TEXT NOT NULL,
                author       TEXT,
                score        INTEGER DEFAULT 0,
                word_count   INTEGER NOT NULL,
                url          TEXT,
                file_path    TEXT,
                scraped_at   TEXT NOT NULL,
                created_utc  INTEGER
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reddit_id ON stories(reddit_id);
            CREATE INDEX IF NOT EXISTS idx_subreddit        ON stories(subreddit);
        """)
        self._conn.commit()

    def _migrate(self):
        """Add new columns to existing tables, then create dependent indexes."""
        cursor = self._conn.execute("PRAGMA table_info(stories)")
        existing = {row[1] for row in cursor.fetchall()}

        for col, definition in [
            ("story_code",   "TEXT"),
            ("json_path",    "TEXT"),
            ("processed_at", "TEXT"),
        ]:
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE stories ADD COLUMN {col} {definition}"
                )
        self._conn.commit()

        # Indexes that depend on migrated columns
        self._conn.executescript("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_story_code ON stories(story_code);
        """)
        self._conn.commit()

        self._assign_missing_codes()

    def _assign_missing_codes(self):
        rows = self._conn.execute(
            "SELECT id FROM stories WHERE story_code IS NULL"
        ).fetchall()
        for row in rows:
            code = self._new_code()
            self._conn.execute(
                "UPDATE stories SET story_code = ? WHERE id = ?", (code, row[0])
            )
        if rows:
            self._conn.commit()

    def _new_code(self) -> str:
        chars = string.ascii_uppercase + string.digits
        while True:
            code = "".join(random.choices(chars, k=6))
            if not self._conn.execute(
                "SELECT 1 FROM stories WHERE story_code = ?", (code,)
            ).fetchone():
                return code

    def generate_code(self) -> str:
        return self._new_code()

    def exists(self, reddit_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM stories WHERE reddit_id = ?", (reddit_id,)
        ).fetchone() is not None

    def insert_story(self, story: dict) -> int:
        if not story.get("story_code"):
            story["story_code"] = self._new_code()
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO stories
               (story_code, reddit_id, subreddit, title, author, score,
                word_count, url, file_path, scraped_at, created_utc)
               VALUES
               (:story_code, :reddit_id, :subreddit, :title, :author, :score,
                :word_count, :url, :file_path, :scraped_at, :created_utc)""",
            story,
        )
        self._conn.commit()
        return cur.lastrowid

    def get_all_subreddits(self) -> list[dict]:
        cur = self._conn.execute(
            """SELECT subreddit,
                      COUNT(*) AS story_count,
                      SUM(CASE WHEN processed_at IS NOT NULL THEN 1 ELSE 0 END) AS processed_count,
                      MAX(scraped_at) AS last_scraped
               FROM stories GROUP BY subreddit ORDER BY subreddit"""
        )
        return [dict(row) for row in cur.fetchall()]

    def get_stories_by_subreddit(
        self, subreddit: str, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        cur = self._conn.execute(
            """SELECT id, story_code, reddit_id, title, author, score,
                      word_count, scraped_at, created_utc, processed_at
               FROM stories WHERE subreddit = ?
               ORDER BY score DESC LIMIT ? OFFSET ?""",
            (subreddit, limit, offset),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_story_by_id(self, story_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM stories WHERE id = ?", (story_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_story_by_code(self, code: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM stories WHERE story_code = ?", (code.upper(),)
        ).fetchone()
        return dict(row) if row else None

    def get_story_count(self, subreddit: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM stories WHERE subreddit = ?", (subreddit,)
        ).fetchone()[0]

    def get_unprocessed_stories(
        self, subreddit: str = None, limit: int = 200
    ) -> list[dict]:
        if subreddit:
            cur = self._conn.execute(
                "SELECT * FROM stories WHERE processed_at IS NULL AND subreddit = ? LIMIT ?",
                (subreddit.lower(), limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM stories WHERE processed_at IS NULL LIMIT ?", (limit,)
            )
        return [dict(row) for row in cur.fetchall()]

    def mark_processed(self, story_id: int, json_path: str):
        self._conn.execute(
            "UPDATE stories SET json_path = ?, processed_at = ? WHERE id = ?",
            (json_path, datetime.now(timezone.utc).isoformat(), story_id),
        )
        self._conn.commit()

    def update_file_path(self, story_id: int, file_path: str):
        self._conn.execute(
            "UPDATE stories SET file_path = ? WHERE id = ?", (file_path, story_id)
        )
        self._conn.commit()

    def close(self):
        self._conn.commit()
        self._conn.close()

    def get_stories(
        self,
        subreddit: str = "",
        sort: str = "score",
        limit: int = 24,
        offset: int = 0,
    ) -> list[dict]:
        _sort_map = {
            "score": "score DESC",
            "date":  "scraped_at DESC",
            "words": "word_count DESC",
        }
        order = _sort_map.get(sort, "score DESC")
        if subreddit:
            cur = self._conn.execute(
                f"""SELECT id, story_code, subreddit, title, author, score,
                           word_count, scraped_at, processed_at
                    FROM stories WHERE subreddit = ?
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                (subreddit.lower(), limit, offset),
            )
        else:
            cur = self._conn.execute(
                f"""SELECT id, story_code, subreddit, title, author, score,
                           word_count, scraped_at, processed_at
                    FROM stories
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        return [dict(row) for row in cur.fetchall()]

    def get_total_count(self, subreddit: str = "") -> int:
        if subreddit:
            return self._conn.execute(
                "SELECT COUNT(*) FROM stories WHERE subreddit = ?",
                (subreddit.lower(),),
            ).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]

    def get_processed_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM stories WHERE processed_at IS NOT NULL"
        ).fetchone()[0]
