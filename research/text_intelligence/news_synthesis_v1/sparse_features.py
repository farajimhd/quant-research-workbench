from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class CSRFeatureRow:
    indices: np.ndarray
    values: np.ndarray
    columns: int

    def toarray(self) -> np.ndarray:
        result = np.zeros((1, self.columns), dtype=np.float32)
        result[0, self.indices] = self.values
        return result


class CSRFeatureMatrix:
    def __init__(
        self,
        *,
        data: np.ndarray,
        indices: np.ndarray,
        indptr: np.ndarray,
        shape: tuple[int, int],
    ) -> None:
        self.data = np.asarray(data, dtype=np.float32)
        self.indices = np.asarray(indices, dtype=np.int32)
        self.indptr = np.asarray(indptr, dtype=np.int64)
        self.shape = tuple(int(value) for value in shape)
        if self.indptr.shape != (self.shape[0] + 1,):
            raise ValueError("CSR indptr length disagrees with row count")
        if len(self.data) != len(self.indices) or int(self.indptr[-1]) != len(self.data):
            raise ValueError("CSR data, indices, and indptr disagree")

    def __len__(self) -> int:
        return self.shape[0]

    def row(self, index: int) -> CSRFeatureRow:
        start, end = int(self.indptr[index]), int(self.indptr[index + 1])
        return CSRFeatureRow(self.indices[start:end], self.data[start:end], self.shape[1])

    def __getitem__(self, value):
        if np.isscalar(value):
            return self.row(int(value))
        indexes = np.arange(self.shape[0], dtype=np.int64)[value]
        return csr_from_rows(
            [(self.row(int(index)).indices, self.row(int(index)).values) for index in indexes],
            columns=self.shape[1],
        )


def csr_from_rows(
    rows: Sequence[tuple[np.ndarray, np.ndarray]], *, columns: int
) -> CSRFeatureMatrix:
    data: list[np.ndarray] = []
    indices: list[np.ndarray] = []
    indptr = [0]
    for row_indices, row_values in rows:
        row_indices = np.asarray(row_indices, dtype=np.int32)
        row_values = np.asarray(row_values, dtype=np.float32)
        if len(row_indices) != len(row_values):
            raise ValueError("Sparse row indices and values disagree")
        order = np.argsort(row_indices)
        indices.append(row_indices[order])
        data.append(row_values[order])
        indptr.append(indptr[-1] + len(row_indices))
    return CSRFeatureMatrix(
        data=np.concatenate(data) if data else np.asarray([], dtype=np.float32),
        indices=np.concatenate(indices) if indices else np.asarray([], dtype=np.int32),
        indptr=np.asarray(indptr, dtype=np.int64),
        shape=(len(rows), columns),
    )


def save_csr_npz(path: Path, matrix: CSRFeatureMatrix) -> None:
    np.savez_compressed(
        path,
        data=matrix.data,
        indices=matrix.indices,
        indptr=matrix.indptr,
        shape=np.asarray(matrix.shape, dtype=np.int64),
        format_version=np.asarray([1], dtype=np.int16),
    )


def load_csr_npz(path: Path) -> CSRFeatureMatrix:
    with np.load(path) as values:
        version = int(values["format_version"][0])
        if version != 1:
            raise RuntimeError(f"Unsupported CSR feature format: {version}")
        return CSRFeatureMatrix(
            data=values["data"],
            indices=values["indices"],
            indptr=values["indptr"],
            shape=tuple(int(value) for value in values["shape"]),
        )
