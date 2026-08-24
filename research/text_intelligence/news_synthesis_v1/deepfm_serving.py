from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import torch
from scipy import sparse

from .provider_filter_analysis import parse_utc, session_date, session_segment, text_flags
from .structured_metadata_rf import _build_matrix
from .structured_tfidf_deepfm_pre_holdout import SparseDeepFM


SERVING_CONTRACT_VERSION = "news_forecast_deepfm_serving_v1"
NEW_YORK = ZoneInfo("America/New_York")


class DeepFMServingRelease:
    """Hash-pinned inference wrapper for the frozen structured plus TF-IDF model."""

    def __init__(self, manifest_path: Path, *, device: str = "cpu") -> None:
        self.manifest_path = manifest_path
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("contract_version") != SERVING_CONTRACT_VERSION:
            raise ValueError("unsupported DeepFM serving contract")
        if self.manifest.get("status") != "promoted":
            raise ValueError("DeepFM release is not promoted")
        self.threshold = float(self.manifest["threshold"])
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("DeepFM threshold must be inside (0,1)")
        self.device = torch.device(device)
        artifacts = self.manifest["artifacts"]
        resolved: dict[str, Path] = {}
        for name, item in artifacts.items():
            path = Path(str(item["path"]))
            if not path.is_file():
                raise FileNotFoundError(path)
            digest = _sha256(path)
            if digest != str(item["sha256"]):
                raise ValueError(f"DeepFM release hash mismatch: {name}")
            resolved[name] = path
        self.release_id = str(self.manifest["release_id"])
        self.release_hash = _sha256(manifest_path)
        self.contract = json.loads(resolved["feature_contract"].read_text(encoding="utf-8"))
        self.feature_names = list(map(str, self.contract["feature_names"]))
        self.feature_index = {name: index for index, name in enumerate(self.feature_names)}
        self.active = {
            str(family): set(map(str, values))
            for family, values in self.contract["active_categories"].items()
        }
        historical: dict[str, set[str]] = defaultdict(set)
        with resolved["category_catalog"].open("r", encoding="utf-8", newline="") as handle:
            for item in csv.DictReader(handle):
                historical[str(item["family"])].add(str(item["category"]))
        self.historical = historical
        self.vectorizer = joblib.load(resolved["tfidf_vectorizer"])
        self.scale = np.load(resolved["column_scale"], allow_pickle=False)
        checkpoint = torch.load(resolved["model"], map_location="cpu", weights_only=True)
        if int(checkpoint["input_dim"]) != len(self.scale):
            raise ValueError("DeepFM checkpoint and scale dimensions differ")
        if len(self.feature_names) + len(self.vectorizer.get_feature_names_out()) != len(self.scale):
            raise ValueError("DeepFM feature authorities do not match checkpoint dimensions")
        self.model = SparseDeepFM(int(checkpoint["input_dim"]))
        self.model.load_state_dict(checkpoint["state_dict"], strict=True)
        self.model.to(self.device).eval()

    def score(
        self,
        source_row: Mapping[str, Any],
        *,
        ticker_history: Mapping[str, Any] | None = None,
        market_cap: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = serving_feature_row(source_row, ticker_history=ticker_history)
        cap = dict(market_cap or {})
        structured = _build_matrix(
            [row], {str(row["source_id"]): cap}, self.feature_index,
            self.active, self.historical,
        )
        text = self.vectorizer.transform([str(source_row.get("text") or source_row.get("title") or "")])
        combined = sparse.hstack((structured, text), format="csr", dtype=np.float32)
        combined = combined.multiply((1.0 / self.scale).astype(np.float32)).tocsr()
        probability = self._probability(combined)
        return {
            "contract_version": SERVING_CONTRACT_VERSION,
            "release_id": self.release_id,
            "release_hash": self.release_hash,
            "threshold": self.threshold,
            "eligible_probability": probability,
            "forecast_eligibility": "eligible" if probability >= self.threshold else "ineligible",
        }

    def _probability(self, matrix: sparse.csr_matrix) -> float:
        coo = matrix.tocoo()
        indices = torch.tensor(np.vstack((coo.row, coo.col)), dtype=torch.int64, device=self.device)
        values = torch.tensor(coo.data, dtype=torch.float32, device=self.device)
        tensor = torch.sparse_coo_tensor(indices, values, size=coo.shape, device=self.device).to_sparse_csr()
        with torch.inference_mode():
            return float(torch.sigmoid(self.model(tensor))[0].cpu())


def serving_feature_row(
    source_row: Mapping[str, Any], *, ticker_history: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    published = parse_utc(str(source_row["source_timestamp"]))
    published_et = published.astimezone(NEW_YORK)
    tickers = tuple(sorted({str(value).strip().upper() for value in source_row.get("tickers") or () if str(value).strip()}))
    history = dict(ticker_history or {})
    text = str(source_row.get("text") or source_row.get("title") or "")
    return {
        "source_id": str(source_row["source_id"]),
        "published_at_text": published.isoformat(),
        "provider": str(source_row.get("provider") or "").strip().casefold(),
        "provider_tags": tuple(str(value).strip().casefold() for value in source_row.get("provider_tags") or () if str(value).strip()),
        "channels": tuple(str(value).strip().casefold() for value in source_row.get("channels") or () if str(value).strip()),
        "content_quality_flags": tuple(str(value).strip().casefold() for value in source_row.get("content_quality_flags") or () if str(value).strip()),
        "ticker_count": len(tickers),
        "rendered_chars": len(text),
        "session_segment": session_segment(published),
        "session_date": session_date(published),
        "hour_et": history.get("hour_et", published_et.hour),
        "weekday_et": history.get("weekday_et", published_et.strftime("%a").casefold()),
        "min_ticker_session_ordinal": history.get("min_ticker_session_ordinal"),
        "max_ticker_session_ordinal": history.get("max_ticker_session_ordinal"),
        "min_seconds_since_previous_ticker_news": history.get("min_seconds_since_previous_ticker_news"),
        "max_seconds_since_previous_ticker_news": history.get("max_seconds_since_previous_ticker_news"),
        "any_ticker_first_session": bool(history.get("any_ticker_first_session")),
        "all_tickers_first_session": bool(history.get("all_tickers_first_session")),
        "any_ticker_news_within_5m": bool(history.get("any_ticker_news_within_5m")),
        "any_ticker_news_within_30m": bool(history.get("any_ticker_news_within_30m")),
        **text_flags(text),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
