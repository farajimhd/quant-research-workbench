"""Keep one monotonic live campaign view separate from historical drill-downs."""
class LiveStatus:
    def __init__(self):
        self.latest={};self.owner=None

    def accept(self,value):
        if not value:return self.latest
        owner=value.get('owner',{})
        identity=(owner.get('pid'),owner.get('creation_time'))
        if identity!=self.owner:
            self.owner=identity;self.latest={}
        previous=self.latest
        completed=max(int(value.get('completed_generations') or 0),int(previous.get('completed_generations') or 0))
        old_generation=max(int(previous.get('generation') or 0),int(previous.get('completed_generations') or 0))
        generation=max(int(value.get('generation') or 0),int(value.get('completed_generations') or 0))
        if generation<old_generation:
            return dict(previous,observer_warning='Ignored older generation status; retaining last live snapshot')
        if value.get('updated_epoch',0)<previous.get('updated_epoch',0):return previous
        self.latest=dict(value,completed_generations=completed)
        return self.latest
