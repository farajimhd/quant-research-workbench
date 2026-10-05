"""Predeclared development-only selection for teacher initialization.

Primary score is exact entry ticker F1, then entry action-class F1, then
lower mean label loss. This scores correct timing AND identity and cannot
select a checkpoint merely by predicting no orders. No financial claim.
"""
import math

from research.rl_trading.v6.split import DEVELOPMENT

SELECTION_VERSION = 'rl-v6-development-exact-entry-f1-v1'


def teacher_validation_score(summaries, *, development_days=None):
    expected=tuple(map(str,DEVELOPMENT)) if development_days is None else tuple(development_days)
    if {r['day'] for r in summaries} != set(expected) or len(summaries)!=len(expected):
        raise ValueError('Teacher selection requires exactly the admitted development days')
    actual = predicted = exact = class_correct = total = 0
    loss_sum = 0.
    for row in summaries:
        count = row['action_class_counts']['enter_long']
        predictions = row['action_predicted_class_counts']['enter_long']
        recall = row['action_class_recall']['enter_long']
        accuracy = row['entry_token_accuracy']
        if (count<1 or predictions<0 or row['decisions']<1 or accuracy is None or
                not all(math.isfinite(v) for v in (recall,accuracy,row['mean_loss'])) or
                not 0<=accuracy<=recall<=1):
            raise ValueError('Invalid development entry evidence')
        actual += count; predicted += predictions
        exact += round(accuracy*count); class_correct += round(recall*count)
        total += row['decisions']; loss_sum += row['mean_loss']*row['decisions']
    denominator = actual+predicted
    return {'version':SELECTION_VERSION, 'exact_entry_f1':2*exact/denominator,
        'entry_class_f1':2*class_correct/denominator,'mean_label_loss':loss_sum/total,
        'teacher_entry_labels':actual,'predicted_entries':predicted,
        'exact_entry_predictions':exact,'scope':'teacher_state_labels_not_financial_replay'}


def selection_key(score):
    return score['exact_entry_f1'],score['entry_class_f1'],-score['mean_label_loss']
