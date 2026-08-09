"""Upload selected images into the open CS-Cart product edit form (no Save).

Acoustic / modern CS-Cart use a multi-image drop zone on the product form:
  "Drop photos here or Select images"
not only classic file_product_main_image_detailed fields.

A previous false success filled a generic file_filename[0] that is not bound
to that widget. This module:
  1) finds the product Images control-group / drop zone
  2) prefers multi-accept image inputs inside that zone
  3) sets files via Chrome CDP DOM.setFileInputFiles (works on hidden inputs)
  4) falls back to classic main/additional inputs if present
  5) verifies success by UI previews or input.files, not input name alone
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


FIND_IMAGE_WIDGET_SCRIPT = r"""
function sectionText(el) {
    try { return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim(); }
    catch (e) { return ''; }
}

function looksLikeImagesSection(el) {
    if (!el) return false;
    const t = sectionText(el).slice(0, 500).toLowerCase();
    if (t.includes('ჩაყარე') || t.includes('select images') || t.includes('drop photo') ||
        t.includes('drop image') || t.includes('drag and drop') || t.includes('drop files')) {
        return true;
    }
    // control-group whose label is Images
    const lab = el.querySelector && el.querySelector(':scope > label, :scope > .control-label, label.control-label, .control-label');
    if (lab) {
        const lt = sectionText(lab).toLowerCase();
        if (lt.includes('სურათ') || lt === 'images:' || lt.startsWith('images') || lt.includes('images:')) return true;
    }
    return false;
}

function findImagesSection() {
    // Prefer labeled control-group "სურათები" / Images on product form
    const root =
        document.querySelector('form[name="product_update_form"]') ||
        document.querySelector('#product_update_form') ||
        document.querySelector('#content_management') ||
        document.body;

    const labels = root.querySelectorAll('label.control-label, .control-label, label');
    for (const lab of labels) {
        const lt = sectionText(lab).toLowerCase().replace('*', '');
        if (lt.includes('სურათები') || lt === 'images:' || lt === 'images' ||
            lt.startsWith('images:') || lt === 'image:' || lt === 'image') {
            const cg = lab.closest('.control-group') || lab.parentElement;
            if (cg) return cg;
        }
    }

    // Drop-zone / modern widget by text
    const all = root.querySelectorAll(
        '.control-group, .image-upload, .image-uploader, .product-image-uploader, ' +
        '[class*="image-upload"], [class*="file-uploader"], [class*="dropzone"], ' +
        '.cm-image-uploader, .ty-file-uploader, div'
    );
    let best = null;
    let bestScore = 0;
    for (const el of all) {
        if (!looksLikeImagesSection(el)) continue;
        // Prefer compact control-groups over huge containers
        const depth = (function () {
            let d = 0, n = el;
            while (n && n !== root) { d++; n = n.parentElement; }
            return d;
        })();
        const fileCount = el.querySelectorAll('input[type="file"]').length;
        const score = 50 + fileCount * 10 - Math.min(depth, 20) +
            (el.classList.contains('control-group') ? 30 : 0);
        if (score > bestScore) {
            bestScore = score;
            best = el;
        }
    }
    if (best) return best;

    // Fallback: #content_images
    return document.getElementById('content_images');
}

function describeInput(el, globalIndex) {
    const name = el.getAttribute('name') || '';
    const id = el.id || '';
    const accept = (el.getAttribute('accept') || '').toLowerCase();
    const blob = (name + ' ' + id + ' ' + accept).toLowerCase();
    let score = 0;
    const inSection = true; // caller only passes in-section inputs for high score
    if (el.multiple) score += 40;
    if (accept.includes('image') || el.classList.contains('cm-image-field')) score += 25;
    if (blob.includes('product_main') && blob.includes('detailed')) score += 100;
    else if (blob.includes('product_main')) score += 70;
    if (blob.includes('product_additional') || blob.includes('product_add_additional')) score += 55;
    if (blob.includes('filename') || blob.includes('file_filename')) score += 20;
    if (blob.includes('image')) score += 15;
    if (blob.includes('icon') || blob.includes('thumb')) score -= 35;
    if (blob.includes('category') || blob.includes('option') || blob.includes('variant')) score -= 90;
    if (blob.includes('file_')) score += 5;
    // Prefer visible tree under images section
    let node = el, hiddenParents = 0;
    for (let i = 0; i < 8 && node; i++) {
        try {
            const st = window.getComputedStyle(node);
            if (st && (st.display === 'none' || st.visibility === 'hidden')) hiddenParents++;
        } catch (e) {}
        node = node.parentElement;
    }
    if (hiddenParents === 0) score += 10;
    return {
        index: globalIndex,
        name,
        id,
        accept,
        multiple: !!el.multiple,
        score,
        isMainDetailed: blob.includes('product_main') && blob.includes('detailed'),
        isMain: blob.includes('product_main') && !blob.includes('icon'),
        isAdditional: blob.includes('product_additional') || blob.includes('product_add_additional'),
        hasValue: !!(el.files && el.files.length),
        fileCount: el.files ? el.files.length : 0
    };
}

// Unhide product image areas lightly
const pane = document.getElementById('content_images');
if (pane) {
    pane.classList.remove('hidden', 'collapsed');
    pane.style.display = '';
}

const section = findImagesSection();
const allFiles = Array.from(document.querySelectorAll('input[type="file"]'));
const sectionFiles = section
    ? Array.from(section.querySelectorAll('input[type="file"]'))
    : [];

// Mark section for later verification
if (section) {
    section.setAttribute('data-asf-images-section', '1');
}

const sectionInfos = sectionFiles.map(el => describeInput(el, allFiles.indexOf(el)))
    .filter(x => x.index >= 0)
    .sort((a, b) => b.score - a.score);

const globalInfos = allFiles.map((el, index) => {
    const info = describeInput(el, index);
    // Downgrade inputs outside the product images section
    if (section && !section.contains(el)) {
        info.score -= 50;
        info.outsideSection = true;
    } else {
        info.outsideSection = false;
    }
    return info;
}).sort((a, b) => b.score - a.score);

const sectionSnippet = section ? sectionText(section).slice(0, 180) : '';

return {
    sectionFound: !!section,
    sectionSnippet,
    sectionInputCount: sectionFiles.length,
    sectionInputs: sectionInfos,
    allInputs: globalInfos.slice(0, 40),
    multiInSection: sectionInfos.some(x => x.multiple),
};
"""

OPEN_IMAGES_AREA_SCRIPT = r"""
const root =
    document.querySelector('form[name="product_update_form"]') ||
    document.querySelector('#product_update_form') ||
    document.querySelector('#content_management') ||
    document.body;
const nodes = root.querySelectorAll(
    '.cm-j-tabs a, .cm-tabs a, .nav-tabs a, .tabs a, a[href^="#content_"], a.cm-js, li.cm-js > a'
);
nodes.forEach(el => {
    const href = (el.getAttribute('href') || '');
    if (href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript:')) return;
    if (href.toLowerCase().includes('dispatch=') || href.toLowerCase().includes('product_features')) return;
    const t = ((el.innerText || el.textContent || '') + ' ' + href).toLowerCase();
    if (t.includes('image') || t.includes('სურათ') || t.includes('фото') ||
        href.includes('content_images') || href.includes('images')) {
        try { el.click(); } catch (e) {}
    }
});
const pane = document.getElementById('content_images');
if (pane) {
    pane.classList.remove('hidden', 'collapsed');
    pane.style.display = '';
}
// Expand dropdown "Select images" / local upload if present inside images section
const section = document.querySelector('[data-asf-images-section="1"]') ||
    document.querySelector('form[name="product_update_form"]');
if (section) {
    const clickables = section.querySelectorAll('a, button, .btn, .dropdown-toggle, label');
    for (const el of clickables) {
        const t = ((el.innerText || el.textContent || '') + ' ' + (el.className || '')).toLowerCase();
        if (
            t.includes('select images') || t.includes('select image') ||
            t.includes('აირჩიე') || t.includes('choose file') || t.includes('local')
        ) {
            // Do not navigate away
            const href = (el.getAttribute('href') || '');
            if (href.includes('dispatch=') && !href.startsWith('#')) continue;
            try { el.click(); } catch (e) {}
            break;
        }
    }
}
// Unhide nested fileuploader btn-groups
document.querySelectorAll(
    '[data-asf-images-section="1"] .hidden, [data-asf-images-section="1"] .btn-group.hidden, ' +
    '#content_images .btn-group.hidden, [id^="link_container_"].hidden'
).forEach(el => {
    el.classList.remove('hidden', 'cm-hide-with-inputs');
    el.style.display = '';
    el.style.visibility = 'visible';
});
return { ok: true, hasSectionMark: !!document.querySelector('[data-asf-images-section="1"]') };
"""

PREPARE_INPUT_SCRIPT = r"""
const el = arguments[0];
if (!el) return { ok: false };
let node = el;
for (let i = 0; i < 14 && node; i++) {
    try {
        if (node.classList) node.classList.remove('hidden', 'cm-hide-with-inputs', 'collapsed');
        if (node.style) {
            const st = window.getComputedStyle(node);
            if (st && st.display === 'none') {
                node.style.display = (node.tagName === 'INPUT') ? 'block' : (node.tagName === 'LABEL' ? 'inline-block' : 'block');
            }
            node.style.visibility = 'visible';
            node.style.opacity = '1';
        }
        node.removeAttribute && node.removeAttribute('hidden');
    } catch (e) {}
    node = node.parentElement;
}
el.style.cssText += ';display:block !important;opacity:1 !important;visibility:visible !important;position:relative !important;z-index:20 !important;font-size:12px !important;width:220px !important;height:32px !important;';
el.removeAttribute('hidden');
el.removeAttribute('disabled');
el.removeAttribute('data-ca-empty-file');
try { el.value = ''; } catch (e) {}

const id = el.id || '';
const name = el.getAttribute('name') || '';
if (id.startsWith('local_')) {
    const base = id.slice(6);
    const typeEl = document.getElementById('type_' + base);
    if (typeEl) { typeEl.value = 'local'; typeEl.removeAttribute('disabled'); }
}
if (name.startsWith('file_')) {
    const typeName = 'type_' + name.slice(5);
    document.querySelectorAll('input[name="' + typeName.replace(/"/g, '') + '"]').forEach(t => {
        t.value = 'local';
        t.removeAttribute('disabled');
    });
}
return { ok: true, id, name, multiple: !!el.multiple };
"""

TRIGGER_AFTER_SET_SCRIPT = r"""
const el = arguments[0];
if (!el) return { ok: false };
const id = el.id || '';
const name = el.getAttribute('name') || '';

if (id.startsWith('local_')) {
    const typeEl = document.getElementById('type_' + id.slice(6));
    if (typeEl) typeEl.value = 'local';
}
if (name.startsWith('file_')) {
    const typeName = 'type_' + name.slice(5);
    document.querySelectorAll('input[name="' + typeName.replace(/"/g, '') + '"]').forEach(t => {
        t.value = 'local';
    });
}

// Prefer a real HTMLEvents/InputEvent so jQuery + Dropzone + Tygh all hear it
function fire(type) {
    try {
        let ev;
        if (type === 'change' || type === 'input') {
            ev = new Event(type, { bubbles: true, cancelable: true });
        } else {
            ev = new Event(type, { bubbles: true, cancelable: true });
        }
        el.dispatchEvent(ev);
    } catch (e) {
        try {
            const ev2 = document.createEvent('HTMLEvents');
            ev2.initEvent(type, true, true);
            el.dispatchEvent(ev2);
        } catch (e2) {}
    }
    try {
        if (window.jQuery) {
            window.jQuery(el).trigger(type);
        } else if (window.Tygh && Tygh.$) {
            Tygh.$(el).trigger(type);
        }
    } catch (e) {}
}
fire('input');
fire('change');

// Call inline onchange if present (CS-Cart fileuploader)
try {
    if (typeof el.onchange === 'function') el.onchange();
} catch (e) {}
try {
    if (el.getAttribute('onchange')) {
        // already executed by dispatch in most browsers
    }
} catch (e) {}

try {
    if (window.Tygh && Tygh.fileuploader) {
        if (id && Tygh.fileuploader.show_loader) {
            try { Tygh.fileuploader.show_loader(id); } catch (e) {}
        }
        if (id && Tygh.fileuploader.check_image) {
            try { Tygh.fileuploader.check_image(id); } catch (e) {}
        }
    }
} catch (e) {}

// Dropzone instances on the page
try {
    if (window.Dropzone) {
        const zones = Dropzone.instances || [];
        for (const dz of zones) {
            try {
                if (dz.hiddenFileInput === el || dz.element.contains(el)) {
                    // files already on input; notify
                    if (typeof dz._enqueueFiles === 'function' && el.files) {
                        dz._enqueueFiles(Array.from(el.files));
                    }
                }
            } catch (e) {}
        }
    }
} catch (e) {}

return {
    ok: !!(el.files && el.files.length),
    fileCount: el.files ? el.files.length : 0,
    names: el.files ? Array.from(el.files).map(f => f.name) : [],
    typeValue: (function () {
        if (id.startsWith('local_')) {
            const t = document.getElementById('type_' + id.slice(6));
            return t ? t.value : '';
        }
        return '';
    })()
};
"""

VERIFY_UI_SCRIPT = r"""
const section = document.querySelector('[data-asf-images-section="1"]');
const root = section ||
    document.querySelector('form[name="product_update_form"]') ||
    document.body;

const fileInputs = Array.from((section || root).querySelectorAll('input[type="file"]'));
let filesOnInputs = 0;
const fileNames = [];
for (const el of fileInputs) {
    if (el.files && el.files.length) {
        filesOnInputs += el.files.length;
        for (const f of el.files) fileNames.push(f.name);
    }
}

// Visual previews in modern widgets
const previewSelectors = [
    'img[src^="blob:"]',
    'img[src^="data:"]',
    '.dz-preview',
    '.dz-image',
    '.cm-uploaded-image',
    '.upload-file-section img',
    '.image-upload img',
    '[class*="preview"] img',
    '.sortable-box img',
    '.attachment-item img',
    '.ty-file-uploader-file',
    '.cm-fu-file',
    'img.hand',
    '.product-image img'
];
let previewCount = 0;
const seen = new Set();
for (const sel of previewSelectors) {
    root.querySelectorAll(sel).forEach(el => {
        // skip huge icons/placeholders without real image load if possible
        const src = (el.getAttribute && el.getAttribute('src')) || '';
        if (src.includes('no_image') || src.includes('icon_sort')) return;
        if (seen.has(el)) return;
        seen.add(el);
        previewCount++;
    });
}

// Text that suggests a file was selected (classic fileuploader)
let messageHits = 0;
root.querySelectorAll('[id^="message_"], .cm-fu-file, .upload-filename, .filename').forEach(el => {
    const t = ((el.innerText || el.textContent) || '').trim();
    if (t && t.length > 2 && !t.toLowerCase().includes('no file') &&
        !t.includes('ჩაყარე') && !t.toLowerCase().includes('drop')) {
        messageHits++;
    }
});

// type_* = local
let typeLocal = 0;
root.querySelectorAll('input[name^="type_"]').forEach(el => {
    if ((el.value || '').toLowerCase() === 'local') typeLocal++;
});

return {
    filesOnInputs,
    fileNames: fileNames.slice(0, 12),
    previewCount,
    messageHits,
    typeLocal,
    looksAttached: filesOnInputs > 0 || previewCount > 0 || messageHits > 0
};
"""


def _abs_paths(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        if not p:
            continue
        ap = str(Path(p).resolve())
        if not os.path.isfile(ap):
            raise RuntimeError(f"Image file missing on disk: {ap}")
        out.append(ap)
    return out


def open_images_area(driver) -> dict[str, Any]:
    # First pass marks section
    info = driver.execute_script(FIND_IMAGE_WIDGET_SCRIPT)
    time.sleep(0.2)
    driver.execute_script(OPEN_IMAGES_AREA_SCRIPT)
    time.sleep(0.6)
    return info if isinstance(info, dict) else {}


def discover_widget(driver) -> dict[str, Any]:
    result = driver.execute_script(FIND_IMAGE_WIDGET_SCRIPT)
    return result if isinstance(result, dict) else {}


# Prepare + mark target by index only (no long-lived WebElement references)
PREPARE_BY_INDEX_SCRIPT = r"""
const index = arguments[0];
const els = document.querySelectorAll('input[type="file"]');
const el = els[index];
if (!el) return { ok: false, error: 'index out of range', count: els.length };
// clear any old marks
document.querySelectorAll('[data-asf-file-target]').forEach(n => n.removeAttribute('data-asf-file-target'));
let node = el;
for (let i = 0; i < 14 && node; i++) {
    try {
        if (node.classList) node.classList.remove('hidden', 'cm-hide-with-inputs', 'collapsed');
        if (node.style) {
            const st = window.getComputedStyle(node);
            if (st && st.display === 'none') {
                node.style.display = (node.tagName === 'INPUT') ? 'block' : 'block';
            }
            node.style.visibility = 'visible';
            node.style.opacity = '1';
        }
        if (node.removeAttribute) node.removeAttribute('hidden');
    } catch (e) {}
    node = node.parentElement;
}
el.style.cssText += ';display:block !important;opacity:1 !important;visibility:visible !important;';
el.removeAttribute('hidden');
el.removeAttribute('disabled');
el.removeAttribute('data-ca-empty-file');
try { el.value = ''; } catch (e) {}
el.setAttribute('data-asf-file-target', '1');
return {
    ok: true,
    name: el.getAttribute('name') || '',
    id: el.id || '',
    multiple: !!el.multiple,
    count: els.length
};
"""

TRIGGER_MARKED_SCRIPT = r"""
const preferIndex = arguments[0];
let el = document.querySelector('input[type="file"][data-asf-file-target="1"]');
if (!el) {
    const els = document.querySelectorAll('input[type="file"]');
    el = els[preferIndex] || null;
}
if (!el) return { ok: false, error: 'target missing after set' };

const id = el.id || '';
const name = el.getAttribute('name') || '';
if (id.startsWith('local_')) {
    const typeEl = document.getElementById('type_' + id.slice(6));
    if (typeEl) typeEl.value = 'local';
}
if (name.startsWith('file_')) {
    const typeName = 'type_' + name.slice(5);
    document.querySelectorAll('input[name="' + typeName.replace(/"/g, '') + '"]').forEach(t => {
        t.value = 'local';
    });
}

function fire(type) {
    try { el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true })); } catch (e) {}
    try {
        if (window.jQuery) window.jQuery(el).trigger(type);
        else if (window.Tygh && Tygh.$) Tygh.$(el).trigger(type);
    } catch (e) {}
}
fire('input');
fire('change');
try { if (typeof el.onchange === 'function') el.onchange(); } catch (e) {}

try {
    if (window.Tygh && Tygh.fileuploader && id) {
        if (Tygh.fileuploader.show_loader) { try { Tygh.fileuploader.show_loader(id); } catch (e) {} }
        if (Tygh.fileuploader.check_image) { try { Tygh.fileuploader.check_image(id); } catch (e) {} }
    }
} catch (e) {}

try {
    if (window.Dropzone && Dropzone.instances) {
        for (const dz of Dropzone.instances) {
            try {
                if (dz.hiddenFileInput === el || (dz.element && dz.element.contains(el))) {
                    if (typeof dz._enqueueFiles === 'function' && el.files) {
                        dz._enqueueFiles(Array.from(el.files));
                    }
                }
            } catch (e) {}
        }
    }
} catch (e) {}

// Drop mark; widget may rebuild DOM next tick
try { el.removeAttribute('data-asf-file-target'); } catch (e) {}

return {
    ok: !!(el.files && el.files.length),
    fileCount: el.files ? el.files.length : 0,
    names: el.files ? Array.from(el.files).map(f => f.name) : [],
    name,
    id
};
"""


def _clear_file_marks(driver) -> None:
    try:
        driver.execute_script(
            "document.querySelectorAll('[data-asf-file-target]').forEach("
            "n => n.removeAttribute('data-asf-file-target'));"
        )
    except Exception:
        pass


def _cdp_set_files_marked(driver, paths: list[str]) -> dict[str, Any]:
    """Set files on the input currently marked data-asf-file-target=1."""
    paths = _abs_paths(paths)
    doc = driver.execute_cdp_cmd("DOM.getDocument", {"depth": 0, "pierce": True})
    node = driver.execute_cdp_cmd(
        "DOM.querySelector",
        {
            "nodeId": doc["root"]["nodeId"],
            "selector": 'input[type="file"][data-asf-file-target="1"]',
        },
    )
    node_id = node.get("nodeId") or 0
    if not node_id:
        raise RuntimeError("CDP could not resolve marked file input")
    driver.execute_cdp_cmd(
        "DOM.setFileInputFiles",
        {"files": paths, "nodeId": node_id},
    )
    return {"method": "cdp", "ok": True, "paths": paths}


def _send_keys_by_index(driver, index: int, paths: list[str]) -> dict[str, Any]:
    paths = _abs_paths(paths)
    els = driver.find_elements("css selector", 'input[type="file"]')
    if index < 0 or index >= len(els):
        raise RuntimeError(f"File input index {index} not found (have {len(els)})")
    # Fresh element each time; only used for send_keys, then discarded
    els[index].send_keys("\n".join(paths))
    return {"method": "send_keys", "ok": True, "paths": paths}


def _set_files_on_index(
    driver, index: int, paths: list[str], prefer_cdp: bool = True
) -> dict[str, Any]:
    """
    Attach files once to input[index]. Never reuses stale WebElements after the
    page mutates (CS-Cart rebuilds inputs after change → duplicates/failures).
    """
    paths = _abs_paths(paths)
    if not paths:
        raise RuntimeError("No paths to set")

    prep = driver.execute_script(PREPARE_BY_INDEX_SCRIPT, int(index)) or {}
    if not prep.get("ok"):
        raise RuntimeError(prep.get("error") or f"Cannot prepare file input {index}")
    time.sleep(0.1)

    set_info: dict[str, Any] = {}
    errors: list[str] = []

    if prefer_cdp:
        try:
            set_info = _cdp_set_files_marked(driver, paths)
        except Exception as exc:
            errors.append(f"cdp: {exc}")
            set_info = {}

    if not set_info.get("ok"):
        try:
            # Re-mark after failed CDP
            prep = driver.execute_script(PREPARE_BY_INDEX_SCRIPT, int(index)) or {}
            if not prep.get("ok"):
                raise RuntimeError(prep.get("error") or "prepare failed")
            set_info = _send_keys_by_index(driver, int(index), paths)
        except Exception as exc:
            errors.append(f"send_keys: {exc}")
            _clear_file_marks(driver)
            raise RuntimeError("; ".join(errors) or str(exc))

    # Files are on the input from this point — never re-run classic with same paths
    files_applied = True
    time.sleep(0.4)
    after: dict[str, Any] = {}
    try:
        after = driver.execute_script(TRIGGER_MARKED_SCRIPT, int(index)) or {}
    except Exception as exc:
        errors.append(f"trigger: {exc}")
        after = {"ok": False, "error": str(exc)}
    finally:
        _clear_file_marks(driver)

    name = after.get("name") or prep.get("name") or ""
    el_id = after.get("id") or prep.get("id") or ""
    input_ok = bool(after.get("ok") or after.get("fileCount"))

    return {
        "set": set_info,
        "after": after,
        "name": name,
        "id": el_id,
        "ok": input_ok,
        "fileCount": after.get("fileCount") or 0,
        "fileNames": after.get("names") or [],
        "paths_set": paths,
        "errors": errors,
        "set_attempted": files_applied,
    }


def _verify(driver) -> dict[str, Any]:
    result = driver.execute_script(VERIFY_UI_SCRIPT)
    return result if isinstance(result, dict) else {}


def _ui_gained(before: dict[str, Any], after: dict[str, Any], *, min_gain: int = 1) -> bool:
    """True when the drop zone gained previews / files since baseline."""
    b_prev = int(before.get("previewCount") or 0)
    a_prev = int(after.get("previewCount") or 0)
    b_files = int(before.get("filesOnInputs") or 0)
    a_files = int(after.get("filesOnInputs") or 0)
    b_msg = int(before.get("messageHits") or 0)
    a_msg = int(after.get("messageHits") or 0)
    b_local = int(before.get("typeLocal") or 0)
    a_local = int(after.get("typeLocal") or 0)
    if a_prev >= b_prev + min_gain:
        return True
    if a_files >= max(b_files + min_gain, min_gain):
        return True
    if a_msg >= b_msg + min_gain:
        return True
    if a_local >= b_local + min_gain:
        return True
    if after.get("looksAttached") and (a_prev > b_prev or a_files > b_files or a_msg > b_msg):
        return True
    return False


def _pick_multi_target(widget: dict[str, Any]) -> dict[str, Any] | None:
    """Best multi / generic image input inside the product Images section."""
    section = widget.get("sectionInputs") or []
    all_inputs = widget.get("allInputs") or []

    # Prefer multi-accept inside section
    multi = [x for x in section if x.get("multiple")]
    if multi:
        return max(multi, key=lambda x: x.get("score", 0))

    # Prefer any high-score image-related in section
    if section:
        best = max(section, key=lambda x: x.get("score", 0))
        if best.get("score", 0) >= 10:
            return best

    # Classic product_main detailed anywhere
    classic = [
        x
        for x in all_inputs
        if x.get("isMainDetailed") or (x.get("isMain") and not x.get("outsideSection"))
    ]
    if classic:
        return max(classic, key=lambda x: x.get("score", 0))

    # Last resort: highest score not heavily outside
    ok = [x for x in all_inputs if not x.get("outsideSection") or x.get("score", 0) >= 40]
    if ok:
        return max(ok, key=lambda x: x.get("score", 0))
    return all_inputs[0] if all_inputs else None


def _pick_classic_main(widget: dict[str, Any]) -> dict[str, Any] | None:
    for pool in (widget.get("sectionInputs") or [], widget.get("allInputs") or []):
        detailed = [x for x in pool if x.get("isMainDetailed")]
        if detailed:
            return max(detailed, key=lambda x: x.get("score", 0))
        mains = [x for x in pool if x.get("isMain")]
        if mains:
            return max(mains, key=lambda x: x.get("score", 0))
    return None


def _pick_classic_additional(
    widget: dict[str, Any], used: set[int]
) -> dict[str, Any] | None:
    for pool in (widget.get("sectionInputs") or [], widget.get("allInputs") or []):
        extras = [
            x
            for x in pool
            if x.get("isAdditional") and x["index"] not in used
        ]
        if extras:
            return max(extras, key=lambda x: x.get("score", 0))
    return None


def _mark_batch_success(report: dict[str, Any], *, n_files: int, detail: dict[str, Any], multi_t: dict) -> None:
    report["main"]["ok"] = True
    report["main"]["error"] = ""
    report["main"]["input_index"] = multi_t.get("index")
    report["main"]["input_name"] = detail.get("name") or multi_t.get("name")
    report["main"]["input_id"] = detail.get("id") or multi_t.get("id")
    report["main"]["fileName"] = (detail.get("fileNames") or [None])[0]
    report["main"]["fileCount"] = detail.get("fileCount") or n_files
    report["main"]["method"] = (detail.get("set") or {}).get("method")
    report["additional"] = [
        {
            "path": p,
            "ok": True,
            "batch": True,
            "input_name": report["main"].get("input_name"),
        }
        for p in (report.get("_extra_paths") or [])
    ]


def upload_images_to_product(
    driver,
    *,
    main_path: str | None,
    additional_paths: list[str],
    product_url: str | None = None,
) -> dict[str, Any]:
    """
    Attach images to the product Images drop zone / file inputs.

    Critical rules (duplicates / stale elements):
    - Upload each set of files AT MOST ONCE.
    - Prefer a single multi_batch attach of all files.
    - If the first attach changes the UI (AJAX previews), never run classic fallback.
    - Do not hold Selenium WebElements across DOM rebuilds.

    Does NOT click Save.
    """
    extra = list(additional_paths or [])
    ordered: list[str] = []
    if main_path:
        ordered.append(main_path)
    ordered.extend(extra)
    ordered = _abs_paths(ordered)

    report: dict[str, Any] = {
        "main": {"ok": False, "path": main_path, "error": ""},
        "additional": [],
        "mode": "",
        "widget": {},
        "verify": {},
        "baseline": {},
        "_extra_paths": extra,
        "note": (
            "Files attached once only — review thumbnails under Images, then Save manually. "
            "The app does not press Save."
        ),
    }

    if not ordered:
        report["main"]["error"] = "No image files to upload"
        return report

    open_images_area(driver)
    widget = discover_widget(driver)
    baseline = _verify(driver)
    report["baseline"] = baseline
    report["widget"] = {
        "sectionFound": widget.get("sectionFound"),
        "sectionSnippet": widget.get("sectionSnippet"),
        "sectionInputCount": widget.get("sectionInputCount"),
        "sectionInputs": widget.get("sectionInputs"),
        "topInputs": (widget.get("allInputs") or [])[:8],
    }

    if not (widget.get("allInputs") or widget.get("sectionInputs")):
        report["main"]["error"] = (
            "No file inputs found on the product page. "
            "Scroll to the Images / სურათები field and try again."
        )
        return report

    multi_t = _pick_multi_target(widget)
    classic_main = _pick_classic_main(widget)

    # Prefer single multi-batch whenever we have a section widget or multi input
    use_multi_batch = bool(
        multi_t
        and (
            multi_t.get("multiple")
            or widget.get("sectionFound")
            or multi_t.get("score", 0) >= 30
            or not classic_main
        )
    )

    batch_attempted = False
    batch_detail: dict[str, Any] = {}

    def _wait_verify(prev: dict[str, Any], *, want: int) -> dict[str, Any]:
        time.sleep(1.0)
        v = _verify(driver)
        if not _ui_gained(prev, v, min_gain=1):
            time.sleep(2.0)
            v = _verify(driver)
        return v

    # --- Mode A: ONE multi_batch of all selected files ---
    if use_multi_batch and multi_t:
        report["mode"] = "multi_batch"
        batch_attempted = True
        try:
            batch_detail = _set_files_on_index(driver, int(multi_t["index"]), ordered)
        except Exception as exc:
            report["main"]["error"] = str(exc)
            report["mode"] = "multi_batch_failed"
            batch_detail = {"ok": False, "set_attempted": False, "error": str(exc)}

        verify_after = _wait_verify(baseline, want=len(ordered))
        report["verify"] = verify_after

        # Success if input accepted files OR UI gained previews (AJAX may clear input)
        attached = bool(batch_detail.get("ok") or batch_detail.get("fileCount")) or _ui_gained(
            baseline, verify_after, min_gain=1
        )
        if attached:
            _mark_batch_success(
                report, n_files=len(ordered), detail=batch_detail or {}, multi_t=multi_t
            )
            report["note"] += (
                f" Single batch of {len(ordered)} file(s); no classic fallback "
                f"(avoids duplicates)."
            )
            report["ok_count"] = len(ordered)
            if verify_after.get("looksAttached") or _ui_gained(baseline, verify_after):
                report["note"] += (
                    f" UI: previews={verify_after.get('previewCount')}, "
                    f"files={verify_after.get('filesOnInputs')}."
                )
            report.pop("_extra_paths", None)
            return report

        # Batch ran but nothing visible — do NOT re-upload same paths via classic if
        # the set call definitely hit a multi widget (risk of silent server-side queue).
        if batch_detail.get("set_attempted"):
            report["mode"] = "multi_batch_no_ui"
            report["main"]["error"] = (
                "Tried multi-batch once; form UI did not show new previews. "
                "Not retrying classic mode (that was creating duplicate uploads). "
                f"Input: {batch_detail.get('name') or multi_t.get('name')}. "
                "Remove any accidental images in CS-Cart, then retry with Images visible."
            )
            report["main"]["input_name"] = batch_detail.get("name") or multi_t.get("name")
            report["main"]["method"] = (batch_detail.get("set") or {}).get("method")
            report["ok_count"] = 0
            report.pop("_extra_paths", None)
            return report

        # Multi chosen but set never started — fall through to classic once
        report["mode"] = "multi_batch_failed+classic"

    # --- Mode B: classic main + additional (only if multi never ran) ---
    report["mode"] = "classic"
    report["additional"] = []
    used: set[int] = set()
    main_t = classic_main or multi_t

    if main_path and main_t:
        try:
            detail = _set_files_on_index(driver, int(main_t["index"]), [_abs_paths([main_path])[0]])
            used.add(int(main_t["index"]))
            report["main"]["ok"] = bool(detail.get("ok") or detail.get("fileCount"))
            report["main"]["input_index"] = main_t["index"]
            report["main"]["input_name"] = detail.get("name") or main_t.get("name")
            report["main"]["input_id"] = detail.get("id") or main_t.get("id")
            report["main"]["fileName"] = (detail.get("fileNames") or [None])[0]
            report["main"]["method"] = (detail.get("set") or {}).get("method")
            if not report["main"]["ok"]:
                report["main"]["error"] = "Main file input empty after set"
        except Exception as exc:
            report["main"]["error"] = str(exc)

    # Additional: each path once on a dedicated slot — never re-hit multi with same set
    for path in extra:
        item: dict[str, Any] = {"path": path, "ok": False, "error": ""}
        try:
            widget = discover_widget(driver)
            pick = _pick_classic_additional(widget, used)
            if not pick:
                item["error"] = (
                    "No free additional-image input. "
                    "Use multi drop-zone (one batch) or expand Additional images."
                )
            else:
                detail = _set_files_on_index(driver, int(pick["index"]), [_abs_paths([path])[0]])
                used.add(int(pick["index"]))
                item["ok"] = bool(detail.get("ok") or detail.get("fileCount"))
                item["input_name"] = detail.get("name") or pick.get("name")
                item["input_id"] = detail.get("id") or pick.get("id")
                item["fileName"] = (detail.get("fileNames") or [None])[0]
                if not item["ok"]:
                    item["error"] = "Additional file input empty after set"
        except Exception as exc:
            item["error"] = str(exc)
        report["additional"].append(item)
        time.sleep(0.35)

    time.sleep(1.0)
    verify = _verify(driver)
    if not verify.get("looksAttached"):
        time.sleep(1.5)
        verify = _verify(driver)
    report["verify"] = verify

    if report["main"].get("ok") and not (
        verify.get("looksAttached") or _ui_gained(baseline, verify, min_gain=1)
    ):
        # Input said ok but no UI change — still keep ok if files still on inputs
        if int(verify.get("filesOnInputs") or 0) < 1:
            report["main"]["ok"] = False
            report["main"]["error"] = (
                "File set but Images UI still empty. "
                f"Input was {report['main'].get('input_name') or '(unknown)'}."
            )

    report["ok_count"] = (1 if report["main"].get("ok") else 0) + sum(
        1 for a in report["additional"] if a.get("ok")
    )
    if verify.get("looksAttached"):
        report["note"] += (
            f" UI check: {verify.get('filesOnInputs', 0)} file(s) on inputs, "
            f"{verify.get('previewCount', 0)} preview element(s)."
        )

    # Drop internal field from response surface
    report.pop("_extra_paths", None)
    return report

