from collections import Counter
from dataclasses import asdict
REJECTIONS=Counter()
class SemanticError(ValueError):pass
def identity(member):return member.payload()
def snapshot():return dict(REJECTIONS)
