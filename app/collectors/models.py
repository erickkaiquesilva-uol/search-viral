from dataclasses import asdict, dataclass, field, fields
from datetime import datetime


@dataclass
class Post:
    """Formato comum para um vídeo/post de qualquer plataforma."""

    platform: str  # youtube | instagram | twitter
    id: str
    url: str
    title: str = ""
    text: str = ""  # descrição / legenda / texto do tweet
    author: str = ""
    author_url: str = ""
    author_followers: int | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    duration: float | None = None  # segundos
    published_at: datetime | None = None
    thumbnail: str = ""
    hashtags: list[str] = field(default_factory=list)
    opening: str = ""  # primeiros segundos falados (legenda), quando disponível
    transcript: list = field(default_factory=list)  # [[segundo, palavra], ...] (só no modo Shorts)
    is_video: bool = True

    # Preenchidos pela análise
    metrics: dict = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)
    format: str = ""

    @property
    def headline(self) -> str:
        """Texto que funciona como 'título' (no Instagram/X é a 1ª linha da legenda)."""
        if self.title:
            return self.title
        first = (self.text or "").strip().splitlines()
        return first[0][:200] if first else ""

    @classmethod
    def from_dict(cls, d: dict) -> "Post":
        """Reconstrói um Post salvo no histórico (inverso de to_dict)."""
        names = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in names}
        if data.get("published_at"):
            data["published_at"] = datetime.fromisoformat(data["published_at"])
        return cls(**data)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        d["headline"] = self.headline
        return d
