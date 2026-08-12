from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import (
    default_clickhouse_url_with_network_fallback,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    sql_string,
)
from research.mlops.env import load_env_files

from .embedding_supervision import (
    DATASET_VERSION,
    DEFAULT_DATA_ROOT,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_GOLD_PATH,
    DEFAULT_RUN_ROOT,
    MODEL_VERSION,
    OPENAI_MODEL_VERSION,
    SENTIMENT_LABELS,
    TFIDF_MODEL_VERSION,
    TFIDF_V2_MODEL_VERSION,
    TFIDF_V3_MODEL_VERSION,
    TFIDF_V4_MODEL_VERSION,
    TrainConfig,
    assert_runtime_path,
    build_supervision_arrays,
    canonical_json_sha256,
    class_weight_binary,
    class_weight_multiclass,
    class_weight_multilabel,
    config_dict,
    dataset_file_manifest,
    deterministic_stratified_split,
    evaluation_report,
    file_sha256,
    pool_embedding_chunks,
    read_jsonl,
    save_array,
    set_reproducible_seed,
    validate_prepared_dataset,
    write_json,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare, train, and evaluate a leakage-resistant multi-task News "
            "Synthesis classifier over durable Qwen article embeddings."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--gold-path", type=Path, default=DEFAULT_GOLD_PATH)
    prepare.add_argument("--output-root", type=Path, default=DEFAULT_DATA_ROOT)
    prepare.add_argument("--train-fraction", type=float, default=0.75)
    prepare.add_argument("--split-seed", default="news-synthesis-qwen-supervision-v1")
    prepare.add_argument("--split-candidates", type=int, default=256)
    prepare.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    prepare.add_argument("--embedding-dim", type=int, default=DEFAULT_EMBEDDING_DIM)
    prepare.add_argument("--clickhouse-url", default="")
    prepare.add_argument("--user", default="")
    prepare.add_argument("--password", default="")

    train = subparsers.add_parser("train")
    _training_arguments(train)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    evaluate.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    evaluate.add_argument("--device", default="auto")
    evaluate.add_argument("--batch-size", type=int, default=512)
    evaluate.add_argument("--torch-threads", type=int, default=0)
    all_command = subparsers.add_parser("all")
    all_command.add_argument("--gold-path", type=Path, default=DEFAULT_GOLD_PATH)
    all_command.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    all_command.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    all_command.add_argument("--train-fraction", type=float, default=0.75)
    all_command.add_argument("--split-seed", default="news-synthesis-qwen-supervision-v1")
    all_command.add_argument("--split-candidates", type=int, default=256)
    all_command.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    all_command.add_argument("--embedding-dim", type=int, default=DEFAULT_EMBEDDING_DIM)
    all_command.add_argument("--clickhouse-url", default="")
    all_command.add_argument("--user", default="")
    all_command.add_argument("--password", default="")
    _training_arguments(all_command, include_paths=False)
    args = parser.parse_args()
    if args.command == "prepare":
        report = prepare_dataset(args)
    elif args.command == "train":
        report = train_model(args)
    elif args.command == "evaluate":
        report = evaluate_checkpoint(args)
    else:
        prepare_args = argparse.Namespace(
            gold_path=args.gold_path,
            output_root=args.data_root,
            train_fraction=args.train_fraction,
            split_seed=args.split_seed,
            split_candidates=args.split_candidates,
            embedding_model=args.embedding_model,
            embedding_dim=args.embedding_dim,
            clickhouse_url=args.clickhouse_url,
            user=args.user,
            password=args.password,
        )
        prepare_report = prepare_dataset(prepare_args)
        train_args = argparse.Namespace(**vars(args))
        train_report = train_model(train_args)
        report = {"prepare": prepare_report, "train": train_report}
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


def _training_arguments(parser: argparse.ArgumentParser, *, include_paths: bool = True) -> None:
    if include_paths:
        parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
        parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--hidden-dim", type=int, default=384)
    parser.add_argument("--residual-blocks", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.20)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=8.0e-4)
    parser.add_argument("--weight-decay", type=float, default=1.0e-4)
    parser.add_argument("--max-epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=7)
    parser.add_argument("--min-delta", type=float, default=1.0e-4)
    parser.add_argument("--tuning-fraction", type=float, default=0.10)
    parser.add_argument("--sentiment-loss-weight", type=float, default=0.75)
    parser.add_argument("--concept-loss-weight", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--torch-threads", type=int, default=0)
    parser.add_argument("--device", default="auto")


def prepare_dataset(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    output_root = assert_runtime_path(Path(args.output_root))
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite prepared dataset: {output_root}")
    gold_path = Path(args.gold_path).resolve()
    gold_rows = read_jsonl(gold_path)
    if not gold_rows:
        raise RuntimeError("Gold authority is empty")
    gold_ids = {str(row["source_id"]) for row in gold_rows}
    client = _clickhouse_client(args)
    embedding_rows = _embedding_rows(
        client,
        sorted(gold_ids),
        model=str(args.embedding_model),
    )
    article_vectors, issuer_vectors, embedding_report = pool_embedding_chunks(
        embedding_rows, embedding_dim=int(args.embedding_dim)
    )
    if not article_vectors:
        raise RuntimeError("No Qwen embeddings matched the gold source IDs")
    available_gold_rows = [
        row for row in gold_rows if str(row["source_id"]) in article_vectors
    ]
    assignments, split_report = deterministic_stratified_split(
        available_gold_rows,
        train_fraction=float(args.train_fraction),
        seed=str(args.split_seed),
        candidate_count=int(args.split_candidates),
    )
    arrays = build_supervision_arrays(
        available_gold_rows, article_vectors, issuer_vectors, assignments
    )
    available_sources = {row["source_id"] for row in arrays["article_metadata"]}
    available_split_report = {
        "articles": len(available_sources),
        "train_articles": sum(
            assignments[source_id] == "train" for source_id in available_sources
        ),
        "validation_articles": sum(
            assignments[source_id] == "validation" for source_id in available_sources
        ),
    }
    output_root.mkdir(parents=True)
    save_array(output_root / "article_embeddings.npy", arrays["article_embeddings"])
    save_array(output_root / "article_eligibility.npy", arrays["article_eligibility"])
    save_array(output_root / "issuer_embeddings.npy", arrays["issuer_embeddings"])
    save_array(output_root / "issuer_eligibility.npy", arrays["issuer_eligibility"])
    save_array(output_root / "issuer_sentiment.npy", arrays["issuer_sentiment"])
    save_array(output_root / "issuer_concepts.npy", arrays["issuer_concepts"])
    write_jsonl(output_root / "article_metadata.jsonl", arrays["article_metadata"])
    write_jsonl(output_root / "issuer_metadata.jsonl", arrays["issuer_metadata"])
    write_json(
        output_root / "label_contract.json",
        {
            "article_forecast_eligibility": ["ineligible", "eligible"],
            "issuer_forecast_eligibility": ["ineligible", "eligible"],
            "issuer_sentiment": list(SENTIMENT_LABELS),
            "issuer_concepts": list(arrays["concept_labels"]),
            "unknown_sentiment_mask": -1,
        },
    )
    file_names = (
        "article_embeddings.npy",
        "article_eligibility.npy",
        "issuer_embeddings.npy",
        "issuer_eligibility.npy",
        "issuer_sentiment.npy",
        "issuer_concepts.npy",
        "article_metadata.jsonl",
        "issuer_metadata.jsonl",
        "label_contract.json",
    )
    manifest = {
        "version": DATASET_VERSION,
        "status": "complete",
        "gold_authority": {
            "path": str(gold_path),
            "rows": len(gold_rows),
            "sha256": file_sha256(gold_path),
        },
        "embedding_authority": {
            "database": "market_sip_compact",
            "table": "news_text_embeddings",
            "model": str(args.embedding_model),
            "embedding_dim": int(args.embedding_dim),
            **embedding_report,
        },
        "split": split_report,
        "available_split": available_split_report,
        "supervision": {
            "article_samples": len(arrays["article_embeddings"]),
            "issuer_samples": len(arrays["issuer_embeddings"]),
            "concept_labels": len(arrays["concept_labels"]),
            "sentiment_scored_samples": int(np.sum(arrays["issuer_sentiment"] >= 0)),
            "unmatched_issuer_units": arrays["unmatched_issuer_units"],
        },
        "files": dataset_file_manifest(output_root, file_names),
        "elapsed_seconds": time.perf_counter() - started,
    }
    manifest["contract_sha256"] = canonical_json_sha256(
        {key: value for key, value in manifest.items() if key != "elapsed_seconds"}
    )
    write_json(output_root / "manifest.json", manifest)
    validation = validate_prepared_dataset(output_root)
    write_json(output_root / "VALIDATION.json", validation)
    return {"manifest": manifest, "validation": validation}


def _clickhouse_client(args: argparse.Namespace) -> ClickHouseHttpClient:
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    return ClickHouseHttpClient(
        str(args.clickhouse_url or default_clickhouse_url_with_network_fallback()),
        str(args.user or default_clickhouse_user()),
        str(args.password or default_clickhouse_password()),
        timeout_seconds=240,
        default_query_params={"max_query_size": 3_000_000, "max_execution_time": 240},
    )


def _embedding_rows(
    client: ClickHouseHttpClient,
    source_ids: list[str],
    *,
    model: str,
    source_batch_size: int = 500,
) -> Iterator[dict[str, Any]]:
    if source_batch_size <= 0:
        raise ValueError("source_batch_size must be positive")
    total_rows = 0
    for start in range(0, len(source_ids), source_batch_size):
        batch = source_ids[start : start + source_batch_size]
        source_sql = ",".join(sql_string(source_id) for source_id in batch)
        query = f"""
SELECT source_id, ticker, token_chunk_index, embedding
FROM market_sip_compact.news_text_embeddings FINAL
WHERE source_id IN ({source_sql})
  AND embedding_model = {sql_string(model)}
ORDER BY source_id, ticker, token_chunk_index
FORMAT JSONEachRow
"""
        batch_rows = 0
        for row in client.iter_json_each_row(query):
            batch_rows += 1
            total_rows += 1
            yield row
        print(
            json.dumps(
                {
                    "stage": "embedding_read",
                    "source_ids_completed": min(start + len(batch), len(source_ids)),
                    "source_ids_total": len(source_ids),
                    "batch_embedding_rows": batch_rows,
                    "embedding_rows_total": total_rows,
                },
                sort_keys=True,
            ),
            flush=True,
        )


def _torch_imports():
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, Dataset

    return torch, nn, DataLoader, Dataset


def _model_class():
    torch, nn, _, _ = _torch_imports()

    class ResidualBlock(nn.Module):
        def __init__(self, hidden_dim: int, dropout: float) -> None:
            super().__init__()
            self.network = nn.Sequential(
                nn.LayerNorm(hidden_dim),
                nn.Linear(hidden_dim, hidden_dim * 2),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim * 2, hidden_dim),
                nn.Dropout(dropout),
            )

        def forward(self, value):
            return value + self.network(value)

    class NewsSynthesisEmbeddingModel(nn.Module):
        def __init__(
            self,
            *,
            input_dim: int,
            hidden_dim: int,
            residual_blocks: int,
            dropout: float,
            concept_count: int,
        ) -> None:
            super().__init__()
            self.encoder = nn.Sequential(
                nn.LayerNorm(input_dim),
                nn.Linear(input_dim, hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
                *[ResidualBlock(hidden_dim, dropout) for _ in range(residual_blocks)],
                nn.LayerNorm(hidden_dim),
            )
            self.article_eligibility = nn.Linear(hidden_dim, 1)
            self.issuer_eligibility = nn.Linear(hidden_dim, 1)
            self.sentiment = nn.Linear(hidden_dim, len(SENTIMENT_LABELS))
            self.concepts = nn.Linear(hidden_dim, concept_count)

        def forward(self, value):
            encoded = self.encoder(value)
            return {
                "article_eligibility": self.article_eligibility(encoded).squeeze(-1),
                "issuer_eligibility": self.issuer_eligibility(encoded).squeeze(-1),
                "sentiment": self.sentiment(encoded),
                "concepts": self.concepts(encoded),
            }

    return NewsSynthesisEmbeddingModel


def _array_dataset_class():
    torch, _, _, Dataset = _torch_imports()

    class ArrayDataset(Dataset):
        def __init__(self, *arrays: np.ndarray) -> None:
            self.arrays = arrays

        def __len__(self) -> int:
            return len(self.arrays[0])

        def __getitem__(self, index: int):
            return tuple(torch.from_numpy(np.asarray(value[index])) for value in self.arrays)

    return ArrayDataset


def _device(value: str):
    torch, _, _, _ = _torch_imports()
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA device requested but unavailable")
    return device


def _split_indexes(metadata: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    train = np.asarray([index for index, row in enumerate(metadata) if row["split"] == "train"], dtype=np.int64)
    validation = np.asarray([index for index, row in enumerate(metadata) if row["split"] == "validation"], dtype=np.int64)
    return train, validation


def _fit_tuning_indexes(
    article_metadata: list[dict[str, Any]],
    issuer_metadata: list[dict[str, Any]],
    *,
    seed: int,
    tuning_fraction: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if not 0.0 < tuning_fraction < 0.5:
        raise ValueError("tuning_fraction must be between zero and 0.5")
    training_sources = sorted(
        str(row["source_id"])
        for row in article_metadata
        if row["split"] == "train"
    )
    tuning_size = max(1, int(round(len(training_sources) * tuning_fraction)))
    ranked = sorted(
        training_sources,
        key=lambda source_id: hashlib.sha256(
            f"{seed}:tuning:{source_id}".encode("utf-8")
        ).digest(),
    )
    tuning_sources = set(ranked[:tuning_size])
    article_fit = np.asarray(
        [
            index
            for index, row in enumerate(article_metadata)
            if row["split"] == "train" and row["source_id"] not in tuning_sources
        ],
        dtype=np.int64,
    )
    article_tuning = np.asarray(
        [
            index
            for index, row in enumerate(article_metadata)
            if row["source_id"] in tuning_sources
        ],
        dtype=np.int64,
    )
    issuer_fit = np.asarray(
        [
            index
            for index, row in enumerate(issuer_metadata)
            if row["split"] == "train" and row["source_id"] not in tuning_sources
        ],
        dtype=np.int64,
    )
    issuer_tuning = np.asarray(
        [
            index
            for index, row in enumerate(issuer_metadata)
            if row["source_id"] in tuning_sources
        ],
        dtype=np.int64,
    )
    if not all(len(values) for values in (article_fit, article_tuning, issuer_fit, issuer_tuning)):
        raise RuntimeError("Fit/tuning partition produced an empty sample set")
    return article_fit, article_tuning, issuer_fit, issuer_tuning


def _model_version_for_dataset(data_root: Path) -> str:
    manifest = json.loads((data_root / "manifest.json").read_text(encoding="utf-8"))
    representation = str((manifest.get("representation") or {}).get("kind") or "qwen")
    if representation == "qwen":
        return MODEL_VERSION
    if representation == "tfidf":
        return TFIDF_MODEL_VERSION
    if representation == "tfidf_v2":
        return TFIDF_V2_MODEL_VERSION
    if representation == "tfidf_v3":
        return TFIDF_V3_MODEL_VERSION
    if representation == "tfidf_v4":
        return TFIDF_V4_MODEL_VERSION
    if representation == "openai":
        return OPENAI_MODEL_VERSION
    raise RuntimeError(f"Unsupported supervision representation: {representation}")


def train_model(args: argparse.Namespace) -> dict[str, Any]:
    torch, nn, DataLoader, _ = _torch_imports()
    config = TrainConfig(
        seed=int(args.seed),
        hidden_dim=int(args.hidden_dim),
        residual_blocks=int(args.residual_blocks),
        dropout=float(args.dropout),
        batch_size=int(args.batch_size),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        max_epochs=int(args.max_epochs),
        patience=int(args.patience),
        min_delta=float(args.min_delta),
        tuning_fraction=float(args.tuning_fraction),
        sentiment_loss_weight=float(args.sentiment_loss_weight),
        concept_loss_weight=float(args.concept_loss_weight),
        workers=int(args.workers),
        torch_threads=int(args.torch_threads),
    )
    set_reproducible_seed(config.seed)
    if config.torch_threads > 0:
        torch.set_num_threads(config.torch_threads)
    data_root = assert_runtime_path(Path(args.data_root))
    run_root = assert_runtime_path(Path(args.run_root))
    if run_root.exists():
        raise RuntimeError(f"Refusing to overwrite training run: {run_root}")
    validation = validate_prepared_dataset(data_root)
    model_version = _model_version_for_dataset(data_root)
    arrays, metadata, contract = _load_dataset(data_root)
    _, article_validation = _split_indexes(metadata["article"])
    _, issuer_validation = _split_indexes(metadata["issuer"])
    article_train, article_tuning, issuer_train, issuer_tuning = _fit_tuning_indexes(
        metadata["article"],
        metadata["issuer"],
        seed=config.seed,
        tuning_fraction=config.tuning_fraction,
    )
    ArrayDataset = _array_dataset_class()
    generator = torch.Generator().manual_seed(config.seed)
    article_loader = DataLoader(
        ArrayDataset(
            arrays["article_x"][article_train], arrays["article_y"][article_train]
        ),
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=config.workers,
    )
    issuer_loader = DataLoader(
        ArrayDataset(
            arrays["issuer_x"][issuer_train],
            arrays["issuer_y"][issuer_train],
            arrays["sentiment_y"][issuer_train],
            arrays["concept_y"][issuer_train],
        ),
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=config.workers,
    )
    Model = _model_class()
    model = Model(
        input_dim=int(arrays["article_x"].shape[1]),
        hidden_dim=config.hidden_dim,
        residual_blocks=config.residual_blocks,
        dropout=config.dropout,
        concept_count=len(contract["issuer_concepts"]),
    )
    device = _device(str(args.device))
    model.to(device)
    article_loss = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(
            class_weight_binary(arrays["article_y"][article_train]), device=device
        )
    )
    issuer_loss = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(
            class_weight_binary(arrays["issuer_y"][issuer_train]), device=device
        )
    )
    sentiment_loss = nn.CrossEntropyLoss(
        weight=torch.tensor(
            class_weight_multiclass(
                arrays["sentiment_y"][issuer_train], len(SENTIMENT_LABELS)
            ),
            device=device,
        )
    )
    concept_loss = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(
            class_weight_multilabel(arrays["concept_y"][issuer_train]), device=device
        )
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=2, min_lr=1.0e-6
    )
    run_root.mkdir(parents=True)
    best_score = -math.inf
    best_epoch = 0
    epochs_without_improvement = 0
    history: list[dict[str, Any]] = []
    started = time.perf_counter()
    for epoch in range(1, config.max_epochs + 1):
        model.train()
        losses = []
        for embedding, target in article_loader:
            embedding = embedding.to(device=device, dtype=torch.float32)
            target = target.to(device=device, dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            loss = article_loss(model(embedding)["article_eligibility"], target)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        for embedding, eligibility, sentiment, concepts in issuer_loader:
            embedding = embedding.to(device=device, dtype=torch.float32)
            eligibility = eligibility.to(device=device, dtype=torch.float32)
            sentiment = sentiment.to(device=device, dtype=torch.long)
            concepts = concepts.to(device=device, dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            output = model(embedding)
            loss = issuer_loss(output["issuer_eligibility"], eligibility)
            mask = sentiment >= 0
            if bool(mask.any()):
                loss = loss + config.sentiment_loss_weight * sentiment_loss(
                    output["sentiment"][mask], sentiment[mask]
                )
            loss = loss + config.concept_loss_weight * concept_loss(
                output["concepts"], concepts
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        report = _evaluate_model(
            model,
            arrays,
            article_tuning,
            issuer_tuning,
            contract,
            device=device,
            batch_size=max(config.batch_size, 512),
        )
        score = float(report["selection_score"])
        scheduler.step(score)
        improved = score > best_score + config.min_delta
        if improved:
            best_score = score
            best_epoch = epoch
            epochs_without_improvement = 0
            _save_checkpoint(
                run_root / "best_model.pt",
                model,
                config,
                contract,
                data_root,
                epoch,
                score,
                model_version,
            )
        else:
            epochs_without_improvement += 1
        epoch_row = {
            "epoch": epoch,
            "train_loss": float(np.mean(losses)),
            "selection_score": score,
            "learning_rate": float(optimizer.param_groups[0]["lr"]),
            "improved": improved,
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(epoch_row)
        print(json.dumps(epoch_row, sort_keys=True), flush=True)
        write_json(run_root / "history.json", history)
        if epochs_without_improvement >= config.patience:
            break
    checkpoint = torch.load(run_root / "best_model.pt", map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["state_dict"])
    final_report = _evaluate_model(
        model,
        arrays,
        article_validation,
        issuer_validation,
        contract,
        device=device,
        batch_size=max(config.batch_size, 512),
    )
    final_report.update(
        {
            "version": model_version,
            "status": "complete",
            "best_epoch": best_epoch,
            "epochs_run": len(history),
            "config": config_dict(config),
            "device": str(device),
            "dataset_validation": validation,
            "data_manifest_sha256": file_sha256(data_root / "manifest.json"),
            "checkpoint_sha256": file_sha256(run_root / "best_model.pt"),
            "elapsed_seconds": time.perf_counter() - started,
            "evaluation_boundary": (
                "Best epoch selected only on a grouped tuning slice inside the 75% "
                "training partition. Final metrics were computed once on the untouched "
                "25% validation partition."
            ),
            "partition_counts": {
                "fit_articles": len(article_train),
                "tuning_articles": len(article_tuning),
                "validation_articles": len(article_validation),
                "fit_issuer_units": len(issuer_train),
                "tuning_issuer_units": len(issuer_tuning),
                "validation_issuer_units": len(issuer_validation),
            },
        }
    )
    write_json(run_root / "evaluation.json", final_report)
    return final_report


def _load_dataset(root: Path):
    arrays = {
        "article_x": np.load(root / "article_embeddings.npy"),
        "article_y": np.load(root / "article_eligibility.npy"),
        "issuer_x": np.load(root / "issuer_embeddings.npy"),
        "issuer_y": np.load(root / "issuer_eligibility.npy"),
        "sentiment_y": np.load(root / "issuer_sentiment.npy"),
        "concept_y": np.load(root / "issuer_concepts.npy"),
    }
    metadata = {
        "article": read_jsonl(root / "article_metadata.jsonl"),
        "issuer": read_jsonl(root / "issuer_metadata.jsonl"),
    }
    contract = json.loads((root / "label_contract.json").read_text(encoding="utf-8"))
    return arrays, metadata, contract


def _save_checkpoint(
    path: Path,
    model,
    config: TrainConfig,
    contract: Mapping[str, Any],
    data_root: Path,
    epoch: int,
    score: float,
    model_version: str,
) -> None:
    torch, _, _, _ = _torch_imports()
    temporary = path.with_name(f".{path.name}.tmp")
    torch.save(
        {
            "version": model_version,
            "state_dict": model.state_dict(),
            "config": config_dict(config),
            "label_contract": dict(contract),
            "data_manifest_sha256": file_sha256(data_root / "manifest.json"),
            "epoch": epoch,
            "selection_score": score,
        },
        temporary,
    )
    os.replace(temporary, path)


def _predict(model, values: np.ndarray, *, device, batch_size: int):
    torch, _, DataLoader, _ = _torch_imports()
    ArrayDataset = _array_dataset_class()
    loader = DataLoader(ArrayDataset(values), batch_size=batch_size, shuffle=False)
    output: dict[str, list[np.ndarray]] = defaultdict(list)
    model.eval()
    with torch.no_grad():
        for (embedding,) in loader:
            result = model(embedding.to(device=device, dtype=torch.float32))
            for name, tensor in result.items():
                output[name].append(tensor.detach().cpu().numpy())
    return {name: np.concatenate(chunks, axis=0) for name, chunks in output.items()}


def _evaluate_model(
    model,
    arrays: Mapping[str, np.ndarray],
    article_indexes: np.ndarray,
    issuer_indexes: np.ndarray,
    contract: Mapping[str, Any],
    *,
    device,
    batch_size: int,
) -> dict[str, Any]:
    article = _predict(
        model, arrays["article_x"][article_indexes], device=device, batch_size=batch_size
    )
    issuer = _predict(
        model, arrays["issuer_x"][issuer_indexes], device=device, batch_size=batch_size
    )
    return evaluation_report(
        article_truth=arrays["article_y"][article_indexes],
        article_logits=article["article_eligibility"],
        issuer_eligibility_truth=arrays["issuer_y"][issuer_indexes],
        issuer_eligibility_logits=issuer["issuer_eligibility"],
        sentiment_truth=arrays["sentiment_y"][issuer_indexes],
        sentiment_logits=issuer["sentiment"],
        concept_truth=arrays["concept_y"][issuer_indexes],
        concept_logits=issuer["concepts"],
        concept_labels=contract["issuer_concepts"],
    )


def evaluate_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    torch, _, _, _ = _torch_imports()
    data_root = assert_runtime_path(Path(args.data_root))
    run_root = assert_runtime_path(Path(args.run_root))
    validate_prepared_dataset(data_root)
    arrays, metadata, contract = _load_dataset(data_root)
    checkpoint = torch.load(run_root / "best_model.pt", map_location="cpu", weights_only=False)
    expected_model_version = _model_version_for_dataset(data_root)
    if checkpoint.get("version") != expected_model_version:
        raise RuntimeError("Checkpoint model version mismatch")
    if checkpoint.get("data_manifest_sha256") != file_sha256(data_root / "manifest.json"):
        raise RuntimeError("Checkpoint was trained against a different prepared dataset")
    config = TrainConfig(**checkpoint["config"])
    if int(args.torch_threads) > 0:
        torch.set_num_threads(int(args.torch_threads))
    device = _device(str(args.device))
    Model = _model_class()
    model = Model(
        input_dim=arrays["article_x"].shape[1],
        hidden_dim=config.hidden_dim,
        residual_blocks=config.residual_blocks,
        dropout=config.dropout,
        concept_count=len(contract["issuer_concepts"]),
    )
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device)
    _, article_validation = _split_indexes(metadata["article"])
    _, issuer_validation = _split_indexes(metadata["issuer"])
    report = _evaluate_model(
        model,
        arrays,
        article_validation,
        issuer_validation,
        contract,
        device=device,
        batch_size=int(args.batch_size),
    )
    report.update(
        {
            "version": expected_model_version,
            "checkpoint_sha256": file_sha256(run_root / "best_model.pt"),
            "best_epoch": int(checkpoint["epoch"]),
            "device": str(device),
        }
    )
    write_json(run_root / "evaluation_reproduced.json", report)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
