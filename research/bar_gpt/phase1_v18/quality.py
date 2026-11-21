import math


def score(metrics,reference):
    """Fixed, unit-balanced checkpoint score; all groups remain reported."""
    required=[k for k in reference if ('/raw_mae/' in k and k.startswith(('masked/','all_visible/')))
              or (k.endswith('/moving_direction_pct') and k.startswith(('all_visible/candle/','activity/all_visible/')))]
    if any(k not in metrics or not math.isfinite(metrics[k]) for k in required):raise ValueError('Missing/nonfinite quality metric')
    ratios={k:metrics[k]/max(v,1e-12) for k,v in reference.items()
            if '/raw_mae/' in k and k.startswith(('masked/','all_visible/')) and k in metrics}
    visible=[math.log1p(v) for k,v in ratios.items() if k.startswith('all_visible/')]
    masked=[math.log1p(v) for k,v in ratios.items() if k.startswith('masked/')]
    direction=[1-v/100 for k,v in metrics.items() if k.endswith('/moving_direction_pct')
               and (k.startswith('all_visible/candle/') or k.startswith('activity/all_visible/'))]
    if not visible or not masked or not direction:raise ValueError('Incomplete quality groups')
    value=.5*sum(visible)/len(visible)+.25*sum(masked)/len(masked)+.25*sum(direction)/len(direction)
    return value,dict(worst_raw_ratio=max(ratios.values()),raw_groups=len(ratios),direction_groups=len(direction))
