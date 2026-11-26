import io
import socket
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import qrcode
import requests

from app.config import settings
from app.database import init_db
from app.rag import ask_rag, ingest_manual_if_empty
from app.security import (
    get_recent_logs,
    verify_hmac_signature,
    write_blackbox_log,
)


def get_local_ip() -> str:
    """Yerel ağ (LAN) üzerindeki IP adresini bulur."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        count = ingest_manual_if_empty()
        print(f"[SİSTEM HAZIR] Veritabanında {count} bakım bölümü indekslendi.")
    except Exception as exc:
        print(f"[UYARI] Ollama bağlantısı henüz hazır değil: {exc}")
    yield


app = FastAPI(
    title="Air-Gapped İHA Bakım ve Acil Durum Karar Destek Sistemi",
    lifespan=lifespan,
)


class QueryRequest(BaseModel):
    operator_id: str
    timestamp: str
    query: str
    signature: str


@app.get("/")
def serve_ui():
    return FileResponse("app/static/index.html")


@app.get("/api/health")
def health_check():
    """[Özellik 2] Heartbeat protokolü için sunucu ve Ollama sağlık kontrolü."""
    ollama_ok = False
    try:
        r = requests.get(f"{settings.ollama_base_url}/api/tags", timeout=2)
        ollama_ok = r.status_code == 200
    except Exception:
        ollama_ok = False

    lan_ip = get_local_ip()
    return {
        "status": "ONLINE",
        "ollama_ready": ollama_ok,
        "lan_url": f"http://{lan_ip}:8000",
    }


@app.get("/api/qr")
def generate_qr():
    """[Özellik 4] Mobil yedek istasyon erişimi için yerel ağ QR kodu üretir."""
    lan_url = f"http://{get_local_ip()}:8000"
    img = qrcode.make(lan_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/offline-pack")
def offline_emergency_pack():
    """[Özellik 4] Bağlantı tamamen kopsa bile mobilde çalışacak acil durum önbellek paketi."""
    return {
        "version": "TB2-Motor-Bakim-v1",
        "procedures": [
            {
                "code": "E-104",
                "title": "Motor Hararet ve Yağ Basıncı Arızası",
                "steps": "1. Gaz kolunu %45 seviyesine çekin. 2. Radyatör kapakçıklarını %100 açın. 3. 30 saniyede 120°C altına düşmezse acil alçalma başlatın.",
            },
            {
                "code": "E-208",
                "title": "Yakıt Akış Düzensizliği",
                "steps": "1. Yedek elektrikli yakıt pompasını (AUX FUEL PUMP) açın. 2. Devri 4800 RPM sabit tutun. 3. Ani gaz değişiminden kaçının.",
            },
            {
                "code": "E-312",
                "title": "Alternatör Şarj Kaybı (Batarya Yedek Modu)",
                "steps": "1. Kamera/gimbal görev yükü gücünü kapatın. 2. Kalan güvenli uçuş süresi maksimum 18 dakikadır; derhal RTL (Üsse Dönüş) verin.",
            },
            {
                "code": "E-401",
                "title": "Haberleşme Kopması ve Geofence İhlali",
                "steps": "1. Otopilot 3000 feet irtifaya yükselip Home Point koordinatına döner. 2. 60 saniye içinde link gelmezse otonom paraşüt/iniş başlar.",
            },
        ],
    }


@app.post("/api/query")
def secure_rag_query(req: QueryRequest):
    """[Özellik 1 & 3] HMAC doğrulamalı RAG sorgusu ve Kara Kutu kaydı."""
    is_valid, reason = verify_hmac_signature(
        operator_id=req.operator_id,
        timestamp=req.timestamp,
        query=req.query,
        provided_signature=req.signature,
    )

    if not is_valid:
        write_blackbox_log(
            operator_id=req.operator_id,
            action_type="SECURITY_REJECTED",
            query_text=req.query,
            response_summary=reason,
            sources_used="NONE",
        )
        raise HTTPException(status_code=401, detail=reason)

    try:
        ingest_manual_if_empty()
        answer, sources = ask_rag(req.query)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama yerel yapay zeka servisine ulaşılamadı: {exc}",
        )

    sources_str = ", ".join(sources) if sources else "YOK"
    log_receipt = write_blackbox_log(
        operator_id=req.operator_id,
        action_type="RAG_QUERY_VERIFIED",
        query_text=req.query,
        response_summary=answer[:140],
        sources_used=sources_str,
    )

    return {
        "answer": answer,
        "sources": sources,
        "blackbox": log_receipt,
    }


@app.get("/api/logs")
def read_blackbox_logs():
    """[Özellik 3] Son değiştirilemez kara kutu kayıtlarını listeler."""
    return {"logs": get_recent_logs(limit=10)}