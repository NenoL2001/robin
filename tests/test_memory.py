from portfolio_bot.memory import MemoryStore, fts_query, memory_path


def test_memory_store_add_search_recent(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    rowid = store.add(
        "manual",
        "POET long call looked cheap before silicon photonics catalyst",
        symbol="POET",
        importance=0.9,
    )
    assert rowid is not None
    assert store.count() == 1
    search = store.search("silicon photonics catalyst", symbol="POET")
    assert len(search) == 1
    assert search[0].symbol == "POET"
    recent = store.recent(symbol="POET")
    assert recent[0].content.startswith("POET long call")


def test_memory_path_resolves_relative_to_data_dir(tmp_path):
    assert memory_path(tmp_path, "memory.sqlite") == tmp_path / "memory.sqlite"


def test_fts_query_quotes_terms():
    assert fts_query("POET catalyst") == '"POET" OR "catalyst"'
