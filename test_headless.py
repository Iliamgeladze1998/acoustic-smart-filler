#!/usr/bin/env python3
"""
Headless multi-product test for Acoustic Smart Filler.
Connects to debug Chrome on 127.0.0.1:9222, logs in to acoustic.ge admin,
opens each product edit page, scrapes, generates AI content, fills the form,
and reports results per product. Does NOT click Save.

Usage:
  python3 test_headless.py                    # test default product 15650
  python3 test_headless.py 15650 15651 15652  # test multiple products
  TEST_PRODUCT_ID=15650 python3 test_headless.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure App dir is on path
APP_DIR = Path(__file__).resolve().parent / "App"
sys.path.insert(0, str(APP_DIR))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from config_loader import load_config
from ai_generate import generate_product_fields
from page_scripts import (
    open_product_edit,
    scan_product_page,
    apply_product_fill,
    verify_product_form_filled,
)
from web_search import search_product_info

# --- Config ---
ADMIN_LOGIN = os.environ.get("ACOUSTIC_ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.environ.get("ACOUSTIC_ADMIN_PASSWORD", "")
ADMIN_URL = "https://acoustic.ge/aco_st_admin.php"
LOGIN_URL = "https://acoustic.ge/aco_st_admin.php?dispatch=auth.login"
DEBUG_ADDRESS = "127.0.0.1:9222"

FIELDS = [
    "product_name", "price", "old_price", "tags",
    "full_description", "promo_text", "seo_name", "page_title",
    "meta_description", "meta_keywords", "categories", "features", "videos",
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def attach_chrome() -> webdriver.Chrome:
    log(f"Attaching to debug Chrome at {DEBUG_ADDRESS}…")
    options = Options()
    options.add_experimental_option("debuggerAddress", DEBUG_ADDRESS)
    options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(180)
    log(f"Attached. Current URL: {driver.current_url}")
    return driver


def is_logged_in(driver) -> bool:
    log("Checking login status…")
    driver.get(ADMIN_URL)
    time.sleep(3)
    url = driver.current_url or ""
    if "dispatch=auth.login" in url or "/login" in url:
        return False
    try:
        has_login_form = driver.execute_script(
            "return !!(document.querySelector('input[name=\"user_login\"]') "
            "|| document.querySelector('form[name=\"login_form\"]'));"
        )
        return not has_login_form
    except Exception:
        return True


def login(driver) -> None:
    log("Not logged in. Navigating to login page…")
    driver.get(LOGIN_URL)
    time.sleep(2)
    try:
        login_field = driver.execute_script(
            "return document.querySelector('input[name=\"user_login\"]');"
        ) or driver.execute_script(
            "return document.querySelector('input#login_login');"
        )
        pass_field = driver.execute_script(
            "return document.querySelector('input[name=\"password\"]');"
        ) or driver.execute_script(
            "return document.querySelector('input#login_password');"
        )
        if not login_field or not pass_field:
            raise RuntimeError("Could not find login/password fields on login page")
        login_field.clear()
        login_field.send_keys(ADMIN_LOGIN)
        pass_field.clear()
        pass_field.send_keys(ADMIN_PASSWORD)
        driver.execute_script(
            "var btn = document.querySelector('button[type=\"submit\"], input[type=\"submit\"], .btn-primary');"
            "if (btn) btn.click();"
        )
        time.sleep(4)
        log(f"After login: {driver.current_url}")
        if "dispatch=auth.login" in (driver.current_url or ""):
            raise RuntimeError("Login failed — still on login page after submit")
        log("Login successful.")
    except Exception as exc:
        raise RuntimeError(f"Login failed: {exc}") from exc


def test_product(driver, product_id: int, config: dict) -> dict:
    """Run full scrape → web search → AI → fill cycle for one product."""
    product_url = f"{ADMIN_URL}?dispatch=products.update&product_id={product_id}"
    api_key = config.get("openai_api_key", "").strip()
    model = config.get("openai_model", "gpt-4o-mini").strip()
    language = config.get("content_language", "Georgian").strip()

    log(f"\n{'='*60}")
    log(f"  PRODUCT #{product_id}")
    log(f"{'='*60}")

    # Open product edit page
    log(f"Opening product edit page (product_id={product_id})…")
    final_url = open_product_edit(driver, product_url, timeout_s=30)
    log(f"Product page loaded: {final_url}")

    # Scan
    log("Scanning product page…")
    page_context = scan_product_page(driver, product_url, progress_cb=log)
    product_title = page_context.get("product_name") or page_context.get("title") or ""
    log(f"Product title: {product_title!r}")

    if not product_title.strip():
        log("ERROR: Product title is empty — cannot generate content.")
        return {"product_id": product_id, "ok": False, "error": "empty_title"}

    # Web search
    log(f"Searching web for product specs: {product_title!r}…")
    t0 = time.perf_counter()
    web_context = search_product_info(product_title)
    web_elapsed = time.perf_counter() - t0
    if web_context:
        log(f"Web search: {len(web_context)} chars in {web_elapsed:.1f}s")
    else:
        log(f"Web search: nothing found in {web_elapsed:.1f}s (AI-only)")

    # AI generate
    log("Generating AI content…")
    t0 = time.perf_counter()
    ai_data = generate_product_fields(
        api_key=api_key,
        model=model,
        content_language=language,
        page_context=page_context,
        product_title=product_title,
        web_context=web_context,
    )
    ai_elapsed = time.perf_counter() - t0
    log(f"AI generation done in {ai_elapsed:.1f}s")

    # Print AI summary
    log("--- AI GENERATED ---")
    for key in FIELDS:
        val = ai_data.get(key)
        if val:
            val_str = str(val)
            if len(val_str) > 150:
                val_str = val_str[:150] + "…"
            log(f"  {key}: {val_str}")
        elif key in ("old_price", "tags"):
            log(f"  {key}: (empty)")

    # Fill
    log("Filling product form (NOT saving)…")
    fill_result = apply_product_fill(driver, ai_data, product_url)
    log("--- FILL RESULT ---")
    for key in FIELDS:
        res = fill_result.get(key)
        if isinstance(res, dict):
            ok = res.get("ok") or res.get("matched", 0)
            preview = res.get("preview", "")
            reason = res.get("reason", "")
            status = "✓" if ok else "✗"
            if preview and len(preview) > 80:
                preview = preview[:80] + "…"
            line = f"  {status} {key}: ok={ok}"
            if preview:
                line += f"  preview={preview!r}"
            if reason and not ok:
                line += f"  reason={reason}"
            log(line)
        elif isinstance(res, list):
            log(f"  ? {key}: {len(res)} items")

    # Verify
    verify = verify_product_form_filled(driver)
    log(f"Verification: ok={verify.get('ok')}, name={verify.get('name', '')!r}")

    # Save artifacts
    tag = f"pid{product_id}"
    for suffix, data in [("scan", page_context), ("ai", ai_data), ("fill", fill_result)]:
        path = APP_DIR / f"test_{tag}_{suffix}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # Summary
    filled = sum(1 for k in FIELDS if isinstance(fill_result.get(k), dict) and (fill_result[k].get("ok") or fill_result[k].get("matched", 0)))
    log(f"--- SUMMARY: {filled}/{len(FIELDS)} fields filled ---")
    return {
        "product_id": product_id,
        "ok": verify.get("ok", False),
        "title": product_title,
        "filled": filled,
        "total": len(FIELDS),
        "categories": ai_data.get("categories", []),
        "brand": (ai_data.get("features") or {}).get("ბრენდი", ""),
        "ai_elapsed": ai_elapsed,
        "web_elapsed": web_elapsed,
    }


def main() -> int:
    # Parse product IDs from args
    if len(sys.argv) > 1:
        product_ids = [int(a) for a in sys.argv[1:] if a.isdigit()]
    else:
        env_id = os.environ.get("TEST_PRODUCT_ID", "15650")
        product_ids = [int(env_id)]

    if not product_ids:
        log("ERROR: No product IDs specified")
        return 1

    config = load_config()
    if not config.get("openai_api_key", "").strip():
        log("ERROR: No OpenAI API key in config.json")
        return 1

    log(f"Config: model={config.get('openai_model')}, language={config.get('content_language')}")
    log(f"Products to test: {product_ids}")

    driver = attach_chrome()

    if not is_logged_in(driver):
        login(driver)
    else:
        log("Already logged in.")

    results = []
    for pid in product_ids:
        try:
            r = test_product(driver, pid, config)
            results.append(r)
        except Exception as exc:
            log(f"ERROR on product #{pid}: {exc}")
            results.append({"product_id": pid, "ok": False, "error": str(exc)})

    # Final summary
    log(f"\n{'='*60}")
    log(f"  FINAL SUMMARY ({len(results)} products)")
    log(f"{'='*60}")
    for r in results:
        pid = r["product_id"]
        if r.get("ok"):
            log(f"  ✓ #{pid}: {r.get('title', '')[:50]}  filled={r['filled']}/{r['total']}  "
                f"cats={r.get('categories', [])}  brand={r.get('brand', '')}  "
                f"ai={r.get('ai_elapsed', 0):.1f}s web={r.get('web_elapsed', 0):.1f}s")
        else:
            log(f"  ✗ #{pid}: FAILED — {r.get('error', 'unknown')}")
    ok_count = sum(1 for r in results if r.get("ok"))
    log(f"\n  {ok_count}/{len(results)} products passed")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
