"""Unchanged V7 certified input/execution utility."""
from research.vectorized_backtest.v7.io_pipeline import *


def prepare_host(data,pin=False,resident=False):
    if hasattr(data,'activate'):data.activate('cpu')
    if pin or resident:
        from .resident_features import ResidentFeatures
        if not hasattr(data,'resident_features'):data.resident_features=ResidentFeatures(data)
    if pin:
        data.resident_features.pin()
        data.host_tensors={k:v if v.is_pinned() else v.pin_memory() for k,v in data.host_tensors.items()}
    return data
