import pytest
from research.rl_trading.v6.learning_rate import cosine_warmup


def test_cosine_warmup_endpoints_and_resume_position():
    assert cosine_warmup(0,20,3e-4)==pytest.approx(3e-5)
    assert cosine_warmup(1,20,3e-4)==pytest.approx(3e-4)
    assert cosine_warmup(20,20,3e-4)==pytest.approx(3e-5)
    rates=[cosine_warmup(i/10,20,3e-4) for i in range(201)]
    assert all(a<=b for a,b in zip(rates[:10],rates[1:11]))
    assert all(a>=b for a,b in zip(rates[10:],rates[11:]))
    assert cosine_warmup(6/16,20,3e-4)==pytest.approx(rates[0]+(3e-4-rates[0])*6/16)


@pytest.mark.parametrize('position,total,warmup,ratio',[
    (-1,20,1,.1),(21,20,1,.1),(0,20,20,.1),(0,20,1,0)])
def test_schedule_rejects_invalid_bounds(position,total,warmup,ratio):
    with pytest.raises(ValueError):
        cosine_warmup(position,total,3e-4,warmup,ratio)
