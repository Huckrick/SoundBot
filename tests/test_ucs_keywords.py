from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / 'backend'
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import ucs_keywords


class UCSKeywordTests(unittest.TestCase):
    def setUp(self) -> None:
        ucs_keywords._ucs_keywords_cache = None
        ucs_keywords._ucs_loaded = False

    def test_committed_xlsx_loads_without_pandas_or_openpyxl(self) -> None:
        mapping = ucs_keywords.load_ucs_keywords(
            str(REPO_ROOT / 'UCS+音效分类中英文对照表.xlsx')
        )

        self.assertIn('气体', mapping)
        self.assertIn('compressed air', mapping['气体'])
        self.assertIn('航空器', mapping)
        self.assertGreater(len(mapping), 100)


if __name__ == '__main__':
    unittest.main()
