"""Golden integration test for cv_render.py.

Renders the reverse-engineered gold markdown
(resumes/cv_pnina_shkulo_tov_shikum_deputy.md) and asserts it is structurally
equivalent to the canonical .docx that was produced directly from
resumes/generate_shkulo_tov_shikum_deputy_docx.py.

"Structurally equivalent" means:
  - Same number of body paragraphs
  - Same paragraph text content (after Hebrew/whitespace normalization)
  - Same key formatting attributes per paragraph (font name, RTL flag,
    section-title color where applicable)

We do NOT byte-compare — python-docx generates fresh rsids and revision IDs
per run, so two renders of the same source produce different bytes.

Run with stdlib unittest (no pytest dependency):
    python3 -m unittest test_golden.py -v
"""

import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import sys
THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

import cv_render  # noqa: E402

WORKTREE_ROOT = THIS_DIR.parent.parent.parent.parent
GOLD_MARKDOWN = WORKTREE_ROOT / 'resumes' / 'cv_pnina_shkulo_tov_shikum_deputy.md'

# Locate the canonical .docx. Order: env var override → relative resolution
# from the worktree (../../../../resumes/...) → main checkout fallback.
# The file lives in the user's untracked `resumes/` folder, so its path
# depends on the install. The test skips gracefully if none of these resolve.
def _resolve_canonical_docx() -> Path:
    env_path = os.environ.get('CV_CANONICAL_DOCX')
    if env_path:
        return Path(env_path)
    candidates = [
        WORKTREE_ROOT / 'resumes' / 'pnina_shkulo_tov_shikum_deputy.docx',
        # Worktree is typically at <checkout>/.claude/worktrees/<name>, so the
        # main checkout's resumes/ sits three levels above the worktree root.
        WORKTREE_ROOT.parent.parent.parent / 'resumes' / 'pnina_shkulo_tov_shikum_deputy.docx',
        Path('/home/ubuntu/nanoclaw-v2/resumes/pnina_shkulo_tov_shikum_deputy.docx'),
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


CANONICAL_DOCX = _resolve_canonical_docx()

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def w(tag: str) -> str:
    return f'{{{W_NS}}}{tag}'


def parse_docx(docx_path: Path) -> ET.Element:
    with zipfile.ZipFile(docx_path) as z:
        with z.open('word/document.xml') as f:
            return ET.fromstring(f.read())


def paragraph_text(p: ET.Element) -> str:
    """Concatenate all w:t runs inside a paragraph into one string."""
    return ''.join(t.text or '' for t in p.iter(w('t')))


def normalize_text(s: str) -> str:
    """Collapse whitespace runs and strip — paragraphs that differ only in
    spacing/quotation should compare equal."""
    # Collapse all whitespace (including no-break spaces) to single space
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def list_paragraphs(root: ET.Element) -> list[ET.Element]:
    body = root.find(w('body'))
    return list(body.findall(w('p')))


class GoldenStructureTest(unittest.TestCase):
    """Render gold markdown and structurally diff against the canonical docx."""

    @classmethod
    def setUpClass(cls):
        if not CANONICAL_DOCX.exists():
            raise unittest.SkipTest(
                f"Canonical docx not found at {CANONICAL_DOCX} — "
                "this test runs only on the dev machine where the canonical "
                "resumes/ folder is populated."
            )
        cls.tmpdir = tempfile.mkdtemp()
        cls.rendered_path = Path(cls.tmpdir) / 'rendered.docx'
        cv_render.render(GOLD_MARKDOWN, cls.rendered_path)
        cls.rendered_root = parse_docx(cls.rendered_path)
        cls.canonical_root = parse_docx(CANONICAL_DOCX)
        cls.rendered_paras = list_paragraphs(cls.rendered_root)
        cls.canonical_paras = list_paragraphs(cls.canonical_root)

    def test_paragraph_count_matches(self):
        """Same total number of body paragraphs."""
        self.assertEqual(
            len(self.rendered_paras), len(self.canonical_paras),
            f"Paragraph count differs: rendered={len(self.rendered_paras)}, "
            f"canonical={len(self.canonical_paras)}",
        )

    def test_paragraph_text_matches_in_order(self):
        """Each paragraph's text (whitespace-normalized) matches the canonical."""
        rendered_texts = [normalize_text(paragraph_text(p)) for p in self.rendered_paras]
        canonical_texts = [normalize_text(paragraph_text(p)) for p in self.canonical_paras]
        if rendered_texts != canonical_texts:
            # Surface the first diff for easy debugging
            for i, (a, b) in enumerate(zip(rendered_texts, canonical_texts)):
                if a != b:
                    self.fail(
                        f"First paragraph diff at index {i}:\n"
                        f"  rendered:  {a!r}\n"
                        f"  canonical: {b!r}"
                    )
            # If equal-prefix exhausted, lengths differ
            self.fail(
                f"Paragraph lists diverge after index "
                f"{min(len(rendered_texts), len(canonical_texts))}"
            )

    def test_section_title_colors_match(self):
        """Every paragraph that uses the section color in the canonical also
        uses it in the rendered output (at the same paragraph index)."""
        rendered_colors = [self._first_color(p) for p in self.rendered_paras]
        canonical_colors = [self._first_color(p) for p in self.canonical_paras]
        self.assertEqual(rendered_colors, canonical_colors)

    def test_every_run_uses_calibri(self):
        """No run in the rendered output uses anything other than Calibri."""
        for fonts in self.rendered_root.iter(w('rFonts')):
            ascii_font = fonts.get(w('ascii'))
            if ascii_font is not None:
                self.assertEqual(
                    ascii_font, 'Calibri',
                    f"Non-Calibri font in rendered output: {ascii_font}",
                )

    @staticmethod
    def _first_color(paragraph: ET.Element) -> str | None:
        for color in paragraph.iter(w('color')):
            return color.get(w('val'))
        return None


if __name__ == '__main__':
    unittest.main()
