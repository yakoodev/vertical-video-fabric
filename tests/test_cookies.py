from app.cookies import (
    parse_cookie_input,
    required_cookie_status,
    tiktok_caption,
    to_cookie_header,
    to_cookie_header_for_host,
)


def test_parse_raw_cookie_header():
    cookies = parse_cookie_input("Cookie: SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e", "youtube")
    assert [cookie.name for cookie in cookies] == ["SID", "HSID", "SSID", "APISID", "SAPISID"]
    assert to_cookie_header(cookies) == "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e"
    ok, missing = required_cookie_status("youtube", cookies)
    assert ok
    assert missing == []


def test_parse_raw_cookie_header_deduplicates_using_latest_value():
    raw = "\n".join(
        [
            "Cookie: SID=old; HSID=old; SAPISID=old",
            "Cookie: SID=new; APISID=new; SAPISID=new; SSID=new",
        ]
    )

    cookies = parse_cookie_input(raw, "youtube")

    assert to_cookie_header(cookies) == "SID=new; HSID=old; SAPISID=new; APISID=new; SSID=new"


def test_parse_netscape_cookie():
    raw = ".tiktok.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\tabc\n"
    cookies = parse_cookie_input(raw, "tiktok")
    assert cookies[0].domain == ".tiktok.com"
    assert cookies[0].expires == 2147483647
    ok, missing = required_cookie_status("tiktok", cookies)
    assert ok
    assert missing == []


def test_cookie_header_for_host_filters_domains():
    raw = "\n".join(
        [
            ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tyoutube",
            ".google.com\tTRUE\t/\tTRUE\t2147483647\tSID\tgoogle",
        ]
    )
    cookies = parse_cookie_input(raw, "youtube")
    assert to_cookie_header_for_host(cookies, "studio.youtube.com") == "SID=youtube"
    assert to_cookie_header_for_host(cookies, "accounts.google.com") == "SID=google"


def test_tiktok_caption_truncates():
    caption = tiktok_caption("t", "x" * 3000)
    assert caption.startswith("t\n\n")
    assert len(caption) == 2200
