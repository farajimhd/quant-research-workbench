from __future__ import annotations

import unittest

from pipelines.sec.edgar.sec_pipeline.text_renderer import (
    DUPLICATE_BLOCK_MIN_CHARS,
    SEC_PACKED_TEXT_RENDERER_VERSION,
    STRUCTURED_XML_EXCLUDED_QUALITY_FLAG,
    render_sec_packed_text,
)


class SecPackedTextRendererTests(unittest.TestCase):
    def test_colspan_grid_keeps_values_under_the_correct_headers(self) -> None:
        source = """
        <table>
          <tr><td>Investment</td><td colspan="3">Rate and Spread</td><td>Interest Rate</td><td>Maturity Date</td><td colspan="2">Cost</td></tr>
          <tr><td>Example Loan</td><td>S +</td><td></td><td>4.50%</td><td>8.17%</td><td>08/29/2031</td><td>$</td><td>6,455</td></tr>
        </table>
        """
        text = render_sec_packed_text(source, "html").packed_text
        self.assertIn("Rate and Spread=S + 4.50%", text)
        self.assertIn("Interest Rate=8.17%", text)
        self.assertIn("Maturity Date=08/29/2031", text)
        self.assertIn("Cost=$ 6,455", text)

    def test_rowspan_values_are_carried_into_following_rows(self) -> None:
        source = """
        <table>
          <tr><td>Company</td><td>Year</td><td>Revenue</td></tr>
          <tr><td rowspan="2">Issuer</td><td>2025</td><td>100</td></tr>
          <tr><td>2024</td><td>80</td></tr>
        </table>
        """
        text = render_sec_packed_text(source, "html").packed_text
        self.assertIn("Company=Issuer; Year=2025; Revenue=100", text)
        self.assertIn("Company=Issuer; Year=2024; Revenue=80", text)

    def test_label_row_after_values_is_not_treated_as_a_header(self) -> None:
        source = """
        <table>
          <tr><td>Delaware</td><td></td><td>84-2009506</td></tr>
          <tr><td>(State of incorporation)</td><td></td><td>(Tax ID)</td></tr>
          <tr><td>1585 Broadway</td><td></td><td>10036</td></tr>
        </table>
        """
        text = render_sec_packed_text(source, "html").packed_text
        self.assertNotIn("Columns:", text)
        self.assertIn("Delaware: 84-2009506", text)
        self.assertIn("1585 Broadway: 10036", text)

    def test_repeated_xml_records_are_packed_with_tag_derived_fields(self) -> None:
        source = "<root>" + "".join(
            f"<record><issuer>Name {index}</issuer><vote><result>FOR</result></vote></record>"
            for index in range(3)
        ) + "</root>"
        result = render_sec_packed_text(source, "xml")
        self.assertIn("xml_repeated_records_packed", result.quality_flags)
        self.assertIn("<record> issuer=Name 0; vote/result=FOR", result.packed_text)
        self.assertNotIn("<root/record/issuer>", result.packed_text)

    def test_structured_fund_xml_stays_in_source_but_is_excluded_from_model_text(self) -> None:
        result = render_sec_packed_text(
            "<root><holding><name>Issuer</name></holding></root>",
            "xml",
            form_type="N-MFP3",
        )
        self.assertEqual(result.packed_text, "")
        self.assertIn(STRUCTURED_XML_EXCLUDED_QUALITY_FLAG, result.quality_flags)

    def test_layout_lines_and_html_page_footer_are_removed(self) -> None:
        plain = render_sec_packed_text("Title\n----------------\nImportant\n<PAGE>\nMore", "plain_text")
        self.assertEqual(plain.packed_text, "Title\nImportant\nMore")
        html = render_sec_packed_text("<p>Important</p><p>171</p><hr style='page-break-after:always'><p>Next</p>", "html")
        self.assertEqual(html.packed_text, "Important\nNext")
        self.assertIn("html_page_numbers_removed", html.quality_flags)

    def test_mojibake_and_ligatures_are_repaired_and_flagged(self) -> None:
        result = render_sec_packed_text("Caf\u00c3\u00a9 \ufb01ling", "plain_text")
        self.assertEqual(result.packed_text, "Caf\u00e9 filing")
        self.assertIn("mojibake_suspect", result.quality_flags)
        self.assertIn("non_ascii", result.quality_flags)

    def test_only_large_exact_blocks_are_replaced_as_duplicates(self) -> None:
        short = "Short repeated paragraph."
        long = "Company-specific repeated disclosure " + "x" * DUPLICATE_BLOCK_MIN_CHARS
        source = f"<p>{short}</p><p>{short}</p><p>{long}</p><p>{long}</p>"
        result = render_sec_packed_text(source, "html")
        self.assertEqual(result.packed_text.count(short), 2)
        self.assertIn("DUPLICATE of [Company-specifi]", result.packed_text)
        self.assertEqual(result.duplicate_block_count, 1)

    def test_intermediate_output_can_be_disabled_for_production_memory_use(self) -> None:
        result = render_sec_packed_text("<p>Important text</p>", "html", include_intermediate=False)
        self.assertEqual(result.renderer_version, SEC_PACKED_TEXT_RENDERER_VERSION)
        self.assertEqual(result.intermediate_text, "")


if __name__ == "__main__":
    unittest.main()
