"""Unit tests for the Docling → Unstructured element mapper.

Tests map_docling_to_elements and all private helper functions:
_build_ref_lookup, _table_to_text, _table_to_html, _get_page_number,
_make_element.
"""

import pytest

from document_parsing.docling_adapter import (
    _build_ref_lookup,
    _get_page_number,
    _make_element,
    _table_to_html,
    _table_to_text,
    map_docling_to_elements,
)

pytestmark = pytest.mark.unit

# ──────────────────────────────────────────────────────────────────────
# Shared fixture — realistic DoclingDocument response
# ──────────────────────────────────────────────────────────────────────

SAMPLE_DOCLING_RESPONSE: dict = {
    'filename': 'test.pdf',
    'md_content': '# Title\n\nSome text',
    'json_content': {
        'schema_name': 'DoclingDocument',
        'version': '1.3.0',
        'name': 'test.pdf',
        'body': {
            'self_ref': '#/body',
            'children': [
                {'$ref': '#/texts/0'},
                {'$ref': '#/texts/1'},
                {'$ref': '#/tables/0'},
                {'$ref': '#/pictures/0'},
                {'$ref': '#/groups/0'},
            ],
        },
        'texts': [
            {
                'self_ref': '#/texts/0',
                'label': 'section_header',
                'text': 'Introduction',
                'orig': 'Introduction',
                'prov': [{'page_no': 1, 'bbox': {'l': 72, 't': 100, 'r': 540, 'b': 120}}],
                'children': [],
            },
            {
                'self_ref': '#/texts/1',
                'label': 'paragraph',
                'text': 'This is a paragraph.',
                'orig': 'This is a paragraph.',
                'prov': [{'page_no': 1, 'bbox': {'l': 72, 't': 130, 'r': 540, 'b': 150}}],
                'children': [],
            },
            {
                'self_ref': '#/texts/2',
                'label': 'list_item',
                'text': 'Item one',
                'orig': 'Item one',
                'prov': [{'page_no': 2}],
                'children': [],
            },
            {
                'self_ref': '#/texts/3',
                'label': 'list_item',
                'text': 'Item two',
                'orig': 'Item two',
                'prov': [{'page_no': 2}],
                'children': [],
            },
        ],
        'tables': [
            {
                'self_ref': '#/tables/0',
                'label': 'table',
                'prov': [{'page_no': 1}],
                'data': {
                    'num_rows': 2,
                    'num_cols': 2,
                    'table_cells': [
                        {
                            'text': 'Name',
                            'start_row_offset_idx': 0,
                            'end_row_offset_idx': 1,
                            'start_col_offset_idx': 0,
                            'end_col_offset_idx': 1,
                            'column_header': True,
                        },
                        {
                            'text': 'Value',
                            'start_row_offset_idx': 0,
                            'end_row_offset_idx': 1,
                            'start_col_offset_idx': 1,
                            'end_col_offset_idx': 2,
                            'column_header': True,
                        },
                        {
                            'text': 'A',
                            'start_row_offset_idx': 1,
                            'end_row_offset_idx': 2,
                            'start_col_offset_idx': 0,
                            'end_col_offset_idx': 1,
                            'column_header': False,
                        },
                        {
                            'text': '1',
                            'start_row_offset_idx': 1,
                            'end_row_offset_idx': 2,
                            'start_col_offset_idx': 1,
                            'end_col_offset_idx': 2,
                            'column_header': False,
                        },
                    ],
                    'grid': [['', ''], ['', '']],
                },
                'children': [],
            },
        ],
        'pictures': [
            {
                'self_ref': '#/pictures/0',
                'label': 'picture',
                'prov': [{'page_no': 2}],
                'image': {
                    'mimetype': 'image/png',
                    'dpi': 144,
                    'size': {'width': 400, 'height': 300},
                    'uri': 'data:image/png;base64,iVBORw0KGgo=',
                },
                'children': [],
            },
        ],
        'groups': [
            {
                'self_ref': '#/groups/0',
                'label': 'list',
                'children': [
                    {'$ref': '#/texts/2'},
                    {'$ref': '#/texts/3'},
                ],
            },
        ],
        'pages': {'1': {'size': {'width': 612, 'height': 792}}},
    },
}


# ──────────────────────────────────────────────────────────────────────
# map_docling_to_elements
# ──────────────────────────────────────────────────────────────────────


class TestMapDoclingToElements:
    def _elements(self) -> list:
        return map_docling_to_elements(SAMPLE_DOCLING_RESPONSE, 'test.pdf')

    def test_produces_correct_number_of_elements(self):
        # section_header + paragraph + table + picture + 2 list_items = 6
        elements = self._elements()
        assert len(elements) == 6

    def test_title_element_type(self):
        elements = self._elements()
        assert elements[0]['type'] == 'Title'
        assert elements[0]['text'] == 'Introduction'

    def test_narrative_text_element_type(self):
        elements = self._elements()
        assert elements[1]['type'] == 'NarrativeText'
        assert elements[1]['text'] == 'This is a paragraph.'

    def test_table_element_type(self):
        elements = self._elements()
        table_el = next(e for e in elements if e['type'] == 'Table')
        assert table_el is not None

    def test_table_has_text_as_html_in_metadata(self):
        elements = self._elements()
        table_el = next(e for e in elements if e['type'] == 'Table')
        assert 'text_as_html' in table_el['metadata']
        assert '<table>' in table_el['metadata']['text_as_html']

    def test_table_text_is_markdown(self):
        elements = self._elements()
        table_el = next(e for e in elements if e['type'] == 'Table')
        assert '|' in table_el['text']
        assert 'Name' in table_el['text']

    def test_image_element_type(self):
        elements = self._elements()
        img_el = next(e for e in elements if e['type'] == 'Image')
        assert img_el is not None

    def test_image_has_base64_metadata(self):
        elements = self._elements()
        img_el = next(e for e in elements if e['type'] == 'Image')
        assert 'image_base64' in img_el['metadata']
        assert img_el['metadata']['image_base64'] == 'iVBORw0KGgo='

    def test_image_has_filetype_metadata(self):
        elements = self._elements()
        img_el = next(e for e in elements if e['type'] == 'Image')
        assert img_el['metadata']['filetype'] == 'image/png'

    def test_list_items_included_from_group(self):
        elements = self._elements()
        list_items = [e for e in elements if e['type'] == 'ListItem']
        assert len(list_items) == 2
        texts = {e['text'] for e in list_items}
        assert texts == {'Item one', 'Item two'}

    def test_page_number_populated(self):
        elements = self._elements()
        assert elements[0]['metadata']['page_number'] == 1

    def test_filename_in_all_element_metadata(self):
        elements = self._elements()
        for el in elements:
            assert el['metadata']['filename'] == 'test.pdf'

    def test_empty_response_returns_empty_list(self):
        result = map_docling_to_elements({}, 'test.pdf')
        assert result == []

    def test_response_with_no_json_content_returns_empty_list(self):
        result = map_docling_to_elements({'md_content': '# Title'}, 'test.pdf')
        assert result == []

    def test_elements_in_body_order(self):
        elements = self._elements()
        # Title comes before NarrativeText in the body children list
        types = [e['type'] for e in elements]
        title_idx = types.index('Title')
        narrative_idx = types.index('NarrativeText')
        assert title_idx < narrative_idx


# ──────────────────────────────────────────────────────────────────────
# _build_ref_lookup
# ──────────────────────────────────────────────────────────────────────


class TestBuildRefLookup:
    def test_builds_lookup_from_texts(self):
        json_content = {
            'texts': [{'self_ref': '#/texts/0', 'label': 'paragraph', 'text': 'Hello'}],
            'tables': [],
            'pictures': [],
            'groups': [],
        }
        lookup = _build_ref_lookup(json_content)
        assert '#/texts/0' in lookup
        assert lookup['#/texts/0']['text'] == 'Hello'

    def test_builds_lookup_from_tables(self):
        json_content = {
            'texts': [],
            'tables': [{'self_ref': '#/tables/0', 'label': 'table'}],
            'pictures': [],
            'groups': [],
        }
        lookup = _build_ref_lookup(json_content)
        assert '#/tables/0' in lookup

    def test_builds_lookup_from_pictures(self):
        json_content = {
            'texts': [],
            'tables': [],
            'pictures': [{'self_ref': '#/pictures/0', 'label': 'picture'}],
            'groups': [],
        }
        lookup = _build_ref_lookup(json_content)
        assert '#/pictures/0' in lookup

    def test_includes_groups_for_recursion(self):
        json_content = {
            'texts': [],
            'tables': [],
            'pictures': [],
            'groups': [{'self_ref': '#/groups/0', 'label': 'list', 'children': []}],
        }
        lookup = _build_ref_lookup(json_content)
        assert '#/groups/0' in lookup

    def test_handles_empty_collections(self):
        lookup = _build_ref_lookup({})
        assert lookup == {}


# ──────────────────────────────────────────────────────────────────────
# _table_to_text
# ──────────────────────────────────────────────────────────────────────


class TestTableToText:
    def test_renders_markdown_table(self):
        table_item = SAMPLE_DOCLING_RESPONSE['json_content']['tables'][0]
        text = _table_to_text(table_item)
        assert '| Name | Value |' in text
        assert '| --- | --- |' in text
        assert '| A | 1 |' in text

    def test_header_separator_after_first_row(self):
        table_item = SAMPLE_DOCLING_RESPONSE['json_content']['tables'][0]
        lines = _table_to_text(table_item).splitlines()
        assert lines[1].startswith('|')
        assert '---' in lines[1]

    def test_empty_data_returns_empty_string(self):
        assert _table_to_text({}) == ''
        assert _table_to_text({'data': {}}) == ''

    def test_zero_rows_returns_empty_string(self):
        assert _table_to_text({'data': {'num_rows': 0, 'num_cols': 2}}) == ''


# ──────────────────────────────────────────────────────────────────────
# _table_to_html
# ──────────────────────────────────────────────────────────────────────


class TestTableToHtml:
    def test_renders_html_table(self):
        table_item = SAMPLE_DOCLING_RESPONSE['json_content']['tables'][0]
        html = _table_to_html(table_item)
        assert html.startswith('<table>')
        assert html.endswith('</table>')

    def test_header_cells_use_th_tag(self):
        table_item = SAMPLE_DOCLING_RESPONSE['json_content']['tables'][0]
        html = _table_to_html(table_item)
        assert '<th>Name</th>' in html
        assert '<th>Value</th>' in html

    def test_data_cells_use_td_tag(self):
        table_item = SAMPLE_DOCLING_RESPONSE['json_content']['tables'][0]
        html = _table_to_html(table_item)
        assert '<td>A</td>' in html
        assert '<td>1</td>' in html

    def test_colspan_attribute_added(self):
        table_item = {
            'data': {
                'num_rows': 1,
                'table_cells': [
                    {
                        'text': 'Merged',
                        'start_row_offset_idx': 0,
                        'end_row_offset_idx': 1,
                        'start_col_offset_idx': 0,
                        'end_col_offset_idx': 3,
                        'column_header': False,
                    }
                ],
            }
        }
        html = _table_to_html(table_item)
        assert 'colspan="3"' in html

    def test_rowspan_attribute_added(self):
        table_item = {
            'data': {
                'num_rows': 2,
                'table_cells': [
                    {
                        'text': 'Tall',
                        'start_row_offset_idx': 0,
                        'end_row_offset_idx': 2,
                        'start_col_offset_idx': 0,
                        'end_col_offset_idx': 1,
                        'column_header': False,
                    }
                ],
            }
        }
        html = _table_to_html(table_item)
        assert 'rowspan="2"' in html

    def test_empty_data_returns_empty_string(self):
        assert _table_to_html({}) == ''
        assert _table_to_html({'data': {'num_rows': 1, 'table_cells': []}}) == ''


# ──────────────────────────────────────────────────────────────────────
# _get_page_number
# ──────────────────────────────────────────────────────────────────────


class TestGetPageNumber:
    def test_extracts_page_no_from_prov(self):
        item = {'prov': [{'page_no': 3}]}
        assert _get_page_number(item) == 3

    def test_returns_first_prov_entry(self):
        item = {'prov': [{'page_no': 1}, {'page_no': 2}]}
        assert _get_page_number(item) == 1

    def test_returns_none_for_empty_prov(self):
        assert _get_page_number({'prov': []}) is None

    def test_returns_none_for_missing_prov(self):
        assert _get_page_number({}) is None

    def test_returns_none_when_page_no_absent(self):
        assert _get_page_number({'prov': [{'bbox': {}}]}) is None


# ──────────────────────────────────────────────────────────────────────
# _make_element
# ──────────────────────────────────────────────────────────────────────


class TestMakeElement:
    def test_returns_none_for_empty_text_non_image(self):
        item = {'text': '', 'orig': '', 'prov': []}
        assert _make_element(item, 'NarrativeText', 'doc.pdf') is None

    def test_returns_element_for_non_empty_text(self):
        item = {'text': 'Hello', 'orig': 'Hello', 'prov': [{'page_no': 1}]}
        el = _make_element(item, 'NarrativeText', 'doc.pdf')
        assert el is not None
        assert el['text'] == 'Hello'
        assert el['type'] == 'NarrativeText'

    def test_image_element_returned_with_no_text(self):
        item = {
            'text': '',
            'prov': [{'page_no': 1}],
            'image': {'mimetype': 'image/png', 'uri': 'data:image/png;base64,abc='},
        }
        el = _make_element(item, 'Image', 'doc.pdf')
        assert el is not None
        assert el['type'] == 'Image'

    def test_filename_always_in_metadata(self):
        item = {'text': 'Content', 'prov': []}
        el = _make_element(item, 'Title', 'myfile.pdf')
        assert el['metadata']['filename'] == 'myfile.pdf'

    def test_page_number_in_metadata_when_present(self):
        item = {'text': 'Content', 'prov': [{'page_no': 5}]}
        el = _make_element(item, 'Title', 'doc.pdf')
        assert el['metadata']['page_number'] == 5

    def test_no_page_number_key_when_absent(self):
        item = {'text': 'Content', 'prov': []}
        el = _make_element(item, 'Title', 'doc.pdf')
        assert 'page_number' not in el['metadata']
