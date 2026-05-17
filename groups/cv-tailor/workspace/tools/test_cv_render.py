"""Unit tests for cv_render.py — parser + golden render assertions.

Run with stdlib unittest (no pytest dependency):
    python3 -m unittest test_cv_render.py -v

The 7 tests cover the Layer 1 (Renderer unit tests) section of the test plan:
  1. Golden render: render gold markdown and assert key XML elements
  2. parse_role_header — valid input
  3. parse_role_header — missing outer ` | ` separator
  4. parse_role_header — missing inner `  |  ` separator
  5. parse_skills (via parse_bullets) — even count → all paired
  6. parse_skills (via parse_bullets) — odd count → last row solo
  7. Missing required section → Hebrew error + exit 1
"""

import io
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

import cv_render  # noqa: E402

GOLD_MARKDOWN = THIS_DIR.parent.parent.parent.parent / 'resumes' / 'cv_pnina_shkulo_tov_shikum_deputy.md'

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def extract_document_xml(docx_path: Path) -> str:
    with zipfile.ZipFile(docx_path) as z:
        with z.open('word/document.xml') as f:
            return f.read().decode('utf-8')


def parse_document(docx_path: Path) -> ET.Element:
    return ET.fromstring(extract_document_xml(docx_path))


def w(tag: str) -> str:
    return f'{{{W_NS}}}{tag}'


class GoldenRenderTest(unittest.TestCase):
    """Test 1: render gold markdown and assert critical formatting elements."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.out = Path(cls.tmpdir) / 'gold.docx'
        cv_render.render(GOLD_MARKDOWN, cls.out)
        cls.root = parse_document(cls.out)
        cls.xml = extract_document_xml(cls.out)

    def test_every_run_uses_calibri(self):
        """Every w:rFonts element must specify Calibri (ascii or hAnsi)."""
        for fonts in self.root.iter(w('rFonts')):
            ascii_font = fonts.get(w('ascii'))
            if ascii_font is not None:
                self.assertEqual(
                    ascii_font, 'Calibri',
                    f"Found non-Calibri font: {ascii_font}",
                )

    def test_body_paragraphs_have_rtl(self):
        """Body paragraphs (non-centered ones) carry per-paragraph w:bidi val=1."""
        # The section-title paragraphs all carry bidi. Count: header (none),
        # contact (none, centered), then 5 section titles + summary + experience
        # role headers + bullets + education + skills rows + languages lines
        # all carry bidi.
        bidi_count = sum(1 for _ in self.root.iter(w('bidi')))
        self.assertGreater(bidi_count, 20,
                           f"Expected >20 w:bidi elements, got {bidi_count}")

    def test_name_color_is_dark_blue(self):
        """The first run's color must be 1F3896 (the dark blue we use for the name)."""
        first_color = None
        for color in self.root.iter(w('color')):
            first_color = color.get(w('val'))
            break
        self.assertEqual(first_color, '1F3896',
                         f"Expected name color 1F3896, got {first_color}")

    def test_section_titles_use_section_color(self):
        """Section-title runs use 2E74B5. There should be exactly 5 section titles."""
        section_color_uses = [
            c for c in self.root.iter(w('color'))
            if c.get(w('val')) == '2E74B5'
        ]
        # 5 section titles + 5 dividers (border color) = at least 5 occurrences of
        # the color in `color` elements (dividers use w:pBdr w:color, a different
        # element, so they don't count here)
        self.assertGreaterEqual(
            len(section_color_uses), 5,
            f"Expected >=5 section-color runs, got {len(section_color_uses)}",
        )

    def test_hebrew_name_present(self):
        """The candidate's Hebrew name must appear verbatim in the document text."""
        self.assertIn('פנינית סבג גולן', self.xml)

    def test_section_titles_all_present(self):
        """All 5 required section titles must appear in the document."""
        for title in cv_render.REQUIRED_SECTIONS:
            self.assertIn(title, self.xml, f"Missing section title: {title}")

    def test_output_file_non_empty(self):
        """Sanity: docx is on disk and non-trivial in size."""
        self.assertGreater(self.out.stat().st_size, 10000)


class ParseRoleHeaderTest(unittest.TestCase):
    """Tests 2-4: parse_role_header valid + two malformed cases."""

    def test_2_valid(self):
        title, org_dates = cv_render.parse_role_header(
            '**מנהלת תפעול** | **אקמי בע"מ  |  2023 עד היום**'
        )
        self.assertEqual(title, 'מנהלת תפעול')
        self.assertEqual(org_dates, 'אקמי בע"מ  |  2023 עד היום')

    def test_3_missing_outer_pipe(self):
        with self.assertRaises(cv_render.RenderError) as ctx:
            cv_render.parse_role_header('**מנהלת תפעול** **אקמי בע"מ  |  2023**')
        self.assertIn("חסר ' | '", str(ctx.exception))
        self.assertIn('שגיאה בהפקת קובץ', str(ctx.exception))

    def test_4_missing_inner_double_space_pipe(self):
        with self.assertRaises(cv_render.RenderError) as ctx:
            cv_render.parse_role_header('**מנהלת תפעול** | **אקמי בע"מ 2023**')
        self.assertIn("חסר '  |  '", str(ctx.exception))
        self.assertIn('שגיאה בהפקת קובץ', str(ctx.exception))


class ParseSkillsTest(unittest.TestCase):
    """Tests 5-6: parse_bullets behaviour + the pairing logic in render()."""

    def test_5_even_count_all_paired(self):
        bullets = cv_render.parse_bullets(
            '* skill A\n* skill B\n* skill C\n* skill D\n'
        )
        self.assertEqual(bullets, ['skill A', 'skill B', 'skill C', 'skill D'])

        # Simulate render's pairing
        rows = []
        for i in range(0, len(bullets), 2):
            row = '• ' + bullets[i]
            if i + 1 < len(bullets):
                row += '   |   • ' + bullets[i + 1]
            rows.append(row)
        self.assertEqual(rows, [
            '• skill A   |   • skill B',
            '• skill C   |   • skill D',
        ])

    def test_6_odd_count_last_row_solo(self):
        bullets = cv_render.parse_bullets(
            '* skill A\n* skill B\n* skill C\n* skill D\n* skill E\n'
        )
        self.assertEqual(len(bullets), 5)

        rows = []
        for i in range(0, len(bullets), 2):
            row = '• ' + bullets[i]
            if i + 1 < len(bullets):
                row += '   |   • ' + bullets[i + 1]
            rows.append(row)
        self.assertEqual(rows, [
            '• skill A   |   • skill B',
            '• skill C   |   • skill D',
            '• skill E',
        ])
        # Solo row has NO trailing pipe
        self.assertNotIn('|', rows[-1])


class PathArgValidationTest(unittest.TestCase):
    """Defense-in-depth: --input/--output must contain only [a-zA-Z0-9_./-]."""

    def test_valid_path_passes(self):
        # No exception expected
        cv_render._validate_path_arg('--input', '/workspace/agent/cv_acme_office.md')
        cv_render._validate_path_arg('--output', 'pnina_test-co_role.docx')

    def test_path_with_space_rejected(self):
        with self.assertRaises(cv_render.RenderError) as ctx:
            cv_render._validate_path_arg('--input', '/workspace/agent/cv acme.md')
        self.assertIn('תווים לא חוקיים', str(ctx.exception))

    def test_path_with_shell_meta_rejected(self):
        for bad in ['/tmp/foo;rm.docx', '/tmp/$(cmd).docx', '/tmp/`cmd`.docx',
                    '/tmp/foo|bar.docx', '/tmp/foo&bar.docx']:
            with self.assertRaises(cv_render.RenderError) as ctx:
                cv_render._validate_path_arg('--output', bad)
            self.assertIn('תווים לא חוקיים', str(ctx.exception))

    def test_unsafe_path_rejected_via_cli(self):
        """End-to-end: passing an unsafe path via CLI exits 1 with Hebrew error."""
        result = subprocess.run(
            [sys.executable, str(THIS_DIR / 'cv_render.py'),
             '--input', '/tmp/cv$(evil).md', '--output', '/tmp/out.docx'],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('תווים לא חוקיים', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class MissingSectionTest(unittest.TestCase):
    """Test 7: missing required section -> Hebrew error on stderr, exit 1."""

    def test_7_missing_experience_section_via_cli(self):
        """End-to-end via subprocess: input missing 'ניסיון מקצועי' exits 1."""
        markdown_without_experience = (
            "**פנינית סבג גולן**\n\n"
            "📞 050-2506169  |  📧 pninushv@gmail.com  |  מגדים\n\n"
            "---\n\n"
            "**תקציר מקצועי**\n\n"
            "Test summary.\n\n"
            "---\n\n"
            "**השכלה**\n\n"
            "**Some degree** – **Some institution**\n\n"
            "---\n\n"
            "**כישורים מרכזיים**\n\n"
            "* skill A\n"
            "* skill B\n\n"
            "---\n\n"
            "**שפות ורישיונות**\n\n"
            "Hebrew\n"
        )
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(markdown_without_experience)
            input_path = f.name
        output_path = tempfile.NamedTemporaryFile(suffix='.docx', delete=False).name

        result = subprocess.run(
            [sys.executable,
             str(THIS_DIR / 'cv_render.py'),
             '--input', input_path,
             '--output', output_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1,
                         f"Expected exit 1, got {result.returncode}. "
                         f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn("שגיאה בהפקת קובץ", result.stderr)
        self.assertIn("ניסיון מקצועי", result.stderr)
        # The whole error fits on one line — no Python traceback leaked through
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('File "', result.stderr)


if __name__ == '__main__':
    unittest.main()
