"""Hard-label diagnostics beside the authoritative soft-distribution loss."""
import numpy as np

LABELS = ('ENTRY', 'WAIT', 'HOLD', 'EXIT')


def classification_metrics(confusion, names=LABELS):
    matrix = np.asarray(confusion)
    if matrix.shape != (len(names), len(names)) or (matrix < 0).any():
        raise ValueError('Confusion must be a nonnegative target-by-prediction matrix')
    result = {}
    for i, name in enumerate(names):
        actual, predicted, correct = int(matrix[i].sum()), int(matrix[:, i].sum()), int(matrix[i, i])
        precision = correct / predicted if predicted else 0.
        recall = correct / actual if actual else 0.
        result[name] = dict(count=actual, predicted_count=predicted, precision=precision,
                            recall=recall, f1=2*precision*recall/(precision+recall) if precision+recall else 0.)
    return dict(labels=result, confusion=matrix.tolist(),
                accuracy=float(np.trace(matrix)/matrix.sum()) if matrix.sum() else None)
