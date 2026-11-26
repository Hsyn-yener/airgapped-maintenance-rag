import hashlib
import hmac
import time
from datetime import datetime, timezone
import psycopg
from app.config import settings


def generate_hmac_signature(operator_id: str, timestamp: str, query: str) -> str:
    """Operatör kimliği, zaman damgası ve sorgudan HMAC-SHA256 imzası üretir."""
    message = f"{operator_id}:{timestamp}:{query}".encode("utf-8")
    secret = settings.hmac_secret_key.encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_hmac_signature(
    operator_id: str,
    timestamp: str,
    query: str,
    provided_signature: str,
    max_age_seconds: int = 300,
) -> tuple[bool, str]:
    """Gelen isteğin HMAC imzasını ve zaman damgasını doğrular."""
    if not provided_signature:
        return False, "Eksik HMAC imzası: Yetkisiz istek reddedildi."

    try:
        req_ts = int(timestamp)
        now_ts = int(time.time())
        if abs(now_ts - req_ts) > max_age_seconds:
            return False, "Zaman aşımı (Replay saldırısı koruması): İstek süresi dolmuş."
    except ValueError:
        return False, "Geçersiz zaman damgası formatı."

    expected_signature = generate_hmac_signature(operator_id, timestamp, query)
    if not hmac.compare_digest(expected_signature, provided_signature):
        return False, "Geçersiz HMAC imzası: Veri bütünlüğü doğrulanamadı."

    return True, "Doğrulandı"


def _compute_record_hash(
    prev_hash: str,
    timestamp_iso: str,
    operator_id: str,
    action_type: str,
    query_text: str,
    response_summary: str,
    sources_used: str,
) -> str:
    payload = (
        f"{prev_hash}|{timestamp_iso}|{operator_id}|"
        f"{action_type}|{query_text}|{response_summary}|{sources_used}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_blackbox_log(
    operator_id: str,
    action_type: str,
    query_text: str,
    response_summary: str,
    sources_used: str,
) -> dict:
    """Kara kutuya önceki kaydın hash'ine zincirlenmiş yeni bir denetim kaydı ekler."""
    now_utc = datetime.now(timezone.utc)
    timestamp_iso = now_utc.isoformat()

    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT record_hash FROM blackbox_logs ORDER BY id DESC LIMIT 1;"
            )
            row = cur.fetchone()
            prev_hash = row[0] if row else "GENESIS_0000000000000000"

            record_hash = _compute_record_hash(
                prev_hash=prev_hash,
                timestamp_iso=timestamp_iso,
                operator_id=operator_id,
                action_type=action_type,
                query_text=query_text,
                response_summary=response_summary,
                sources_used=sources_used,
            )

            cur.execute(
                """
                INSERT INTO blackbox_logs (
                    timestamp_utc, operator_id, action_type,
                    query_text, response_summary, sources_used,
                    prev_hash, record_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    now_utc,
                    operator_id,
                    action_type,
                    query_text,
                    response_summary,
                    sources_used,
                    prev_hash,
                    record_hash,
                ),
            )
            log_id = cur.fetchone()[0]
        conn.commit()

    return {
        "id": log_id,
        "timestamp_utc": timestamp_iso,
        "operator_id": operator_id,
        "action_type": action_type,
        "record_hash": record_hash[:16] + "...",
    }


def get_recent_logs(limit: int = 10) -> list[dict]:
    """Son kara kutu kayıtlarını döndürür."""
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, timestamp_utc, operator_id, action_type,
                       query_text, sources_used, prev_hash, record_hash
                FROM blackbox_logs
                ORDER BY id DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "timestamp_utc": r[1].isoformat(),
            "operator_id": r[2],
            "action_type": r[3],
            "query_text": r[4],
            "sources_used": r[5],
            "prev_hash": r[6][:12] + "...",
            "record_hash": r[7][:12] + "...",
        }
        for r in rows
    ]