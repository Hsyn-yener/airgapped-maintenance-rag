from pathlib import Path
import requests
from app.config import settings
from app.database import get_connection


def get_embedding(text: str) -> list[float]:
    """Ollama nomic-embed-text modeli ile metni vektöre çevirir."""
    response = requests.post(
        f"{settings.ollama_base_url}/api/embeddings",
        json={"model": settings.embed_model, "prompt": text},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["embedding"]


def ingest_manual_if_empty() -> int:
    """Veritabanı boşsa data/TB2-Motor-Bakim-v1.txt dosyasını parçalayıp vektör olarak kaydeder."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM maintenance_chunks;")
            count = cur.fetchone()[0]
            if count > 0:
                return count

    manual_path = Path("data/TB2-Motor-Bakim-v1.txt")
    if not manual_path.exists():
        return 0

    raw_text = manual_path.read_text(encoding="utf-8")
    chunks = [c.strip() for c in raw_text.split("---") if c.strip()]

    with get_connection() as conn:
        with conn.cursor() as cur:
            for idx, chunk in enumerate(chunks):
                emb = get_embedding(chunk)
                cur.execute(
                    """
                    INSERT INTO maintenance_chunks (source_doc, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s);
                    """,
                    ("TB2-Motor-Bakim-v1.txt", idx + 1, chunk, emb),
                )
        conn.commit()

    return len(chunks)


def ask_rag(question: str, top_k: int = 2) -> tuple[str, list[str]]:
    """Soruya en yakın bakım maddelerini bulur ve Ollama llama3 ile yalnızca belgeden yanıt üretir."""
    query_emb = get_embedding(question)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_doc, chunk_index, content
                FROM maintenance_chunks
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (query_emb, top_k),
            )
            rows = cur.fetchall()

    if not rows:
        return (
            "Veritabanında yüklü bakım dokümanı bulunamadı.",
            [],
        )

    context_blocks = []
    sources = []
    for source_doc, chunk_index, content in rows:
        context_blocks.append(content)
        sources.append(f"{source_doc} (Bölüm #{chunk_index})")

    context_text = "\n\n---\n\n".join(context_blocks)

    prompt = f"""Sen savunma sanayii sahasında görev yapan, kapalı devre (air-gapped) çalışan bir İHA Teknik Bakım ve Acil Durum Asistanısın.
Aşağıdaki TEKNİK DOKÜMAN parçalarına dayanarak operatörün sorusuna Türkçe, net, kısa ve maddeler halinde yanıt ver.
Dokümanda yazmayan hiçbir bilgiyi uydurma; dokümanda yoksa "Teknik dokümanda bu duruma ait kayıt bulunamadı." de.

TEKNİK DOKÜMAN:
{context_text}

OPERATÖR SORUSU:
{question}

ACİL MÜDAHALE YANITI:"""

    response = requests.post(
        f"{settings.ollama_base_url}/api/generate",
        json={
            "model": settings.llm_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=90,
    )
    response.raise_for_status()
    answer = response.json()["response"].strip()
    return answer, sources