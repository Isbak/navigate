from catalog.extraction import extract_links_from_text
from catalog.links.classifier import (
    classify,
    classify_link_kind,
    classify_target_system,
    classify_target_type,
)
from catalog.links.config import LinkConfig
from catalog.links.normalizer import normalize_url


# -- normalization -------------------------------------------------------------

def test_normalize_trims_and_lowercases_scheme_and_host():
    assert (
        normalize_url("  HTTPS://Example.COM/Path/To  ")
        == "https://example.com/Path/To"
    )


def test_normalize_strips_trailing_punctuation():
    assert normalize_url("https://example.com/page).") == "https://example.com/page"
    assert normalize_url("https://example.com/a,") == "https://example.com/a"


def test_normalize_removes_tracking_parameters():
    out = normalize_url(
        "https://example.com/p?utm_source=news&id=42&utm_campaign=x&utm_medium=email"
    )
    assert out == "https://example.com/p?id=42"


def test_normalize_preserves_content_query_parameters():
    out = normalize_url("https://contoso.sharepoint.com/x?d=w123&csf=1")
    assert "d=w123" in out
    assert "csf=1" in out


def test_normalize_drops_plain_fragment_but_keeps_route_fragment():
    assert normalize_url("https://example.com/p#section") == "https://example.com/p"
    assert (
        normalize_url("https://example.com/app#/board/42")
        == "https://example.com/app#/board/42"
    )


def test_normalize_mailto_lowercases_and_drops_subject():
    assert normalize_url("mailto:John.Doe@Example.COM?subject=Hi") == "mailto:john.doe@example.com"


def test_normalize_file_url_and_local_path():
    assert normalize_url("FILE://Host/Some/Path").startswith("file://host/Some/Path")
    assert normalize_url("/home/user/report.pdf") == "file:///home/user/report.pdf"
    assert normalize_url(r"C:\docs\report.docx") == "file:///C:/docs/report.docx"


# -- target system classification ----------------------------------------------

def test_classify_sharepoint():
    assert classify_target_system("https://contoso.sharepoint.com/sites/x") == "sharepoint"
    assert classify_target_system("https://contoso.sharepoint.com/:w:/r/sites/a/doc.docx") == "sharepoint"


def test_classify_onedrive():
    assert classify_target_system("https://onedrive.live.com/?id=1") == "onedrive"
    assert classify_target_system("https://1drv.ms/f/s!abc") == "onedrive"
    assert classify_target_system("https://contoso-my.sharepoint.com/personal/a_b/Documents") == "onedrive"


def test_classify_confluence():
    assert classify_target_system("https://contoso.atlassian.net/wiki/spaces/ENG/pages/1") == "confluence"
    assert classify_target_system("https://confluence.contoso.com/display/ENG/Home") == "confluence"
    assert classify_target_system("https://docs.example.com/wiki/page") == "confluence"


def test_classify_jira():
    assert classify_target_system("https://contoso.atlassian.net/browse/ABC-1") == "jira"
    assert classify_target_system("https://jira.contoso.com/browse/ABC-1") == "jira"


def test_classify_azure_devops():
    assert classify_target_system("https://dev.azure.com/org/project") == "azure_devops"
    assert classify_target_system("https://contoso.visualstudio.com/p/_git/repo") == "azure_devops"
    assert classify_target_system("https://dev.azure.com/org/proj/_workitems/edit/5") == "azure_devops"


def test_classify_github():
    assert classify_target_system("https://github.com/openai/openai-python") == "github"


def test_classify_teams():
    assert classify_target_system("https://teams.microsoft.com/l/channel/abc") == "teams"
    assert classify_target_system("https://teams.microsoft.com/l/message/19:xyz") == "teams"


def test_classify_mailto():
    assert classify_target_system("mailto:a@b.com") == "email"


def test_classify_local_file():
    assert classify_target_system("file:///home/user/x.docx") == "local_file"
    assert classify_target_system("/home/user/report.pdf") == "local_file"


def test_classify_external_and_unknown():
    assert classify_target_system("https://example.com") == "external_web"
    assert classify_target_system("not-a-url") == "unknown"


# -- target type classification ------------------------------------------------

def test_target_type_sharepoint_document():
    assert classify_target_type("https://contoso.sharepoint.com/:w:/r/sites/a/d.docx", "sharepoint") == "document"
    assert classify_target_type("https://contoso.sharepoint.com/:x:/r/sites/a/s.xlsx", "sharepoint") == "document"


def test_target_type_ado():
    assert classify_target_type("https://dev.azure.com/o/p/_workitems/edit/5", "azure_devops") == "work_item"
    assert classify_target_type("https://dev.azure.com/o/p/_git/repo", "azure_devops") == "repository"
    assert classify_target_type("https://dev.azure.com/o/p/_wiki/wikis/w", "azure_devops") == "wiki_page"


def test_target_type_github_pull_request_and_repo():
    assert classify_target_type("https://github.com/acme/repo/pull/12", "github") == "pull_request"
    assert classify_target_type("https://github.com/acme/repo", "github") == "repository"


def test_target_type_teams():
    assert classify_target_type("https://teams.microsoft.com/l/message/19:abc", "teams") == "message"
    assert classify_target_type("https://teams.microsoft.com/l/channel/19:abc", "teams") == "channel"


def test_target_type_email():
    assert classify_target_type("mailto:a@b.com", "email") == "email_address"


# -- link kind -----------------------------------------------------------------

def test_link_kind_internal_external_local_email():
    assert classify_link_kind("https://contoso.sharepoint.com/sites/x", "sharepoint") == "internal"
    assert classify_link_kind("https://example.com", "external_web") == "external"
    assert classify_link_kind("file:///home/x.txt", "local_file") == "local"
    assert classify_link_kind("mailto:a@b.com", "email") == "email"


def test_link_kind_github_external_by_default_internal_via_config():
    assert classify_link_kind("https://github.com/acme/repo", "github") == "external"
    cfg = LinkConfig(internal_domains=("github.com/acme",))
    assert classify_link_kind("https://github.com/acme/repo", "github", cfg) == "internal"


def test_classify_combined():
    result = classify("https://github.com/acme/repo/pull/9")
    assert result.target_system == "github"
    assert result.target_type == "pull_request"
    assert result.link_kind == "external"


# -- raw extraction (extraction layer) -----------------------------------------

def test_extract_links_from_text():
    links = extract_links_from_text(
        "See [GitHub](https://github.com/acme/repo) and https://example.com."
    )
    urls = {item["raw_url"] for item in links}
    assert "https://github.com/acme/repo" in urls
    anchor = next(i for i in links if i["raw_url"] == "https://github.com/acme/repo")["anchor_text"]
    assert anchor == "GitHub"
