import psycopg
from pgvector.psycopg import register_vector
from app.config import settings


def get_connection():
    conn = psycopg.connect(settings.database_url)
    register_vector(conn)
    return conn


def init_db():
    # Önce pgvector eklentisini aktif et
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # Tabloları oluştur
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Teknik bakım dokümanları ve vektörleri
            cur.execute("""
                CREATE TABLE IF NOT EXISTS maintenance_chunks (
                    id SERIAL PRIMARY KEY,
                    source_doc TEXT NOT NULL,
                    chunk_index INT NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector(768)
                );
            """)

            # 2. [Özellik 3] Değiştirilemez Kara Kutu (Black-Box Audit Log) tablosu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS blackbox_logs (
                    id SERIAL PRIMARY KEY,
                    timestamp_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    operator_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    response_summary TEXT NOT NULL,
                    sources_used TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL
                );
            """)
        conn.commit()