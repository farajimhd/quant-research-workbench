"""Closed typed declaration of one independent-session Portfolio quota."""
from dataclasses import dataclass
from .portfolio_acquisition_policy import RULE, PARAMETER, declared_acquisition_limit

@dataclass(frozen=True,slots=True)
class SessionAcquisitionQuotaPolicy:
    maximum: int = 1
    scope: str = 'independent_native_session'
    def __post_init__(self):
        if type(self.maximum) is not int or not 1<=self.maximum<=32 or self.scope!='independent_native_session':
            raise ValueError('Exact bounded native acquisition policy required')
    def payload(self):
        self.__post_init__()
        return {'maximum_accepted_acquisitions_per_ticker_session':self.maximum,'scope':self.scope}

def require_declared_acquisition_policy(release,policy):
    selected=release.rule_set_contracts.count(RULE)
    if not selected and policy is None:return None
    if selected!=1 or type(policy) is not SessionAcquisitionQuotaPolicy:
        raise ValueError('Acquisition quota requires exact paired typed declaration')
    policy.__post_init__()
    return policy
