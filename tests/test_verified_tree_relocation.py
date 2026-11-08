import importlib.util
import json
import os
from pathlib import Path

import pytest

SPEC=importlib.util.spec_from_file_location('relocate_verified_tree',Path(__file__).parents[1]/'scripts/relocate_verified_tree.py')
M=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def job(tmp_path):
    source=tmp_path/'source';source.mkdir()
    (source/'nested').mkdir();(source/'nested'/'receipt.json').write_text('{"ok":true}')
    (source/'data.bin').write_bytes(bytes(range(256))*4000)
    (source/'empty').mkdir()
    return M.Relocation(tmp_path/'control',tmp_path/'archive',[source],workers=2),source


def test_backup_and_final_retirement_preserve_readers_and_external_links(tmp_path):
    j,source=job(tmp_path)
    outside=tmp_path/'outside';outside.mkdir();(outside/'untouched').write_text('preserve')
    M.junction(source/'external',outside)
    j.backup()
    assert (source/'data.bin').is_file()
    expected=M.hash_file(source/'data.bin')
    j.finalize()
    assert source.resolve()==j.destination(0).resolve()
    assert M.hash_file(source/'data.bin')==expected
    assert (source/'empty').is_dir()
    assert (outside/'untouched').read_text()=='preserve'
    assert not j.retiring(0).exists()
    assert j.state['roots']['0']['phase']=='complete'
    j.db.close()


def test_corruption_with_preserved_metadata_blocks_source_deletion(tmp_path):
    j,source=job(tmp_path);j.backup()
    archived=j.destination(0)/'data.bin';s=archived.stat()
    with archived.open('r+b') as stream:stream.write(b'WRONG')
    os.utime(archived,ns=(s.st_atime_ns,s.st_mtime_ns))
    with pytest.raises(RuntimeError,match='hash changed'):j.finalize()
    assert source.resolve()==source
    assert (source/'data.bin').is_file()
    j.db.close()


def test_reinventory_copies_new_and_changed_source_files(tmp_path):
    j,source=job(tmp_path);j.backup()
    (source/'new').write_text('created after first snapshot')
    (source/'data.bin').write_bytes(b'changed')
    j.finalize()
    assert (source/'new').read_text()=='created after first snapshot'
    assert (source/'data.bin').read_bytes()==b'changed'
    j.db.close()


def test_interrupted_rename_rolls_back_and_resumes_without_loss(tmp_path):
    j,source=job(tmp_path);j.backup()
    j.state['roots']['0']['phase']='cutover_intent';j.save()
    os.rename(source,j.retiring(0));j.db.close()
    resumed=M.Relocation(tmp_path/'control',tmp_path/'archive',[source],workers=2)
    assert source.resolve()==source
    assert (source/'data.bin').exists()
    resumed.finalize()
    assert resumed.state['roots']['0']['phase']=='complete'
    resumed.db.close()


def test_archive_copy_failure_never_removes_source(tmp_path,monkeypatch):
    j,source=job(tmp_path)
    def fail(*args):raise OSError('simulated destination I/O failure')
    monkeypatch.setattr(j,'copy_one',fail)
    with pytest.raises(OSError,match='I/O failure'):j.backup()
    assert source.resolve()==source
    assert (source/'data.bin').exists()
    assert not j.retiring(0).exists()
    j.db.close()


def test_existing_unowned_archive_is_never_overwritten(tmp_path):
    source=tmp_path/'source';source.mkdir()
    archive=tmp_path/'archive';archive.mkdir();(archive/'retain').write_text('existing backup')
    with pytest.raises(ValueError,match='pre-existing'):
        M.Relocation(tmp_path/'control',archive,[source])
    assert (archive/'retain').read_text()=='existing backup'


def test_retained_parent_allows_new_versions_on_original_volume(tmp_path):
    source=tmp_path/'backtests';source.mkdir()
    version=source/'v68';version.mkdir();(version/'receipt').write_text('sealed')
    j=M.Relocation(tmp_path/'control',tmp_path/'archive',[source],keep_containers=(0,))
    j.backup();j.finalize()
    assert source.resolve()==source
    assert (source/'v68').resolve()==(j.destination(0)/'v68').resolve()
    assert (source/'v68'/'receipt').read_text()=='sealed'
    (source/'v69').mkdir()
    assert (source/'v69').resolve().is_relative_to(source)
    assert not (j.destination(0)/'v69').exists()
    j.db.close()


def test_partial_container_alias_installation_recovers_before_deletion(tmp_path):
    source=tmp_path/'backtests';source.mkdir();(source/'v68').mkdir()
    (source/'v68'/'receipt').write_text('sealed')
    j=M.Relocation(tmp_path/'control',tmp_path/'archive',[source],keep_containers=(0,))
    j.backup();j.state['roots']['0']['phase']='cutover_intent';j.save()
    os.rename(source,j.retiring(0));source.mkdir();M.junction(source/'v68',j.destination(0)/'v68');j.db.close()
    resumed=M.Relocation(tmp_path/'control',tmp_path/'archive',[source],keep_containers=(0,))
    assert (source/'v68'/'receipt').read_text()=='sealed'
    assert (source/'v68').resolve()==source/'v68'
    resumed.finalize();assert resumed.aliases_valid(0)
    resumed.db.close()
