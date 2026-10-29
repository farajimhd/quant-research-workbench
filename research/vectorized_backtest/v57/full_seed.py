"""Fresh version-local initialization; never import V56 fitness or genomes."""
def global_seed(exports,job,probe,progress=None):
    from .campaign_seed import seed
    return seed(exports,dict(job,campaign_stage=job.get('campaign_stage','session')),probe,progress=progress)
