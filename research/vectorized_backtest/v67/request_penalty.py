"""One five-dollar penalty per distinct blocked request, independent of fills."""
import numpy as np

LAMBDA = 5.
REASONS = ('invalid_price','invalid_stop','limit','quote','liquidity','inventory')


def assess(reasons, charged):
    """Boolean request lanes by reason; reasons may overlap, charge cannot."""
    reasons=np.asarray(reasons,dtype=bool)
    charged=np.asarray(charged,dtype=bool)
    if reasons.shape != (*charged.shape,len(REASONS)):
        raise ValueError('Request/reason axes differ')
    blocked=reasons.any(-1)
    first=blocked&~charged
    return charged|blocked,first.astype(np.float64)*LAMBDA
