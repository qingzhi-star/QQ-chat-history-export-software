from dataclasses import replace
import pytest
from qqexport.export.archive import ArchiveReader,create_archive
from qqexport.library import Library,LibraryError
from qqexport.errors import UserError
from tests.test_orchestrator import make_source


@pytest.fixture(autouse=True)
def home(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))


def test_library_import_dedup_snapshot_account_and_clear(tmp_path):
    source = make_source(tmp_path)
    archive = tmp_path/"synthetic.qqarchive"
    create_archive(source,["c2c:u_a"],archive)
    reader,library = ArchiveReader(archive),Library()
    view = None
    try:
        result = library.import_archive(reader)
        assert result["added"]==2
        assert library.import_archive(reader)["duplicates"]==2
        view = library.source(result["owner"])
        messages = list(view.iter_messages("c2c:u_a"))
        assert len(messages)==2 and messages[1].segments[0].media.source.read_bytes()==b"img"
        with pytest.raises(UserError):
            Library.clear()
        source.account_uin = "synthetic-second-owner"
        other = tmp_path/"other.qqarchive"
        create_archive(source,["c2c:u_a"],other)
        second = ArchiveReader(other)
        try:
            library.import_archive(second)
        finally:
            second.close()
        assert len(library.accounts())==2
        assert view.count("c2c:u_a")==2
    finally:
        if view:
            view.close()
        reader.close()
        library.close()
    Library.clear()
    assert not (tmp_path/"home"/"library"/"library.sqlite3").exists()


def test_conflicting_import_rolls_back_all_changes(tmp_path):
    source = make_source(tmp_path)
    original = source.iter_messages
    archive = tmp_path/"first.qqarchive"
    create_archive(source,["c2c:u_a"],archive)
    library,reader = Library(),ArchiveReader(archive)
    try:
        owner = library.import_archive(reader)["owner"]
        def altered(chat):
            for message in original(chat):
                yield replace(message,sender_name="changed synthetic sender")
        source.iter_messages = altered
        bad = tmp_path/"conflict.qqarchive"
        create_archive(source,["c2c:u_a"],bad)
        other = ArchiveReader(bad)
        try:
            with pytest.raises(LibraryError,match="不同内容"):
                library.import_archive(other)
        finally:
            other.close()
        assert library.accounts()[0]["messages"]==2
        view = library.source(owner)
        try:
            assert next(view.iter_messages("c2c:u_a")).sender_name!="changed synthetic sender"
        finally:
            view.close()
    finally:
        reader.close()
        library.close()


def test_future_library_schema_is_rejected_without_rewriting(tmp_path):
    library = Library()
    path = library.path
    library.con.execute("PRAGMA user_version=999")
    library.con.commit()
    library.close()
    before = path.read_bytes()
    with pytest.raises(LibraryError,match="版本"):
        Library()
    assert path.read_bytes()==before
