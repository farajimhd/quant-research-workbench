"""Close-priced financial results are incompatible with V48."""
def import_receipt(*args, **kwargs):
    raise ValueError('V48 reuses only initial programs/RNG, never old financial results')
