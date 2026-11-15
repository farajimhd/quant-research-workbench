"""Frozen graph ownership is per worker stream, with unchanged shapes and RNG."""
import torch
import pytest
from research.bar_gpt.phase2_v1.teacher_replay import TeacherReplay
from research.bar_gpt.phase2_v1.run_train import arguments
from tests.test_bargpt_phase2_latent_v1 import Teacher


def test_replay_flags_require_explicit_cuda_parallel_training(tmp_path):
    base=['--dataset-root','unused','--output',str(tmp_path)]
    for extra in ([],['--device','cuda'],['--device','cuda','--precision','bf16']):
        with pytest.raises(SystemExit):arguments(base+extra+['--teacher-replay'])
    a=arguments(base+['--mode','train','--execute','--device','cuda','--precision','bf16','--prepare-workers','4','--teacher-replay'])
    assert a.teacher_replay and a.teacher_replay_mib==16384


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA replay qualification')
def test_private_stream_buffers_preserve_exact_outputs_and_independent_ownership():
    torch.set_num_threads(1);teacher=Teacher().cuda()
    cpu=torch.get_rng_state();gpu=torch.cuda.get_rng_state()
    keys=set(teacher.state_dict());replay=TeacherReplay(teacher,64,2,memory_mib=2048)
    assert torch.equal(cpu,torch.get_rng_state()) and torch.equal(gpu,torch.cuda.get_rng_state())
    assert set(teacher.state_dict())==keys
    streams=[torch.cuda.Stream(),torch.cuda.Stream()]
    replay.begin();outputs=[]
    for i,stream in enumerate(streams):
        with torch.cuda.stream(stream),torch.no_grad(),torch.autocast('cuda',dtype=torch.bfloat16,cache_enabled=False):
            x=torch.full((64,64,18),float(i),device='cuda');v=torch.ones_like(x,dtype=torch.bool)
            t=torch.zeros((64,64,27),device='cuda')[...,18:];tv=torch.ones((64,64,27),device='cuda',dtype=torch.bool)[...,18:]
            expected=teacher.encode_full(x,v,t,tv);actual=replay.encode(x,v,t,tv)
            assert torch.equal(actual,expected)
            outputs.append(actual.clone());before=actual.clone()
            replay.encode(x+2,v,t,tv)
            assert torch.equal(actual,before)
            assert torch.equal(replay.encode(x[:1],v[:1],t[:1],tv[:1]),teacher.encode_full(x[:1],v[:1],t[:1],tv[:1]))
    for stream in streams:stream.synchronize()
    assert len(replay.bindings)==2 and not torch.equal(outputs[0],outputs[1])
    with pytest.raises(RuntimeError,match='active'):replay.begin()
    replay.end();assert not replay.bindings
    with pytest.raises(RuntimeError,match='lease'):replay.encode(x,v,t,tv)
    replay.begin()
    with torch.cuda.stream(torch.cuda.Stream()),torch.no_grad():replay.encode(x,v,t,tv)
    torch.cuda.synchronize();replay.end()


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA replay qualification')
def test_frozen_teacher_replay_checks_resource_and_freeze_preconditions():
    teacher=Teacher().cuda()
    with pytest.raises(ValueError):TeacherReplay(teacher,64,0)
    teacher.train()
    with pytest.raises(ValueError,match='frozen'):TeacherReplay(teacher,64,2)
