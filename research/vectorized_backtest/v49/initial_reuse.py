"""Close-priced financial results are incompatible with V49."""
def import_receipt(*args, **kwargs):
    raise ValueError('V49 reuses only initial programs/RNG, never old financial results')
