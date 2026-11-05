import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import db, jobs
from .config import ROOT, settings
from .services import research, shorts, viral
from .services.render import to_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Search Viral", lifespan=lifespan)
APP_DIR = ROOT / "app"
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

PERIODS = {"day", "week", "month", "year", "any"}


class ViralRequest(BaseModel):
    niche: str = Field(min_length=2, max_length=100)
    keywords: str = Field(default="", max_length=150)
    platforms: list[str] = Field(default_factory=lambda: ["youtube"])
    period: str = "month"
    limit: int = Field(default=30, ge=5, le=80)
    use_ai: bool = False


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    depth: int = Field(default=8, ge=3, le=20)
    use_ai: bool = False
    include_videos: bool = True


class ShortsRequest(BaseModel):
    niche: str = Field(min_length=2, max_length=100)
    keywords: str = Field(default="", max_length=150)
    period: str = "month"
    limit: int = Field(default=30, ge=5, le=120)
    use_ai: bool = False


class XrayRequest(BaseModel):
    url: str = Field(min_length=10, max_length=300)
    benchmark_id: int | None = None
    limit: int = Field(default=20, ge=5, le=60)  # usado quando o link é de um canal
    use_ai: bool = False


def _shorts_job(req: ShortsRequest, progress) -> int:
    result = shorts.run_niche(req.niche.strip(), req.keywords.strip(), req.period, req.limit, req.use_ai, progress)
    return db.save_search("shorts", req.niche.strip(), result["params"], result)


def _xray_job(req: XrayRequest, progress) -> int:
    result = shorts.run_xray(req.url.strip(), req.benchmark_id, req.use_ai, req.limit, progress)
    if result["params"]["mode"] == "channel":
        return db.save_search("shorts", f"Canal: {result['params']['niche']}", result["params"], result)
    return db.save_search("short_xray", result["title"][:120] or req.url, result["params"], result)


def _viral_job(req: ViralRequest, progress) -> int:
    result = viral.run(req.niche.strip(), req.keywords.strip(), req.platforms, req.period, req.limit, req.use_ai, progress)
    return db.save_search("viral", req.niche.strip(), result["params"], result)


def _research_job(req: ResearchRequest, progress) -> int:
    result = research.run(req.topic.strip(), req.depth, req.use_ai, req.include_videos, progress)
    return db.save_search("research", req.topic.strip(), result["params"], result)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/status")
def status():
    return {"ai": settings.ai_enabled, "ai_model": settings.ai_model if settings.ai_enabled else None, "apify": settings.apify_enabled}


@app.post("/api/viral")
def start_viral(req: ViralRequest):
    req.platforms = [p for p in req.platforms if p in {"youtube", "instagram", "twitter"}] or ["youtube"]
    if req.period not in PERIODS:
        raise HTTPException(400, "Período inválido")
    return {"job_id": jobs.submit(_viral_job, req)}


@app.post("/api/research")
def start_research(req: ResearchRequest):
    return {"job_id": jobs.submit(_research_job, req)}


@app.post("/api/shorts")
def start_shorts(req: ShortsRequest):
    if req.period not in PERIODS:
        raise HTTPException(400, "Período inválido")
    req.limit = min(req.limit, settings.shorts_limit_max)
    return {"job_id": jobs.submit(_shorts_job, req)}


@app.post("/api/shorts/xray")
def start_xray(req: XrayRequest):
    url = req.url.strip()
    if not re.match(r"^https?://([\w-]+\.)?(youtube\.com|youtu\.be)/", url, re.IGNORECASE):
        raise HTTPException(400, "Use um link do YouTube (Short, vídeo ou canal).")
    return {"job_id": jobs.submit(_xray_job, req)}


@app.post("/api/searches/{search_id}/retry-speech")
def retry_speech(search_id: int):
    s = db.get_search(search_id)
    if not s or s["kind"] not in ("shorts", "short_xray"):
        raise HTTPException(404, "Estudo de Shorts não encontrado")
    return {"job_id": jobs.submit(shorts.retry_speech, search_id)}


@app.get("/api/shorts/benchmarks")
def shorts_benchmarks():
    """Estudos de Shorts salvos, para comparar no raio-X."""
    return [s for s in db.list_searches(100) if s["kind"] == "shorts"]


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return {k: job[k] for k in ("id", "status", "progress", "search_id", "error")}


@app.get("/api/history")
def history():
    return db.list_searches()


@app.get("/api/searches/{search_id}")
def get_search(search_id: int):
    s = db.get_search(search_id)
    if not s:
        raise HTTPException(404, "Busca não encontrada")
    s["report_html"] = to_html(s["result"].get("report_md", ""))
    return s


@app.delete("/api/searches/{search_id}")
def delete_search(search_id: int):
    db.delete_search(search_id)
    return {"ok": True}


@app.get("/api/searches/{search_id}/report.md")
def download_report(search_id: int):
    s = db.get_search(search_id)
    if not s:
        raise HTTPException(404, "Busca não encontrada")
    slug = re.sub(r"[^a-z0-9]+", "-", s["query"].lower()).strip("-")[:50] or "relatorio"
    return PlainTextResponse(
        s["result"].get("report_md", ""),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{s["kind"]}-{slug}-{search_id}.md"'},
    )
