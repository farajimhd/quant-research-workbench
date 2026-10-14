import json,subprocess,sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from scripts.clickhouse import mount_certified_sec_runtime as mount


@pytest.mark.parametrize('source,alias',[
    ('/mnt/g/data/sec',str(mount.DEFAULT_ALIAS)),
    (str(mount.RUNTIME),str(mount.DEFAULT_ALIAS)),
    ('/mnt/d/TradingML/runtimes/../secrets/sec',str(mount.DEFAULT_ALIAS)),
    (str(mount.DEFAULT_SOURCE),'/mnt/d/market-data/flatfiles'),
])
def test_mount_scope_cannot_redirect_or_shadow_other_data(source,alias):
    with pytest.raises(ValueError):mount.plan(source,alias)


def fixture(stack,*,mounted=False,matching=False,nonempty=False):
    paths={}
    for name,inode in ((mount.RUNTIME,10),(mount.FILES,11),(mount.DEFAULT_SOURCE,20),(mount.DEFAULT_ALIAS,20 if matching else 21)):
        path=Mock();path.is_dir.return_value=True;path.exists.return_value=True
        path.resolve.return_value=path;path.stat.return_value=SimpleNamespace(st_dev=7,st_ino=inode)
        path.iterdir.return_value=iter(['foreign'] if nonempty else [])
        path.__str__=Mock(return_value=str(name));paths[str(name)]=path
    stack.enter_context(patch.object(mount,'Path',side_effect=lambda value:paths[str(value)]))
    stack.enter_context(patch.object(mount.os,'name','posix'))
    stack.enter_context(patch.object(mount.os,'geteuid',return_value=0,create=True))
    states=stack.enter_context(patch.object(mount,'mounted',return_value={'vfs-options':'ro'} if mounted else None))
    runner=stack.enter_context(patch.object(mount,'run',return_value=SimpleNamespace(returncode=0)))
    return paths,states,runner


def test_foreign_mount_and_nonempty_directory_are_preserved():
    for state,content in ((True,False),(False,True)):
        with ExitStack() as stack:
            paths,states,runner=fixture(stack,mounted=state,nonempty=content)
            with pytest.raises(ValueError,match='another directory|nonempty'):
                mount.operate(str(mount.DEFAULT_SOURCE),str(mount.DEFAULT_ALIAS),apply=True)
            runner.assert_not_called()
            for path in paths.values():path.mkdir.assert_not_called()


def test_verified_existing_readonly_alias_is_reused_without_mount_changes():
    with ExitStack() as stack:
        paths,states,runner=fixture(stack,mounted=True,matching=True)
        result=mount.operate(str(mount.DEFAULT_SOURCE),str(mount.DEFAULT_ALIAS))
        assert result['status']=='verified_mount'
        assert all(call.args[0][0]=='sudo' for call in runner.call_args_list)


def test_source_on_another_filesystem_blocks_before_mount_or_directory_changes():
    with ExitStack() as stack:
        paths,states,runner=fixture(stack)
        paths[str(mount.DEFAULT_SOURCE)].stat.return_value=SimpleNamespace(st_dev=8,st_ino=20)
        with pytest.raises(ValueError,match='filesystem'):
            mount.operate(str(mount.DEFAULT_SOURCE),str(mount.DEFAULT_ALIAS),apply=True)
        runner.assert_not_called()
        for path in paths.values():path.mkdir.assert_not_called()


def test_apply_uses_only_exact_bind_and_readonly_commands_then_verifies():
    with ExitStack() as stack:
        paths,states,runner=fixture(stack)
        states.side_effect=[None,{'vfs-options':'ro'}]
        def execute(command):
            if command[:2]==['mount','--bind']:
                paths[str(mount.DEFAULT_ALIAS)].stat.return_value=SimpleNamespace(st_dev=7,st_ino=20)
            return SimpleNamespace(returncode=0)
        runner.side_effect=execute
        assert mount.operate(str(mount.DEFAULT_SOURCE),str(mount.DEFAULT_ALIAS),apply=True)['readonly']
        assert [call.args[0] for call in runner.call_args_list if call.args[0][0]=='mount']==mount.plan(str(mount.DEFAULT_SOURCE),str(mount.DEFAULT_ALIAS))['commands']


def test_actual_plan_cli_is_readonly_and_bootstrap_hook_is_opt_in():
    result=subprocess.run([sys.executable,'-B',mount.__file__,'plan'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    plan=json.loads(result.stdout)
    assert plan['readonly'] and not plan['server_restart']
    from pathlib import Path
    bootstrap=Path(mount.__file__).with_name('clickhouse_bootstrap.sh').read_text()
    assert 'CLICKHOUSE_CERTIFIED_SEC_RUNTIME_ENABLED:-false' in bootstrap
