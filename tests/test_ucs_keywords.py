from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import ucs_keywords


class UCSKeywordTests(unittest.TestCase):
    def setUp(self):
        def clear_cache():
            ucs_keywords._ucs_keywords_cache = None
            ucs_keywords._ucs_loaded = False
            ucs_keywords.category_codes.cache_clear()

        clear_cache()
        self.addCleanup(clear_cache)

    def test_local_workbook_loads_keywords_and_category_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ucs-keywords.xlsx"
            with ZipFile(path, "w") as workbook:
                workbook.writestr("xl/workbook.xml", """
                    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                      <sheets><sheet name="Synthetic" r:id="rId1"/></sheets>
                    </workbook>
                """)
                workbook.writestr("xl/_rels/workbook.xml.rels", """
                    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                      <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
                    </Relationships>
                """)
                workbook.writestr("xl/worksheets/sheet1.xml", """
                    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                      <sheetData>
                        <row r="3">
                          <c r="A3" t="inlineStr"><is><t>CatID</t></is></c>
                          <c r="B3" t="inlineStr"><is><t>Category_zh</t></is></c>
                          <c r="C3" t="inlineStr"><is><t>Synonyms - Comma Separated</t></is></c>
                        </row>
                        <row r="4">
                          <c r="A4" t="inlineStr"><is><t>DEMO</t></is></c>
                          <c r="B4" t="inlineStr"><is><t>提示音</t></is></c>
                          <c r="C4" t="inlineStr"><is><t>alert, beep</t></is></c>
                        </row>
                      </sheetData>
                    </worksheet>
                """)
            with patch.dict("os.environ", {"SOUNDBOT_UCS_PATH": str(path)}):
                self.assertEqual(ucs_keywords.load_ucs_keywords(), {"提示音": ["alert", "beep"]})
                self.assertEqual(ucs_keywords.category_codes(), {"demo": "DEMO"})

    def test_missing_workbook_keeps_search_available(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict("os.environ", {"SOUNDBOT_UCS_PATH": str(Path(temp) / "absent.xlsx")}):
                self.assertEqual(ucs_keywords.load_ucs_keywords(), {})
                self.assertEqual(ucs_keywords.category_codes(), {})


if __name__ == "__main__":
    unittest.main()
