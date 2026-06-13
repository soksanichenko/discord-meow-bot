"""Tests for Telegram relay HTML-to-Markdown conversion."""

from sources.lib.cogs.telegram_relay import _html_to_markdown


class TestHtmlToMarkdown:
    def test_plain_text(self):
        text, images = _html_to_markdown('Hello world')
        assert text == 'Hello world'
        assert images == []

    def test_bold_b(self):
        text, _ = _html_to_markdown('<b>bold</b>')
        assert text == '**bold**'

    def test_bold_strong(self):
        text, _ = _html_to_markdown('<strong>bold</strong>')
        assert text == '**bold**'

    def test_italic_i(self):
        text, _ = _html_to_markdown('<i>italic</i>')
        assert text == '*italic*'

    def test_italic_em(self):
        text, _ = _html_to_markdown('<em>italic</em>')
        assert text == '*italic*'

    def test_strikethrough(self):
        text, _ = _html_to_markdown('<s>strike</s>')
        assert text == '~~strike~~'

    def test_code(self):
        text, _ = _html_to_markdown('<code>code</code>')
        assert text == '`code`'

    def test_link_with_href(self):
        text, _ = _html_to_markdown('<a href="https://example.com">click</a>')
        assert text == '[click](https://example.com)'

    def test_link_without_href(self):
        text, _ = _html_to_markdown('<a>no href</a>')
        assert text == '[no href]'

    def test_br_becomes_newline(self):
        text, _ = _html_to_markdown('line1<br>line2')
        assert text == 'line1\nline2'

    def test_p_end_adds_newline(self):
        text, _ = _html_to_markdown('<p>para1</p><p>para2</p>')
        assert 'para1' in text
        assert 'para2' in text
        assert '\n' in text

    def test_image_extracted_not_in_text(self):
        text, images = _html_to_markdown('<img src="https://example.com/img.jpg">')
        assert images == ['https://example.com/img.jpg']
        assert text == ''

    def test_multiple_images_in_order(self):
        html = '<img src="https://a.com/1.jpg"><img src="https://b.com/2.jpg">'
        _, images = _html_to_markdown(html)
        assert images == ['https://a.com/1.jpg', 'https://b.com/2.jpg']

    def test_image_without_src_ignored(self):
        _, images = _html_to_markdown('<img alt="no src">')
        assert images == []

    def test_mixed_formatting(self):
        text, _ = _html_to_markdown('<b>bold</b> and <i>italic</i>')
        assert text == '**bold** and *italic*'

    def test_consecutive_newlines_collapsed(self):
        text, _ = _html_to_markdown('a<br><br><br>b')
        assert '\n\n\n' not in text
        assert 'a' in text
        assert 'b' in text

    def test_nested_link_and_bold(self):
        text, _ = _html_to_markdown('<a href="https://t.me/x"><b>Channel</b></a>')
        assert '[' in text
        assert '](https://t.me/x)' in text
        assert '**Channel**' in text

    def test_empty_string(self):
        text, images = _html_to_markdown('')
        assert text == ''
        assert images == []
