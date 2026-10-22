from research.vectorized_backtest.v42.local_details import candidate_label

def test_compact_origin_retains_mutation_crossover_and_serial():
    row=dict(serial=37714,candidate_id='G001-T2588747-R19997059-037714-M003-X010-00ce')
    assert candidate_label(row)=='G001-T11R89-M3X10/37714'

def test_pure_origins_and_unavailable_labels():
    assert candidate_label(dict(serial=1,candidate_id='G001-T1-R0-000001-M000-X000-abcd'))=='G001-T100R0-M0X0/1'
    assert candidate_label(dict(serial=2))=='2'
    assert candidate_label(dict(serial=2,candidate_id='legacy'))=='legacy'
