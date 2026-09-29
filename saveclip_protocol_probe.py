from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SENSITIVE_KEYS = {"token", "k_token", "k_exp", "cftoken", "authorization", "cookie"}


def body_keys(post_data: str | None) -> list[str]:
    if not post_data:
        return []
    try:
        return sorted(parse_qs(post_data, keep_blank_values=True).keys())
    except Exception:
        return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe a normal SaveClip browser request and write a sanitized protocol report."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9223")
    parser.add_argument("--output", default="saveclip-protocol-report.json")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    report: dict = {"requests": [], "responses": []}

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(args.cdp_url)
        contexts = browser.contexts
        if not contexts:
            raise SystemExit("No CDP browser context is available.")
        pages = [page for context in contexts for page in context.pages]
        page = next((page for page in pages if "saveclip.app" in page.url), None)
        if page is None:
            raise SystemExit("Open SaveClip in the connected Chrome before running the probe.")

        def on_request(request):
            parsed = urlsplit(request.url)
            if parsed.hostname and "saveclip.app" not in parsed.hostname:
                return
            if request.resource_type not in {"xhr", "fetch", "document"}:
                return
            keys = body_keys(request.post_data)
            report["requests"].append(
                {
                    "method": request.method,
                    "hostname": parsed.hostname,
                    "pathname": parsed.path,
                    "resource_type": request.resource_type,
                    "request_body_keys": keys,
                    "sensitive_key_present": any(key.lower() in SENSITIVE_KEYS for key in keys),
                }
            )

        def on_response(response):
            parsed = urlsplit(response.url)
            if parsed.hostname and "saveclip.app" not in parsed.hostname:
                return
            request = response.request
            if request.resource_type not in {"xhr", "fetch", "document"}:
                return
            content_type = response.headers.get("content-type", "")
            entry = {
                "status": response.status,
                "hostname": parsed.hostname,
                "pathname": parsed.path,
                "content_type": content_type,
            }
            try:
                body = response.body()
                entry["response_size"] = len(body)
                if "json" in content_type:
                    payload = json.loads(body.decode("utf-8", errors="replace"))
                    entry["top_level_json_keys"] = (
                        sorted(payload.keys()) if isinstance(payload, dict) else []
                    )
            except Exception:
                pass
            report["responses"].append(entry)

        page.on("request", on_request)
        page.on("response", on_response)

        print("Probe attached. Submit one normal Story permalink in the open SaveClip tab.")
        print("Press Enter here after the result cards are visible.")
        input()

    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Sanitized report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
