"""Close-priced financial results are incompatible with V47."""
def import_receipt(*args, **kwargs):
    raise ValueError('V47 reuses only initial programs/RNG, never old financial results')
