#!/usr/bin/env python3
"""Offline check of the category classifier against the real acoustic.ge tree.

Feeds the Georgian family/type hints the model is asked to produce and asserts
the deterministic mapping lands on the expected category. No API calls.

Usage:  python3 test_category_logic.py
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "App"
sys.path.insert(0, str(APP_DIR))

from category_match import classify, compact_tree_prompt  # noqa: E402


def load_catalog() -> list[dict]:
    files = sorted(glob.glob(str(APP_DIR / "scrape_cache" / "categories_*.json")))
    if not files:
        raise SystemExit("No cached category tree found in App/scrape_cache/")
    data = json.load(open(files[0], encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    return [
        {
            "id": str(x.get("id") or x.get("value") or ""),
            "label": str(x.get("label") or ""),
            "parent_id": str(x.get("parent_id") or ""),
            "path": str(x.get("path") or x.get("label") or ""),
        }
        for x in items
        if isinstance(x, dict)
    ]


# title, family hint (ka), type hint (ka), acceptable results
CASES: list[tuple[str, str, str, tuple[str, ...]]] = [
    # --- the four previously tested products ---
    ("Electro Harmonix String9 String Ensemble", "გიტარა", "გიტარის ეფექტების პედალი",
     ("ეფექტები",)),
    ("Harley Benton SolidBass 410T bass cabinet", "ბასი", "ბას გიტარის კაბინეტი გამაძლიერებელი",
     ("გამაძლიერებელი/კომბი",)),
    ("König & Meyer 18997 keyboard stand", "სადგამები", "კლავიშის სადგამი",
     ("კლავიშის",)),
    ("Electro-Voice PRO 780 MIC", "მიკროფონები", "დინამიური ვოკალური მიკროფონი",
     ("დინამიური", "მიკროფონები")),
    # --- guitar family disambiguation ---
    ("Ibanez GIO GAX-70 Black", "გიტარა", "ელექტრო გიტარა", ("ელექტრო",)),
    ("Yamaha F310 acoustic guitar", "გიტარა", "აკუსტიკური გიტარა", ("აკუსტიკური",)),
    ("Valencia VC204 classical guitar", "გიტარა", "კლასიკური გიტარა", ("კლასიკური",)),
    ("Fender Rumble 40 bass combo", "ბასი", "ბასის გამაძლიერებელი კომბო",
     ("გამაძლიერებელი/კომბი",)),
    ("Yamaha TRBX174 bass guitar", "ბასი", "ბას-გიტარა", ("ბას-გიტარა",)),
    ("Ernie Ball 2221 electric guitar strings", "გიტარა", "გიტარის სიმები", ("სიმები",)),
    ("Fender guitar gig bag", "გიტარა", "გიტარის ჩანთა", ("ჩანთები",)),
    ("Marshall MG30GFX guitar amp", "გიტარა", "გიტარის გამაძლიერებელი კომბო",
     ("გამაძლიერებელი/კომბი",)),
    # --- other families ---
    ("Pearl Export EXX725 drum kit", "დასარტყამი", "აკუსტიკური დრამ ნაკრები",
     ("აკუსტიკური",)),
    ("Zildjian A Custom 16 crash", "დასარტყამი", "თეფში", ("თეფშები",)),
    ("Vic Firth 5A drumsticks", "დასარტყამი", "დრამის ჯოხები", ("ჯოხები",)),
    ("Yamaha P-45 digital piano", "კლავიშებიანი", "ციფრული პიანინო", ("ციფრული პიანინო",)),
    ("Korg Minilogue XD synthesizer", "კლავიშებიანი", "სინთეზატორი", ("სინთეზატორი",)),
    ("Behringer X32 Compact mixer", "პრო აუდიო", "ციფრული მიქსერი", ("მიქსერი",)),
    ("Focusrite Scarlett 2i2 audio interface", "სტუდია", "აუდიო ინტერფეისი",
     ("აუდიო ინტერფეისი",)),
    ("Audio-Technica ATH-M50x headphones", "სტუდია", "სტუდიური ყურსასმენი", ("ყურსასმენი",)),
    ("KRK Rokit 5 G4 studio monitor", "სტუდია", "სტუდიური მონიტორი",
     ("სტუდიური მონიტორები",)),
    ("Shure SM58 vocal microphone", "მიკროფონები", "დინამიური ვოკალური მიკროფონი",
     ("დინამიური",)),
    ("Rode NT1-A condenser mic", "მიკროფონები", "კონდენსატორული მიკროფონი",
     ("კონდენსატორული",)),
    ("Shure BLX24 wireless system", "მიკროფონები", "უკაბელო მიკროფონის სისტემა",
     ("უკაბელო",)),
    ("Klotz XLR microphone cable 5m", "კომუტაცია", "მიკროფონის კაბელი",
     ("მიკროფონის კაბელი",)),
    ("Hosa instrument cable jack 6m", "კომუტაცია", "ინსტრუმენტის კაბელი",
     ("ინსტრუმენტის კაბელი",)),
    ("Yamaha YAS-280 alto saxophone", "ჩასაბერი", "საქსაფონი", ("საქსაფონი",)),
    ("Hohner Special 20 harmonica", "ჩასაბერი", "ჰარმონიკა", ("ჰარმონიკა",)),
    ("Stentor Student II 4/4 violin", "ხემიანი", "ვიოლინო", ("ვიოლინო",)),
    ("Pioneer DDJ-FLX4 DJ controller", "DJ", "DJ კონტროლერი", ("DJ კონტროლერი",)),
    ("Akai MPK Mini MK3 MIDI keyboard", "კლავიშებიანი", "MIDI კლავიატურა",
     ("MIDI კლავიატურა",)),
    ("Chauvet SlimPAR 56 LED par", "სასცენო ეფექტები", "ლედ-პარი განათება", ("ლედ-პარი",)),
    ("Antari Z-1020 fog machine", "სასცენო ეფექტები", "ბოლის აპარატი", ("ბოლის აპარატი",)),
    ("K&M 210/9 microphone stand", "სადგამები", "მიკროფონის სადგამი", ("მიკროფონის",)),
    ("Boss DS-1 distortion pedal", "გიტარა", "გიტარის ეფექტების პედალი", ("ეფექტები",)),
    ("Panduri Georgian traditional", "ტრადიციული", "ქართული ხალხური ინსტრუმენტი",
     ("ქართული",)),
    ("Kalimba 17 keys", "ტრადიციული", "კალიმბა", ("კალიმბა",)),
    ("Wittner metronome", "აქსესუარები & სხვა", "მეტრონომი", ("მეტრონომი",)),
    # --- vague product: general family fallback is acceptable ---
    ("Some unknown guitar thing", "გიტარა", "გიტარისთვის ნივთი",
     ("კატეგორია: გიტარა", "აქსესუარები", "ნაწილები")),
]


def main() -> int:
    catalog = load_catalog()
    print(f"Catalog: {len(catalog)} categories")
    prompt = compact_tree_prompt(catalog)
    print(f"Compact tree prompt: {len(prompt)} chars (~{int(len(prompt.encode())/2.5)} tokens)\n")

    ok = 0
    fails: list[str] = []
    for title, fam, typ, expected in CASES:
        res = classify(
            catalog=catalog, title=title, family_hint=fam, type_hint=typ, ai_labels=[]
        )
        got = res["labels"][0] if res["labels"] else "(none)"
        good = got in expected
        ok += good
        mark = "✓" if good else "✗"
        print(f"{mark} {title[:44]:44} → {got:24} [{res['mode']}] fam={res['family']}")
        if not good:
            fails.append(f"{title}: got {got!r}, expected one of {expected}")
            for t in res["trace"]:
                print(f"     {t}")

    print(f"\n{ok}/{len(CASES)} correct")
    if fails:
        print("\nFailures:")
        for f in fails:
            print(" -", f)
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
