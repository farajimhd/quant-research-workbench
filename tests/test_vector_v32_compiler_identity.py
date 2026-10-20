from research.vectorized_backtest.v32.runtime import compiler_source_hash


def test_only_host_changes_preserve_compiler_identity(tmp_path):
    root=tmp_path/'v32';root.mkdir()
    kernel=root/'network.py';host=root/'host_store.py'
    kernel.write_bytes(b'kernel = 1\r\n');host.write_text('host = 1\n')
    original=compiler_source_hash(tmp_path)
    host.write_text('host = 2\n')
    assert compiler_source_hash(tmp_path)==original
    kernel.write_bytes(b'kernel = 1\n')
    assert compiler_source_hash(tmp_path)==original
    kernel.write_text('kernel = 2\n')
    assert compiler_source_hash(tmp_path)!=original


def test_all_prior_version_dependencies_remain_bound(tmp_path):
    root=tmp_path/'v27';root.mkdir();path=root/'io_pipeline.py';path.write_text('old = 1')
    original=compiler_source_hash(tmp_path);path.write_text('old = 2')
    assert compiler_source_hash(tmp_path)!=original
