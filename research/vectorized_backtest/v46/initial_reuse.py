"""Close-priced financial results are incompatible with V46."""
def import_receipt(*args, **kwargs):
    raise ValueError('V46 reuses only initial programs/RNG, never old financial results')
