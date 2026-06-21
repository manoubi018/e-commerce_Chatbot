from pydantic import BaseModel
from typing import List, Dict, Any

class VisibleProduct(BaseModel):
    categorie: str
    nom_produit: str
    description_marketing: str


class BenchmarkResult(BaseModel):
    provider: str
    model: str
    time_sec: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    raw_output: Dict[str, Any]
