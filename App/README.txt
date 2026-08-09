ACOUSTIC SMART FILLER
=====================

What it does
------------
Connects to the Acoustic.ge CS-Cart product edit page already open in the
special Chrome window, asks OpenAI to generate product content, then fills
form fields in the browser.

It does NOT click Save.
It does NOT connect to the Acoustic.ge database.
You review everything in CS-Cart and Save only when you are happy.


OpenAI API key setup (important)
--------------------------------
1) Create an OpenAI account
   https://platform.openai.com/

2) Add billing / payment method
   https://platform.openai.com/settings/organization/billing
   (API usage is paid; free trial credit may or may not be available.)

3) Create an API key
   https://platform.openai.com/api-keys
   - Click "Create new secret key"
   - Copy it once (starts with sk- ...)
   - Store it safely; OpenAI will not show the full key again.

4) Put the key into THIS app (choose one):

   Option A — recommended for daily use
   - Run SETUP.bat once from the package root (folder above App\)
   - Open the file:

       config.json

     in this App folder (same folder as app.py)
   - Replace PASTE_YOUR_OPENAI_API_KEY_HERE with your real key:

       {
         "openai_api_key": "sk-your-real-key-here",
         "openai_model": "gpt-4o-mini",
         "content_language": "Georgian"
       }

   - Save the file.
   - Do NOT email or share config.json. It is a secret.

   Option B — type it in the app
   - Run RUN_APP.bat
   - Paste the key into the "OpenAI API key" field
   - Optionally click "Save key to config.json"

   Option C — Windows environment variable
   - Set user environment variable OPENAI_API_KEY to your key
   - The app reads it automatically if config has no key

5) Model
   Default: gpt-4o-mini (cheap and good enough for store copy).
   You can change openai_model in config.json or in the app field
   (example: gpt-4o).


FIRST-TIME SETUP
----------------
1. Install Python 3 from python.org and tick "Add Python to PATH".
2. From the folder ABOVE this App folder, double-click SETUP.vbs
   (Windows setup window — installs packages + Desktop icon).
3. Put your API key into config.json in THIS App folder.
4. Start from Desktop "Acoustic Smart Filler" or START.bat here.


HOW TO USE (simplest)
---------------------
1. Double-click START.bat  (or START.vbs)
   → opens special Chrome + the Smart Filler app
   → runs without a black CMD window (pythonw / silent launcher)

2. In Chrome: log in (first time only), open a product edit page
   (dispatch=products.update)

3. In the app — only two main buttons:
   a) Scrape
      Reads product Name, generates texts / specs / categories / video,
      finds photos. Nothing is written to the site yet.
   b) Review tabs:
      - Texts          edit AI copy
      - Images         select photos + Main (mouse wheel scrolls)
      - Categories     tick categories from real page options
      - მახასიათებლები  pick from real dropdowns (single/multi)
      - Video          edit URL, Preview in browser
   c) Fill page
      Writes everything you reviewed into the open product form.
      Never presses Save.

4. Check CS-Cart, then Save yourself.

Optional: START_CHROME.bat alone, or RUN_APP.bat / RUN_APP.vbs for the app only.
   Save API key / model / language with “Save settings” in the app.


BULK SCRAPE AND FILL
--------------------
Do NOT open one Chrome tab per product. Work from the product list.

1. In debug Chrome: Products → Products (or filter by category).
2. Tick the products you want on that list page.
3. App → Bulk tab → “Import from Chrome list”
   - Default: only ticked rows
   - Or “All products visible on this list page”
   - Re-import after flipping CS-Cart list pages for more products.
4. “Scrape queue” — opens each product_id edit URL in the SAME tab,
   scans, AI, images (can take ~1 min per product). Use Cancel to stop after current.
5. Double-click a “ready” row to load it into Texts / Categories / Specs / Video / Images.
   Edit freely. Click ✓ column to include/exclude; Approve column for fill.
   “Approve all ready” marks every ready job for fill.
6. “Fill approved” — fills each approved ready form. NEVER saves.
7. In CS-Cart, review each product and click Save yourself.
8. “Clear done” removes filled/skipped rows from the queue.

Single-product Scrape / Fill page still works as before.


FIELDS THE APP TRIES TO FILL
----------------------------
General
  - Product name
  - Price
  - Full description (textarea / TinyMCE / CKEditor)
  - Promo text
  - Categories (best-effort: clicks matching category checkboxes by name)

Specifications / Features
  - Scans product feature selects/inputs on the page
  - Asks AI to pick from available option labels when possible
  - Applies matching feature values (dynamic IDs differ per product)

SEO
  - SEO name / SEO URL (if present on the form)
  - Page title
  - META description
  - META keywords

AB: Video gallery
  - Best-effort fill of video URL / title / description fields when the
    add-on DOM is recognized. Versions differ; if unmatched, fill manually.

Images (Main + Additional)
  - CS-Cart uses local file upload controls.
  - Browsers do not allow remote apps to inject arbitrary files by path
    without a real chosen file. The AI may describe images, but UPLOADS
    must be done manually in admin for now.


SAFETY
------
- Only acoustic.ge / aco_st_admin.php product update tabs
- Never submits the product form
- No Acoustic.ge database credentials
- OpenAI key stays on your PC (config.json or env var)
- Chrome debug port is localhost-only


TROUBLESHOOTING
---------------
"Chrome not connected"
  Use the window from START_CHROME.bat, not ordinary Chrome.

"API key missing" / authentication error from OpenAI
  Check the key in config.json, billing is enabled, and the key is not revoked.

Field "not found / skipped"
  That control used a different id/name on this page/theme. Fill it manually
  and send a screenshot if you want the selector map extended.

Features/categories not matching
  Open the Features / category UI on the product page before filling so
  controls exist in the DOM; names must match scanned labels.


IMAGES TAB
----------
1. Open a product edit page (products.update) in START_CHROME Chrome.
2. In the app open the Images tab.
3. Click "Find images (AI + web)":
   - OpenAI builds a product-photo search query from the title
   - Prefer Google Custom Search when google_api_key + google_cse_id are set
   - Falls back to DuckDuckGo (often rate-limits with 403)
   - Previews appear in a grid
4. Check Select on images you want; choose Main image with the radio button.
5. Click "Compress ≤400KB & upload selected":
   - Downloads full images
   - Converts to progressive JPEG, keeps quality high, reduces size only
     until each file is max 400 KB
   - Attaches files to CS-Cart product image file inputs via Selenium
   - Works even when the product already has images: CS-Cart hides the local
     upload UI; the app unhides it and fills main detailed + additional
     slots (type set to "local"). Old saved images stay until you Save.
6. Review images in CS-Cart, then Save yourself.

Images cache under image_cache\ in the app folder.


GOOGLE IMAGE SEARCH SETUP (recommended — avoids DuckDuckGo rate limits)
----------------------------------------------------------------------
Google does not allow free anonymous scraping of Google Images from apps.
Use the official Custom Search JSON API (100 free queries/day):

1) Google Cloud project + API key
   - https://console.cloud.google.com/
   - Enable "Custom Search API"
   - Create an API key (Credentials)

2) Programmable Search Engine (CSE)
   - https://programmablesearchengine.google.com/
   - Create a search engine
   - Turn ON: Search the entire web
   - Turn ON: Image search
   - Copy the Search engine ID (cx)

3) Put both values into config.json next to app.py:

   "google_api_key": "YOUR_GOOGLE_API_KEY",
   "google_cse_id": "YOUR_SEARCH_ENGINE_CX",
   "image_search_backend": "auto"

   auto = Google first (if keys set), then DuckDuckGo
   google = Google only
   duckduckgo = DuckDuckGo only

You can also set env vars GOOGLE_API_KEY and GOOGLE_CSE_ID.


FILES
-----
(This App folder holds everything the program needs.)

app.py                 Main window + single fill + bulk queue UI
ai_generate.py         OpenAI product text generation
image_tools.py         Image search (Google/DDG), download, ≤400KB compress
image_upload.py        Attach files to CS-Cart product form
page_scripts.py        Browser scan + apply + product list harvest (bulk)
config_loader.py       Loads/saves config.json
config.example.json    Template (safe to share)
config.json            YOUR secrets (created by root SETUP — do not share)
requirements.txt       Python packages
START.vbs / START.bat  Chrome + app (no black CMD; uses pythonw)
RUN_APP.vbs / RUN_APP.bat  App only (silent)
START_CHROME.bat       Special Chrome only

Package root (one level up):
  SETUP.vbs / setup_wizard.py   Visual setup (no CMD)
  CreateDesktopIcon.vbs         Desktop shortcut helper
