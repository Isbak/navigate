from catalog.links import classify_target_system, extract_links_from_text


def test_classify_target_systems():
    assert classify_target_system("https://contoso.sharepoint.com/sites/x") == "sharepoint"
    assert classify_target_system("https://1drv.ms/f/s!abc") == "onedrive"
    assert classify_target_system("https://docs.example.com/wiki/page") == "confluence/wiki"
    assert classify_target_system("https://contoso.atlassian.net/browse/ABC-1") == "jira"
    assert classify_target_system("https://dev.azure.com/org/project") == "azure_devops"
    assert classify_target_system("https://github.com/openai/openai-python") == "github"
    assert classify_target_system("https://teams.microsoft.com/l/channel/abc") == "teams"
    assert classify_target_system("https://example.com") == "external"
    assert classify_target_system("not-a-url") == "unknown"


def test_extract_links_from_text():
    links = extract_links_from_text("See [GitHub](https://github.com/acme/repo) and https://example.com.")
    assert {item["target_url"] for item in links} == {"https://github.com/acme/repo", "https://example.com"}
    assert next(item for item in links if item["target_url"] == "https://github.com/acme/repo")["anchor_text"] == "GitHub"
