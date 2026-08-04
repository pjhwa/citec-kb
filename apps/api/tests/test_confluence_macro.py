from app.confluence.macro import extract_drawio_diagram_names, match_attachment_for_diagram

_SINGLE_MACRO_BODY = """
<p>intro</p>
<ac:structured-macro ac:name="drawio" ac:schema-version="1">
  <ac:parameter ac:name="diagramName">architecture</ac:parameter>
  <ac:parameter ac:name="revision">3</ac:parameter>
</ac:structured-macro>
<p>outro</p>
"""

_MULTI_MACRO_BODY = """
<ac:structured-macro ac:name="drawio">
  <ac:parameter ac:name="diagramName">network-topology</ac:parameter>
</ac:structured-macro>
<p>text between</p>
<ac:structured-macro ac:name="drawio">
  <ac:parameter ac:name="diagramName">deployment flow</ac:parameter>
</ac:structured-macro>
"""

_NO_MACRO_BODY = "<p>just text, no diagrams here</p>"

_MALFORMED_BODY = "<ac:structured-macro ac:name=\"drawio\"><ac:parameter ac:name=\"diagramName\">broken"


def test_extract_single_macro():
    assert extract_drawio_diagram_names(_SINGLE_MACRO_BODY) == ["architecture"]


def test_extract_multiple_macros():
    assert extract_drawio_diagram_names(_MULTI_MACRO_BODY) == [
        "network-topology",
        "deployment flow",
    ]


def test_extract_no_macros():
    assert extract_drawio_diagram_names(_NO_MACRO_BODY) == []


def test_extract_empty_body():
    assert extract_drawio_diagram_names("") == []


def test_extract_malformed_body_fails_soft():
    assert extract_drawio_diagram_names(_MALFORMED_BODY) == []


def test_match_attachment_exact_filename():
    attachments = [
        {"id": "att1", "title": "architecture.drawio"},
        {"id": "att2", "title": "unrelated.png"},
    ]
    match = match_attachment_for_diagram("architecture", attachments)
    assert match == {"id": "att1", "title": "architecture.drawio"}


def test_match_attachment_case_insensitive_stem_fallback():
    attachments = [{"id": "att3", "title": "Network-Topology.drawio"}]
    match = match_attachment_for_diagram("network-topology", attachments)
    assert match == {"id": "att3", "title": "Network-Topology.drawio"}


def test_match_attachment_none_found():
    attachments = [{"id": "att1", "title": "other.drawio"}]
    assert match_attachment_for_diagram("missing", attachments) is None


def test_match_attachment_extensionless_source_file():
    """This org's draw.io Confluence app stores the source attachment with NO
    extension at all (title == diagramName verbatim), alongside an
    auto-generated same-name .png preview and a '~name.tmp' lock file. Real
    production evidence: candidate_attachment_titles for a page returned
    ['MAZ 가용성 테스트 물리 구성도', 'MAZ 가용성 테스트 물리 구성도.png',
    '~MAZ 가용성 테스트 물리 구성도.tmp', 'MAZ 가용성 테스트 구성도', ...] —
    an exact '<name>.drawio' match and the extension-restricted stem fallback
    both correctly found nothing, because the real file simply has no suffix."""
    attachments = [
        {"id": "att-tmp", "title": "~MAZ 가용성 테스트 구성도.tmp"},
        {"id": "att-png", "title": "MAZ 가용성 테스트 구성도.png"},
        {"id": "att-source", "title": "MAZ 가용성 테스트 구성도"},
    ]
    match = match_attachment_for_diagram("MAZ 가용성 테스트 구성도", attachments)
    assert match == {"id": "att-source", "title": "MAZ 가용성 테스트 구성도"}


def test_match_attachment_prefers_drawio_extension_over_extensionless():
    """When both an explicit .drawio file and a same-name extensionless file
    exist, the explicit .drawio source should win (it's the more specific,
    unambiguous signal)."""
    attachments = [
        {"id": "att-bare", "title": "architecture"},
        {"id": "att-drawio", "title": "architecture.drawio"},
    ]
    match = match_attachment_for_diagram("architecture", attachments)
    assert match == {"id": "att-drawio", "title": "architecture.drawio"}


def test_match_attachment_stem_fallback_ignores_non_diagram_extensions():
    """draw.io Confluence apps commonly generate a same-stem .png preview
    alongside the .drawio source. If the .drawio title doesn't hit the exact
    match (renamed, revision suffix, ...), the stem fallback must not pick
    the binary .png/.jpg/.svg preview — that produces undecodable bytes
    downstream and crashes the caller."""
    attachments = [
        {"id": "att-png", "title": "architecture.png"},
        {"id": "att-drawio", "title": "Architecture.drawio.xml"},
    ]
    match = match_attachment_for_diagram("architecture", attachments)
    assert match is None


def test_match_attachment_stem_fallback_allows_xml_extension():
    attachments = [{"id": "att-xml", "title": "Architecture.xml"}]
    match = match_attachment_for_diagram("architecture", attachments)
    assert match == {"id": "att-xml", "title": "Architecture.xml"}
