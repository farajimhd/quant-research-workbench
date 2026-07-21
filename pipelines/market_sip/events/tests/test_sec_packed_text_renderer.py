from __future__ import annotations

import unittest

from pipelines.sec.edgar.sec_pipeline.text_renderer import (
    DUPLICATE_BLOCK_MIN_CHARS,
    SEC_PACKED_TEXT_RENDERER_VERSION,
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

    def test_substantive_xml_comments_are_preserved_in_document_order(self) -> None:
        source = """<assetdata>
        <!-- Exhibit 103 -->
        <!-- Asset Related Document -->
        <!-- Item 3(c)(4): Original loan term includes a partial month. -->
        </assetdata>"""
        result = render_sec_packed_text(source, "xml", document_type="EX-103", form_type="ABS-EE")

        self.assertEqual(
            result.packed_text,
            "<assetdata>\nExhibit 103\nAsset Related Document\n"
            "Item 3(c)(4): Original loan term includes a partial month.",
        )
        self.assertIn("xml_comments_preserved", result.quality_flags)
        self.assertNotIn("empty_rendered_text", result.quality_flags)

    def test_body_terminates_malformed_unclosed_html_head(self) -> None:
        source = """<html><head><title></title><body>
        <p>Exhibit 5.1</p><p>We have acted as counsel to the issuer.</p>
        </body></head></html>"""
        result = render_sec_packed_text(source, "html", document_type="EX-5.1", form_type="S-3")

        self.assertEqual(result.packed_text, "Exhibit 5.1\nWe have acted as counsel to the issuer.")
        self.assertNotIn("empty_rendered_text", result.quality_flags)

    def test_structurally_empty_html_emits_document_presence_without_fabricated_content(self) -> None:
        result = render_sec_packed_text(
            "<html><body></body></html>",
            "html",
            document_name="documents_list.htm",
            document_type="EX-99",
            form_type="C",
            text_kind="press_release_exhibit",
        )

        self.assertIn("Submitted document presence record", result.packed_text)
        self.assertIn("contains no renderable content", result.packed_text)
        self.assertIn("document_name=documents_list.htm", result.packed_text)
        self.assertIn("source_characters=26", result.packed_text)
        self.assertIn("document_presence_only", result.quality_flags)
        self.assertIn("html_structurally_empty_document", result.quality_flags)
        self.assertIn("no_renderable_content", result.quality_flags)
        self.assertNotIn("empty_rendered_text", result.quality_flags)

    def test_empty_xml_root_and_empty_source_emit_typed_presence_records(self) -> None:
        xml = render_sec_packed_text("<XBRL>\n</XBRL>", "xml", document_type="8-K")
        empty = render_sec_packed_text("", "plain_text", document_type="EX-99")

        self.assertIn("xml_structurally_empty_document", xml.quality_flags)
        self.assertIn("content_format=xml", xml.packed_text)
        self.assertIn("source_payload_empty", empty.quality_flags)
        self.assertIn("source_characters=0", empty.packed_text)

    def test_hidden_only_html_is_presence_only_and_legacy_table_text_is_preserved(self) -> None:
        hidden = render_sec_packed_text(
            "<html><body><div style='display:none'>Hidden metadata</div></body></html>",
            "html",
        )
        legacy_table = render_sec_packed_text("<html><body><table>Important text</table></body></html>", "html")

        self.assertIn("html_nonvisible_only_document", hidden.quality_flags)
        self.assertIn("document_presence_only", hidden.quality_flags)
        self.assertEqual(legacy_table.packed_text, "Important text")
        self.assertNotIn("empty_rendered_text", legacy_table.quality_flags)

    def test_legacy_sec_fixed_width_table_preserves_caption_headers_and_rows(self) -> None:
        source = """<TABLE><CAPTION>EXHIBIT A</CAPTION>
<S>                                      <C>
Fund                                     Effective Date
--------------------------------------   ----------------
First Trust Alpha Fund                   February 1, 2013
First Trust Beta Fund                    March 4, 2014
</TABLE>"""
        result = render_sec_packed_text(source, "html", document_type="EX-99.E UNDR CONTR", form_type="485BPOS")

        self.assertIn("Table: EXHIBIT A", result.packed_text)
        self.assertIn("Columns: Fund; Effective Date", result.packed_text)
        self.assertIn("Fund=First Trust Alpha Fund; Effective Date=February 1, 2013", result.packed_text)
        self.assertIn("Fund=First Trust Beta Fund; Effective Date=March 4, 2014", result.packed_text)
        self.assertIn("html_tables_rendered", result.quality_flags)

    def test_unclosed_html_table_is_finalized_before_empty_classification(self) -> None:
        result = render_sec_packed_text("<html><body><table><tr><td>Revenue<td>100", "html")

        self.assertIn("Revenue: 100", result.packed_text)
        self.assertNotIn("document_presence_only", result.quality_flags)

    def test_structured_fund_xml_is_rendered_with_tag_context(self) -> None:
        result = render_sec_packed_text(
            "<root><holding><name>Issuer</name></holding></root>",
            "xml",
            form_type="N-MFP3",
        )
        self.assertIn("<root/holding>", result.packed_text)
        self.assertIn("<holding/name>: Issuer", result.packed_text)
        self.assertNotIn("document_presence_only", result.quality_flags)

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

    def test_image_only_html_preserves_complete_image_inventory_without_claiming_ocr(self) -> None:
        source = "<html><head><title>Scanned agreement</title></head><body>" + "".join(
            f'<p><img src="agreement_{index:03d}.jpg" width="670" height="870"></p>'
            for index in range(1, 20)
        ) + "</body></html>"
        result = render_sec_packed_text(source, "html", document_type="EX-10.3", form_type="10-K")

        self.assertIn("Image-only HTML document", result.packed_text)
        self.assertIn("Document title: Scanned agreement", result.packed_text)
        self.assertIn("Image references: 19", result.packed_text)
        self.assertIn("Image 19: src=agreement_019.jpg; width=670; height=870", result.packed_text)
        self.assertIn("html_image_only_document", result.quality_flags)
        self.assertIn("image_content_not_ocr_extracted", result.quality_flags)
        self.assertNotIn("empty_rendered_text", result.quality_flags)

    def test_workiva_image_only_opinion_preserves_title_and_slide_names(self) -> None:
        source = """<HTML><HEAD><TITLE>determinationltr08252018</TITLE></HEAD><BODY>
        <IMG src="determinationltr08252018001.jpg" title="slide1" width="791" height="1024">
        <FONT size="1" style="font-size:1pt;color:white"> </FONT>
        <IMG src="determinationltr08252018002.jpg" title="slide2" width="791" height="1024">
        </BODY></HTML>"""
        result = render_sec_packed_text(source, "html", document_type="EX-5", form_type="S-8")

        self.assertIn("Document title: determinationltr08252018", result.packed_text)
        self.assertIn("Image references: 2", result.packed_text)
        self.assertIn("src=determinationltr08252018001.jpg; title=slide1", result.packed_text)
        self.assertIn("src=determinationltr08252018002.jpg; title=slide2", result.packed_text)


if __name__ == "__main__":
    unittest.main()
