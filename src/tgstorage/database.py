import aiosqlite
from .config import settings
import datetime

async def init_db():
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT UNIQUE,
                message_id INTEGER,
                file_name TEXT,
                file_size INTEGER,
                mime_type TEXT,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expiration_date TIMESTAMP,
                share_token TEXT UNIQUE,
                view_count INTEGER DEFAULT 0,
                password TEXT,
                owner_key TEXT
            )
        """)
        async with db.execute("PRAGMA table_info(files)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "owner_key" not in columns:
            await db.execute("ALTER TABLE files ADD COLUMN owner_key TEXT")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                owner TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Insert default key if it doesn't exist
        await db.execute(
            "INSERT OR IGNORE INTO api_keys (key, owner) VALUES (?, ?)",
            (settings.ADMIN_API_KEY, "admin")
        )
        await db.commit()

async def verify_key_db(key):
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        async with db.execute("SELECT 1 FROM api_keys WHERE key = ?", (key,)) as cursor:
            return await cursor.fetchone() is not None

async def add_file(
    file_id,
    message_id,
    file_name,
    file_size,
    mime_type,
    expiration_date=None,
    share_token=None,
    password=None,
    owner_key=None,
):
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        await db.execute(
            "INSERT INTO files (file_id, message_id, file_name, file_size, mime_type, expiration_date, share_token, password, owner_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, message_id, file_name, file_size, mime_type, expiration_date, share_token, password, owner_key),
        )
        await db.commit()

async def get_file_by_id(file_id):
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)) as cursor:
            return await cursor.fetchone()

async def get_file_by_share_token(token):
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE share_token = ?", (token,)) as cursor:
            return await cursor.fetchone()

async def increment_view_count(file_id):
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        await db.execute("UPDATE files SET view_count = view_count + 1 WHERE file_id = ?", (file_id,))
        await db.commit()

async def list_files(limit=20, offset=0, search=None, auth_key=None):
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM files"
        params = []
        where_clauses = []
        is_admin = auth_key == settings.ADMIN_API_KEY.strip()
        if auth_key and not is_admin:
            where_clauses.append("owner_key = ?")
            params.append(auth_key)
        if search:
            where_clauses.append("file_name LIKE ?")
            params.append(f"%{search}%")
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY upload_date DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

async def get_stats():
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        async with db.execute("SELECT COUNT(*), SUM(file_size), SUM(view_count) FROM files") as cursor:
            row = await cursor.fetchone()
            return {
                "total_files": row[0] or 0,
                "total_size_bytes": row[1] or 0,
                "total_views": row[2] or 0
            }

async def delete_file_db(file_id):
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        await db.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
        await db.commit()

async def get_expired_files():
    async with aiosqlite.connect(settings.DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        now = datetime.datetime.now().isoformat()
        async with db.execute("SELECT * FROM files WHERE expiration_date IS NOT NULL AND expiration_date < ?", (now,)) as cursor:
            return await cursor.fetchall()
