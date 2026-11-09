"""The resume offset saved at the page cap must be the offset inside the chunk being drained."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import rfp_text_pipeline as p


def test_next_offset_is_within_current_ncode(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "DONE_CHUNKS_JSON", tmp_path / "done.json")
    monkeypatch.setattr(p.time, "sleep", lambda s: None)
    sizes = {("A", 0): 1000, ("A", 1000): 5,          # ncode A: 2 pages, then short page
             ("B", 0): 1000, ("B", 1000): 1000, ("B", 2000): 1000}  # B: still full at the cap

    def fake_search(session, cf, ct, offset, ncode, limit=1000):
        n = sizes[(ncode, offset)]
        return [{"noticeId": f"{ncode}{offset}-{i}"} for i in range(n)], 9999

    monkeypatch.setattr(p, "search_page", fake_search)
    progress = {}
    pages = 0
    for _, pages in p.iter_opps_in_window(None, date(2026, 1, 1), date(2026, 1, 31), 5, ["A", "B"], progress=progress):
        pass
    assert pages == 5                      # 2 pages of A + 3 of B
    # B was read at offsets 0, 1000, 2000; the next unread page of B is 3000, not 5000
    assert progress["next_offset"] == 3000
