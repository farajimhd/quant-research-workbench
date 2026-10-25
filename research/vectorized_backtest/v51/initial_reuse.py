"""Unrestricted receipt imports are forbidden; use the sealed initial_rescore path."""
def import_receipt(*args,**kwargs):
    raise ValueError('V51 requires the explicit source-bound initial_rescore import')
